"""A pasta de trabalho: add, status, árvores a partir do índice e checkout."""

import os
import stat

from . import dates
from .ignore import IgnoreMatcher
from .index import IndexEntry, mode_for_stat
from .objects import (TREE_MODE, Commit, TreeEntry, cleanup_message, encode_path, format_ident,
                      hash_object, serialize_commit, serialize_tree)
from .repo import GitError


def abs_path(repo, rel):
    return os.path.join(repo.worktree, *rel.split("/"))


def path_key(path):
    """Ordem do Git: bytes do caminho."""
    return encode_path(path)


def read_file_data(path, st):
    """Conteúdo que vira blob: o arquivo, ou o destino de um link simbólico."""
    if stat.S_ISLNK(st.st_mode):
        return os.fsencode(os.readlink(path))
    with open(path, "rb") as f:
        return f.read()


def matcher_for(repo):
    return IgnoreMatcher(repo.worktree, repo.git_dir, repo.config())


def walk_files(repo, matcher, rel_dir=""):
    """Arquivos (e links) não ignorados abaixo de rel_dir, em ordem."""
    base = abs_path(repo, rel_dir) if rel_dir else repo.worktree
    try:
        names = sorted(os.listdir(base), key=lambda n: encode_path(n))
    except (FileNotFoundError, NotADirectoryError):
        return
    matcher.load_dir(rel_dir)
    for name in names:
        if name == ".git":
            continue
        rel = rel_dir + "/" + name if rel_dir else name
        st = os.lstat(os.path.join(base, name))
        is_dir = stat.S_ISDIR(st.st_mode)
        if matcher.is_ignored(rel, is_dir):
            continue
        if is_dir:
            yield from walk_files(repo, matcher, rel)
        elif stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            yield rel


# ---------------------------------------------------------------- árvores

def build_tree(repo, entries):
    """Grava as árvores (uma por pasta) a partir das entradas do índice."""
    root = {}
    for e in entries:
        parts = e.path.split("/")
        node = root
        for folder in parts[:-1]:
            node = node.setdefault(folder, {})
            if not isinstance(node, dict):
                raise GitError("o índice tem um arquivo e uma pasta com o mesmo nome: %s" % e.path)
        node[parts[-1]] = (e.mode_text, e.sha)

    def write(node):
        items = []
        for name, value in node.items():
            if isinstance(value, dict):
                items.append(TreeEntry(TREE_MODE, name, write(value)))
            else:
                items.append(TreeEntry(value[0], name, value[1]))
        return repo.objects.write("tree", serialize_tree(items))

    return write(root)


def flatten_tree(repo, tree_sha, prefix=""):
    """{caminho: (modo, sha)} com todos os arquivos de uma árvore."""
    out = {}
    for e in repo.read_tree(tree_sha):
        path = prefix + e.name
        if e.is_tree:
            out.update(flatten_tree(repo, e.sha, path + "/"))
        else:
            out[path] = (e.mode, e.sha)
    return out


def head_files(repo):
    _, sha = repo.head()
    return flatten_tree(repo, repo.read_commit(sha).tree) if sha else {}


# ------------------------------------------------------------------- add

def to_repo_path(repo, path):
    """Caminho digitado pelo usuário (relativo à pasta atual) para o caminho no repositório."""
    full = os.path.abspath(path)
    rel = os.path.relpath(full, repo.worktree)
    if rel == os.curdir:
        return ""
    if rel.startswith(os.pardir + os.sep) or rel == os.pardir:
        raise GitError("%s está fora do repositório" % path)
    rel = rel.replace(os.sep, "/")
    if rel == ".git" or rel.startswith(".git/"):
        raise GitError("não dá para adicionar arquivos de dentro de .git")
    return rel


def stage_entry(repo, rel):
    path = abs_path(repo, rel)
    st = os.lstat(path)
    sha = repo.objects.write("blob", read_file_data(path, st))
    return IndexEntry.from_stat(rel, sha, mode_for_stat(st), st)


def add(repo, pathspecs, force=False):
    """Põe os arquivos no índice (e registra os que foram apagados). Devolve a lista de mudanças."""
    matcher = matcher_for(repo)
    index = {e.path: e for e in repo.read_index() if e.stage == 0}
    to_add, to_remove = set(), set()
    for spec in pathspecs:
        rel = to_repo_path(repo, spec)
        path = abs_path(repo, rel) if rel else repo.worktree
        inside = (lambda p, r=rel: True) if rel == "" else (lambda p, r=rel: p == r or p.startswith(r + "/"))
        if os.path.isdir(path) and not os.path.islink(path):
            to_add.update(walk_files(repo, matcher, rel))
            to_remove.update(p for p in index if inside(p) and not os.path.lexists(abs_path(repo, p)))
        elif os.path.lexists(path):
            if rel not in index and not force and matcher.is_ignored_path(rel, False):
                raise GitError("%s é ignorado por um .gitignore (use -f para adicionar mesmo assim)" % spec)
            to_add.add(rel)
        else:
            gone = [p for p in index if inside(p)]
            if not gone:
                raise GitError("o caminho '%s' não corresponde a nenhum arquivo" % spec)
            to_remove.update(gone)

    changes = []
    for rel in sorted(to_add, key=path_key):
        entry = stage_entry(repo, rel)
        old = index.get(rel)
        # Um arquivo novo em a/b tira do índice um antigo arquivo "a" (e vice-versa).
        parts = rel.split("/")
        for i in range(1, len(parts)):
            index.pop("/".join(parts[:i]), None)
        for p in [p for p in index if p.startswith(rel + "/")]:
            del index[p]
        if old is None or old.sha != entry.sha or old.mode != entry.mode:
            changes.append(("adicionado" if old is None else "atualizado", rel))
        index[rel] = entry
    for rel in sorted(to_remove, key=path_key):
        if index.pop(rel, None) is not None:
            changes.append(("removido", rel))
    repo.write_index(list(index.values()))
    return changes


# ---------------------------------------------------------------- status

class Status:
    def __init__(self):
        self.staged = {}       # caminho -> A, M, D ou T (índice comparado ao HEAD)
        self.unstaged = {}     # caminho -> M, D ou T (pasta de trabalho comparada ao índice)
        self.untracked = []    # caminhos; pastas inteiras terminam com "/"

    @property
    def clean(self):
        return not self.staged and not self.unstaged

    def porcelain(self):
        """As linhas do ``git status --porcelain`` (formato v1)."""
        from .quoting import quote_path
        lines = []
        for path in sorted(set(self.staged) | set(self.unstaged), key=path_key):
            lines.append("%s%s %s" % (self.staged.get(path, " "), self.unstaged.get(path, " "),
                                      quote_path(path, quote_space=True)))
        lines += ["?? " + quote_path(p, quote_space=True) for p in self.untracked]
        return lines


def _kind(mode_text):
    return "link" if mode_text == "120000" else "commit" if mode_text == "160000" else "file"


def compute_status(repo, refresh=True):
    st_result = Status()
    entries = [e for e in repo.read_index() if e.stage == 0]
    index = {e.path: e for e in entries}
    head = head_files(repo)

    # Índice contra o HEAD: o que vai entrar no próximo commit.
    for path in set(head) | set(index):
        h, i = head.get(path), index.get(path)
        if h is None:
            st_result.staged[path] = "A"
        elif i is None:
            st_result.staged[path] = "D"
        elif h[1] != i.sha or h[0] != i.mode_text:
            st_result.staged[path] = "T" if _kind(h[0]) != _kind(i.mode_text) else "M"

    # Pasta de trabalho contra o índice.
    try:
        index_mtime = os.stat(repo.index_path).st_mtime_ns
    except FileNotFoundError:
        index_mtime = 0
    refreshed = False
    for e in entries:
        path = abs_path(repo, e.path)
        try:
            st = os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            st_result.unstaged[e.path] = "D"
            continue
        if stat.S_ISDIR(st.st_mode):
            st_result.unstaged[e.path] = "D"
            continue
        mode = mode_for_stat(st)
        if _kind("%o" % mode) != _kind(e.mode_text):
            st_result.unstaged[e.path] = "T"
            continue
        entry_mtime = e.mtime_s * 1000000000 + e.mtime_ns
        racy = entry_mtime >= index_mtime
        if mode == e.mode and e.stat_matches(st) and not racy:
            continue                      # dados de stat iguais: nem precisa ler
        if hash_object(read_file_data(path, st)) != e.sha:
            st_result.unstaged[e.path] = "M"
        elif mode != e.mode:
            st_result.unstaged[e.path] = "M"
        elif not e.stat_matches(st):
            # Mesmo conteúdo, stat diferente: atualiza o índice, como o Git faz.
            index[e.path] = IndexEntry.from_stat(e.path, e.sha, e.mode, st)
            refreshed = True
    if refreshed and refresh:
        try:
            repo.write_index(list(index.values()))
        except Exception:
            pass   # outro processo usando o índice: atualizar é só uma otimização

    st_result.untracked = untracked_files(repo, set(index))
    return st_result


def untracked_files(repo, tracked):
    """Arquivos fora do índice. Uma pasta sem nada rastreado aparece inteira, como "pasta/"."""
    tracked_dirs = set()
    for path in tracked:
        parts = path.split("/")
        for i in range(1, len(parts)):
            tracked_dirs.add("/".join(parts[:i]))
    matcher = matcher_for(repo)
    out = []

    def has_files(rel_dir):
        for _ in walk_files(repo, matcher, rel_dir):
            return True
        return False

    def visit(rel_dir):
        base = abs_path(repo, rel_dir) if rel_dir else repo.worktree
        matcher.load_dir(rel_dir)
        for name in sorted(os.listdir(base), key=lambda n: encode_path(n)):
            if name == ".git":
                continue
            rel = rel_dir + "/" + name if rel_dir else name
            st = os.lstat(os.path.join(base, name))
            is_dir = stat.S_ISDIR(st.st_mode)
            if matcher.is_ignored(rel, is_dir):
                continue
            if is_dir:
                if rel in tracked_dirs:
                    visit(rel)
                elif has_files(rel):
                    out.append(rel + "/")
            elif rel not in tracked and (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                out.append(rel)

    visit("")
    return out


# ---------------------------------------------------------------- commit

def identity(repo, role):
    """Nome, email e data de quem escreveu (AUTHOR) ou registrou (COMMITTER) o commit."""
    config = repo.config()
    name = os.environ.get("GIT_%s_NAME" % role) or config.get("%s.name" % role.lower()) \
        or config.get("user.name")
    email = os.environ.get("GIT_%s_EMAIL" % role) or config.get("%s.email" % role.lower()) \
        or config.get("user.email") or os.environ.get("EMAIL")
    if not name or not email:
        raise GitError("diga quem você é antes do commit:\n"
                       "  mini-git config user.name \"Seu Nome\"\n"
                       "  mini-git config user.email \"voce@exemplo.com\"\n"
                       "(ou use as variáveis GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL e as de COMMITTER)")
    raw_date = os.environ.get("GIT_%s_DATE" % role)
    try:
        ts, tz = dates.parse_git_date(raw_date) if raw_date else dates.now()
    except ValueError as exc:
        raise GitError("GIT_%s_DATE: %s" % (role, exc)) from None
    return format_ident(name, email, ts, dates.format_tz(tz))


def commit(repo, message, allow_empty=False):
    entries = repo.read_index()
    if any(e.stage != 0 for e in entries):
        raise GitError("há conflitos não resolvidos no índice")
    message = cleanup_message(message)
    if not message:
        raise GitError("mensagem de commit vazia: commit cancelado")
    tree = build_tree(repo, entries)
    ref = head_ref(repo)
    _, parent = repo.head()
    if not allow_empty:
        if parent and repo.read_commit(parent).tree == tree:
            raise GitError("nada para commitar: o índice é igual ao último commit")
        if not parent and not entries:
            raise GitError("nada para commitar: o índice está vazio (use mini-git add)")
    c = Commit(tree, [parent] if parent else [], identity(repo, "AUTHOR"),
               identity(repo, "COMMITTER"), message)
    sha = repo.objects.write("commit", serialize_commit(c))
    if ref:
        repo.update_ref(ref, sha)
    else:
        repo.set_head_detached(sha)
    return sha, parent is None


def head_ref(repo):
    with open(os.path.join(repo.git_dir, "HEAD"), encoding="utf-8") as f:
        content = f.read().strip()
    return content[5:] if content.startswith("ref: ") else None


# --------------------------------------------------------------- checkout

def _write_worktree_file(repo, rel, mode, sha):
    path = abs_path(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    obj_type, data = repo.read_object(sha)
    if os.path.lexists(path):
        if os.path.isdir(path) and not os.path.islink(path):
            raise GitError("%s é uma pasta na pasta de trabalho" % rel)
        os.unlink(path)
    if mode == "120000":
        os.symlink(os.fsdecode(data), path)
        return
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, 0o755 if mode == "100755" else 0o644)


def _remove_empty_dirs(repo, rel):
    parts = rel.split("/")[:-1]
    while parts:
        path = abs_path(repo, "/".join(parts))
        try:
            os.rmdir(path)
        except OSError:
            return
        parts.pop()


def checkout(repo, target, branch=None):
    """Troca a pasta de trabalho e o índice para o commit target. Recusa se houver mudanças locais."""
    status = compute_status(repo)
    if not status.clean:
        changed = sorted(set(status.staged) | set(status.unstaged), key=path_key)
        raise GitError("você tem mudanças locais que seriam perdidas no checkout:\n" +
                       "\n".join("  " + p for p in changed[:20]) +
                       "\nfaça um commit delas (ou desfaça) antes de trocar")
    current = head_files(repo)
    wanted = flatten_tree(repo, repo.read_commit(target).tree)

    # Arquivos não rastreados que seriam sobrescritos.
    blocked = []
    for path in wanted:
        if path in current:
            continue
        parts = path.split("/")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            full = abs_path(repo, prefix)
            if prefix in current or not os.path.lexists(full):
                continue
            if i == len(parts) or not os.path.isdir(full) or os.path.islink(full):
                blocked.append(prefix)
                break
    if blocked:
        raise GitError("arquivos não rastreados seriam sobrescritos pelo checkout:\n" +
                       "\n".join("  " + p for p in sorted(set(blocked), key=path_key)[:20]) +
                       "\nmova ou apague esses arquivos antes")

    for path in sorted(set(current) - set(wanted), key=path_key, reverse=True):
        full = abs_path(repo, path)
        if os.path.lexists(full):
            os.unlink(full)
        _remove_empty_dirs(repo, path)
    for path, (mode, sha) in sorted(wanted.items(), key=lambda kv: path_key(kv[0])):
        if current.get(path) != (mode, sha) or not os.path.lexists(abs_path(repo, path)):
            _write_worktree_file(repo, path, mode, sha)

    entries = []
    for path, (mode, sha) in wanted.items():
        st = os.lstat(abs_path(repo, path))
        entries.append(IndexEntry.from_stat(path, sha, int(mode, 8), st))
    repo.write_index(entries)
    if branch:
        repo.set_head_branch(branch)
    else:
        repo.set_head_detached(target)
