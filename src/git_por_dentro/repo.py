"""O repositório: a pasta .git, as configurações, as referências e o HEAD.

    .git/
      HEAD              "ref: refs/heads/main" (ou um SHA-1, no HEAD destacado)
      config            configurações do repositório
      index             a área de preparação
      objects/          os objetos (blobs, trees, commits)
      refs/heads/main   o SHA-1 do último commit do ramo main
      refs/tags/...     etiquetas
"""

import os
import re
import sys

from .index import read_index, write_index
from .objects import ObjectError, ObjectStore, parse_commit, parse_tree


class GitError(Exception):
    """Erro para mostrar ao usuário (mensagem em português)."""


DEFAULT_CONFIG = """[core]
\trepositoryformatversion = 0
\tfilemode = true
\tbare = false
\tlogallrefupdates = true
"""


# ---------------------------------------------------------------- config

def _unquote(value):
    """Valor de configuração: tira comentários fora de aspas e trata escapes."""
    out, quoted, i = [], False, 0
    while i < len(value):
        c = value[i]
        if c == '"':
            quoted = not quoted
        elif c == "\\" and i + 1 < len(value):
            i += 1
            out.append({"n": "\n", "t": "\t", "b": "\b"}.get(value[i], value[i]))
        elif c in "#;" and not quoted:
            break
        else:
            out.append(c)
        i += 1
    return "".join(out).strip()


def parse_config(text):
    """Lê o formato de configuração do Git. Devolve {"secao.subsecao.chave": valor}."""
    values = {}
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        m = re.match(r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\]\s*(.*)$', line)
        if m:
            section = m.group(1).lower()
            if m.group(2) is not None:
                section += "." + re.sub(r"\\(.)", r"\1", m.group(2))
            line = m.group(3).strip()
            if not line or line[0] in "#;":
                continue
        if section is None:
            continue
        key, eq, value = line.partition("=")
        key = key.strip().lower()
        if not re.match(r"^[a-z][a-z0-9-]*$", key):
            continue
        values[section + "." + key] = _unquote(value) if eq else "true"
    return values


def global_config_paths():
    """Arquivos globais: os mesmos lugares em que o Git procura."""
    paths = []
    explicit = os.environ.get("GIT_CONFIG_GLOBAL")
    if explicit:
        return [explicit]
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    paths.append(os.path.join(xdg, "git", "config"))
    paths.append(os.path.join(os.path.expanduser("~"), ".gitconfig"))
    return paths


def read_config_files(paths):
    values = {}
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                values.update(parse_config(f.read()))
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            pass
    return values


def set_config_value(path, key, value):
    """Grava "secao.chave = valor" no arquivo, trocando a linha se ela já existir."""
    section, _, name = key.rpartition(".")
    if not section or not re.match(r"^[A-Za-z][A-Za-z0-9-]*$", name) or "." in section:
        raise GitError("chave de configuração inválida: %s (use secao.chave, como user.name)" % key)
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    if escaped != escaped.strip() or any(c in escaped for c in "#;"):
        escaped = '"%s"' % escaped
    new_line = "\t%s = %s" % (name, escaped)
    current, insert_at = None, None
    for i, line in enumerate(lines):
        m = re.match(r'^\s*\[\s*([A-Za-z0-9.-]+)(?:\s+"([^"]*)")?\s*\]', line)
        if m:
            current = m.group(1).lower() + ("." + m.group(2) if m.group(2) is not None else "")
            if current == section.lower():
                insert_at = i + 1
            continue
        if current == section.lower():
            k = line.split("=", 1)[0].strip().lower()
            if k == name.lower():
                lines[i] = new_line
                break
            insert_at = i + 1
    else:
        if insert_at is None:
            lines += ["[%s]" % section.lower(), new_line]
        else:
            lines.insert(insert_at, new_line)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------- repositório

_REF_NAME = re.compile(r"^(?![-/])(?!.*\.\.)(?!.*//)(?!.*@\{)(?!.*\.lock$)(?!.*/$)(?!.*\.$)"
                       r"[^\x00-\x20~^:?*\[\\\x7f]+$")


def valid_branch_name(name):
    """Regras do ``git check-ref-format`` mais comuns, para nomes de ramos."""
    return bool(_REF_NAME.match(name)) and name != "@" and not any(
        part.startswith(".") for part in name.split("/"))


class Repository:
    def __init__(self, worktree):
        self.worktree = os.path.abspath(worktree)
        self.git_dir = os.path.join(self.worktree, ".git")
        self.objects = ObjectStore(self.git_dir)

    # -------------------------------------------------------- criação

    @classmethod
    def find(cls, start="."):
        """Procura a pasta .git a partir de start, subindo até a raiz."""
        path = os.path.abspath(start)
        while True:
            if os.path.isdir(os.path.join(path, ".git")):
                return cls(path)
            parent = os.path.dirname(path)
            if parent == path:
                raise GitError("não é um repositório git (nem nenhuma pasta acima): %s"
                               % os.path.abspath(start))
            path = parent

    @classmethod
    def init(cls, path, branch=None):
        repo = cls(path)
        if os.path.exists(os.path.join(repo.git_dir, "HEAD")):
            return repo, False
        if branch is None:
            branch = read_config_files(global_config_paths()).get("init.defaultbranch", "main")
        if not valid_branch_name(branch):
            raise GitError("nome de ramo inválido: %s" % branch)
        for sub in ("objects/info", "objects/pack", "refs/heads", "refs/tags", "info"):
            os.makedirs(os.path.join(repo.git_dir, sub), exist_ok=True)
        config = DEFAULT_CONFIG
        if sys.platform == "darwin":
            config += "\tignorecase = true\n\tprecomposeunicode = true\n"
        repo._write(os.path.join(repo.git_dir, "config"), config)
        repo._write(os.path.join(repo.git_dir, "description"),
                    "Unnamed repository; edit this file 'description' to name the repository.\n")
        repo._write(os.path.join(repo.git_dir, "HEAD"), "ref: refs/heads/%s\n" % branch)
        return repo, True

    @staticmethod
    def _write(path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    # ----------------------------------------------------------- config

    def config(self):
        values = read_config_files(global_config_paths())
        values.update(read_config_files([os.path.join(self.git_dir, "config")]))
        return values

    # ------------------------------------------------------------ índice

    @property
    def index_path(self):
        return os.path.join(self.git_dir, "index")

    def read_index(self):
        return read_index(self.index_path)

    def write_index(self, entries):
        write_index(self.index_path, entries)

    # ------------------------------------------------------------- refs

    def _packed_refs(self):
        refs = {}
        try:
            with open(os.path.join(self.git_dir, "packed-refs"), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and line[0] not in "#^":
                        sha, _, name = line.partition(" ")
                        refs[name] = sha
        except FileNotFoundError:
            pass
        return refs

    def read_ref(self, name):
        """SHA-1 de uma referência (refs/heads/main, ...), ou None se não existe."""
        path = os.path.join(self.git_dir, *name.split("/"))
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                content = f.read().strip()
            if content.startswith("ref: "):
                return self.read_ref(content[5:])
            return content
        return self._packed_refs().get(name)

    def update_ref(self, name, sha):
        path = os.path.join(self.git_dir, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".lock"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(sha + "\n")
        os.replace(tmp, path)

    def delete_ref(self, name):
        path = os.path.join(self.git_dir, *name.split("/"))
        if os.path.isfile(path):
            os.unlink(path)
        packed = self._packed_refs()
        if name in packed:
            raise GitError("a referência %s está em packed-refs; apague-a com o git" % name)

    def head(self):
        """Devolve (ramo, sha). ramo é None no HEAD destacado; sha é None num ramo sem commits."""
        with open(os.path.join(self.git_dir, "HEAD"), encoding="utf-8") as f:
            content = f.read().strip()
        if content.startswith("ref: "):
            ref = content[5:]
            branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
            return branch, self.read_ref(ref)
        return None, content

    def set_head_branch(self, branch):
        self._write(os.path.join(self.git_dir, "HEAD"), "ref: refs/heads/%s\n" % branch)

    def set_head_detached(self, sha):
        self._write(os.path.join(self.git_dir, "HEAD"), sha + "\n")

    def branches(self):
        names = set(self._packed_refs_names("refs/heads/"))
        root = os.path.join(self.git_dir, "refs", "heads")
        for folder, _, files in os.walk(root):
            for name in files:
                if not name.endswith(".lock"):
                    full = os.path.join(folder, name)
                    names.add(os.path.relpath(full, root).replace(os.sep, "/"))
        return sorted(names)

    def _packed_refs_names(self, prefix):
        return [name[len(prefix):] for name in self._packed_refs() if name.startswith(prefix)]

    # ---------------------------------------------------------- objetos

    def read_object(self, sha):
        try:
            return self.objects.read(sha)
        except ObjectError as exc:
            raise GitError(str(exc)) from None

    def read_commit(self, sha):
        obj_type, data = self.read_object(sha)
        if obj_type != "commit":
            raise GitError("%s não é um commit (é um %s)" % (sha, obj_type))
        return parse_commit(data)

    def read_tree(self, sha):
        obj_type, data = self.read_object(sha)
        if obj_type == "commit":
            return self.read_tree(parse_commit(data).tree)
        if obj_type != "tree":
            raise GitError("%s não é uma árvore (é um %s)" % (sha, obj_type))
        return parse_tree(data)

    # --------------------------------------------------------- revisões

    def resolve(self, rev):
        """Nome de revisão para SHA-1: HEAD, ramos, etiquetas, SHA-1 abreviado,
        e os sufixos ~N, ^ e ^{tree}."""
        m = re.match(r"^(.*?)((?:~\d*|\^(?:\{tree\}|\{commit\}|1)?)*)$", rev)
        base, suffix = m.group(1), m.group(2)
        sha = self._resolve_base(base or rev)
        for op in re.findall(r"~\d*|\^\{tree\}|\^\{commit\}|\^1?", suffix):
            if op == "^{tree}":
                obj_type, data = self.read_object(sha)
                sha = parse_commit(data).tree if obj_type == "commit" else sha
            elif op == "^{commit}":
                self.read_commit(sha)
            else:
                steps = 1 if op in ("^", "^1", "~") else int(op[1:])
                for _ in range(steps):
                    parents = self.read_commit(sha).parents
                    if not parents:
                        raise GitError("%s não tem tantos ancestrais" % rev)
                    sha = parents[0]
        return sha

    def _resolve_base(self, name):
        if name == "HEAD":
            branch, sha = self.head()
            if sha is None:
                raise GitError("o ramo %s ainda não tem commits" % branch)
            return sha
        # A mesma ordem do Git: .git/<nome>, refs/<nome>, etiquetas, ramos e remotos.
        if name and ".." not in name and not name.startswith("/") and "\0" not in name:
            for ref in (name, "refs/" + name, "refs/tags/" + name, "refs/heads/" + name,
                        "refs/remotes/" + name):
                sha = self.read_ref(ref)
                if sha and re.match(r"^[0-9a-f]{40}$", sha):
                    return sha
        if re.match(r"^[0-9a-fA-F]{4,40}$", name):
            found = self.objects.find_prefix(name)
            if len(found) == 1:
                return found[0]
            if len(found) > 1:
                raise GitError("o SHA-1 abreviado %s é ambíguo" % name)
        raise GitError("revisão desconhecida: %s" % name)
