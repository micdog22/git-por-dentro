"""mini-git: a linha de comando."""

import argparse
import os
import sys

from . import __version__, dates
from .diffs import file_diff
from .objects import OBJECT_TYPES, encode_path, parse_commit, parse_ident, parse_tree
from .quoting import quote_path
from .repo import GitError, Repository, global_config_paths, set_config_value, valid_branch_name
from .index import IndexFileError
from .worktree import (abs_path, add, build_tree, checkout, commit, compute_status,
                       flatten_tree, head_files, path_key, read_file_data, to_repo_path)

PROG = "mini-git"

_TRANSLATIONS = (
    ("the following arguments are required", "faltam argumentos obrigatórios"),
    ("unrecognized arguments", "argumentos não reconhecidos"),
    ("invalid choice", "opção inválida"),
    ("expected one argument", "esperava um valor"),
    ("not allowed with argument", "não pode ser usado junto com"),
    ("one of the arguments", "um dos argumentos"),
    ("is required", "é obrigatório"),
    ("invalid int value", "número inválido"),
    ("choose from", "escolha entre"),
    ("argument ", "argumento "),
)


class PtFormatter(argparse.RawDescriptionHelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):
        # prefix "" é usado pelo argparse para montar o nome dos subcomandos.
        return super().add_usage(usage, actions, groups, prefix="uso: " if prefix is None else prefix)


class PtParser(argparse.ArgumentParser):
    """ArgumentParser com mensagens em português."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("formatter_class", PtFormatter)
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)
        self._positionals.title = "argumentos"
        self._optionals.title = "opções"
        self.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS,
                          help="mostra esta ajuda e sai")

    def error(self, message):
        for en, pt in _TRANSLATIONS:
            message = message.replace(en, pt)
        self.print_usage(sys.stderr)
        self.exit(2, "%s: erro: %s\n" % (self.prog, message))


def out(text=""):
    sys.stdout.write(text + "\n")


def write_bytes(data):
    """Saída binária (conteúdo de blobs e diffs), sem passar por conversão de texto."""
    buf = getattr(sys.stdout, "buffer", None)
    if buf is None:
        sys.stdout.write(data.decode("utf-8", "surrogateescape"))
        return
    sys.stdout.flush()
    buf.write(data)
    buf.flush()


def repo_here():
    return Repository.find(".")


# ------------------------------------------------------------------ comandos

def cmd_init(args):
    path = args.pasta or "."
    os.makedirs(path, exist_ok=True)
    repo, created = Repository.init(path, branch=args.initial_branch)
    where = os.path.join(repo.git_dir, "")
    out(("Repositório Git vazio criado em %s" if created else
         "Já existe um repositório em %s (nada mudou)") % where)
    return 0


def cmd_hash_object(args):
    if args.type not in OBJECT_TYPES:
        raise GitError("tipo de objeto inválido: %s" % args.type)
    sources = []
    if args.stdin:
        sources.append(sys.stdin.buffer.read())
    for name in args.arquivos:
        try:
            with open(name, "rb") as f:
                sources.append(f.read())
        except OSError as exc:
            raise GitError("não foi possível ler %s: %s" % (name, exc.strerror)) from None
    if not sources:
        raise GitError("informe um arquivo ou use --stdin")
    repo = repo_here() if args.write else None
    from .objects import hash_object
    for data in sources:
        out(repo.objects.write(args.type, data) if repo else hash_object(data, args.type))
    return 0


def iter_tree(repo, tree_sha, prefix="", recursive=False):
    """(caminho, entrada) de uma árvore; com recursive, desce nas subpastas."""
    for e in repo.read_tree(tree_sha):
        path = prefix + e.name
        if recursive and e.is_tree:
            yield from iter_tree(repo, e.sha, path + "/", True)
        else:
            yield path, e


def tree_line(path, e, name_only=False):
    if name_only:
        return quote_path(path)
    return "%06d %s %s\t%s" % (int(e.mode), e.type, e.sha, quote_path(path))


def cmd_cat_file(args):
    repo = repo_here()
    sha = repo.resolve(args.objeto)
    obj_type, data = repo.read_object(sha)
    if args.t:
        out(obj_type)
    elif args.s:
        out(str(len(data)))
    elif obj_type == "tree":
        for e in parse_tree(data):
            out(tree_line(e.name, e))
    else:
        write_bytes(data)
    return 0


def cmd_add(args):
    repo = repo_here()
    changes = add(repo, args.caminhos, force=args.force)
    if args.verbose:
        for kind, path in changes:
            out("%s: %s" % (kind, path))
    return 0


def cmd_write_tree(args):
    repo = repo_here()
    out(build_tree(repo, [e for e in repo.read_index() if e.stage == 0]))
    return 0


def cmd_commit(args):
    repo = repo_here()
    message = "\n\n".join(args.message)
    sha, root = commit(repo, message, allow_empty=args.allow_empty)
    branch, _ = repo.head()
    where = branch if branch else "HEAD destacado"
    subject = message.strip().split("\n", 1)[0]
    out("[%s%s %s] %s" % (where, " (commit raiz)" if root else "", sha[:7], subject))
    return 0


def subject_of(message):
    """Assunto, como no Git: o primeiro parágrafo numa linha só."""
    lines = message.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    para = []
    for line in lines:
        if not line.strip():
            break
        para.append(line.rstrip())
    return " ".join(para)


def cmd_log(args):
    repo = repo_here()
    sha = repo.resolve(args.revisao)
    blocks, count = [], 0
    while sha and (args.n is None or count < args.n):
        c = repo.read_commit(sha)
        if args.oneline:
            blocks.append("%s %s" % (sha[:7], subject_of(c.message)))
        else:
            name, email, ts, tz = parse_ident(c.author)
            lines = ["commit %s" % sha]
            if len(c.parents) > 1:
                lines.append("Merge: " + " ".join(p[:7] for p in c.parents))
            lines += ["Author: %s <%s>" % (name, email),
                      "Date:   %s" % dates.format_default(ts, dates.parse_tz(tz)), ""]
            body = c.message.split("\n")
            if body and body[-1] == "":
                body.pop()
            while body and not body[0].strip():
                body.pop(0)
            lines += ["    " + (line if line.strip() else "") for line in body]
            blocks.append("\n".join(lines))
        count += 1
        sha = c.parents[0] if c.parents else None   # só o primeiro pai
    if blocks:
        out(("\n" if args.oneline else "\n\n").join(blocks))
    return 0


def cmd_status(args):
    repo = repo_here()
    st = compute_status(repo)
    if args.porcelain:
        for line in st.porcelain():
            out(line)
        return 0
    branch, sha = repo.head()
    if branch:
        out("No ramo %s" % branch)
    else:
        out("HEAD destacado em %s" % sha[:7])
    if sha is None:
        out("\nNenhum commit ainda")
    labels = {"A": "novo:", "M": "modificado:", "D": "removido:", "T": "tipo mudou:"}
    sections = (("Mudanças prontas para o commit:", st.staged),
                ("Mudanças fora do índice (use mini-git add):", st.unstaged))
    for title, changes in sections:
        if changes:
            out("\n" + title)
            for path in sorted(changes, key=path_key):
                out("\t%-13s%s" % (labels[changes[path]], quote_path(path)))
    if st.untracked:
        out("\nArquivos não rastreados (use mini-git add):")
        for path in st.untracked:
            out("\t" + quote_path(path))
    if st.clean and not st.untracked:
        out("\nnada para commitar, a pasta de trabalho está limpa")
    elif st.clean:
        out("\nnada no índice para commitar, mas há arquivos não rastreados")
    return 0


def cmd_checkout(args):
    repo = repo_here()
    name = args.alvo
    if args.b:
        if not valid_branch_name(name):
            raise GitError("nome de ramo inválido: %s" % name)
        if repo.read_ref("refs/heads/" + name):
            raise GitError("o ramo %s já existe" % name)
        _, sha = repo.head()
        if sha is None:
            raise GitError("ainda não há commits para criar um ramo")
        repo.update_ref("refs/heads/" + name, sha)
        repo.set_head_branch(name)
        out("Mudou para o novo ramo '%s'" % name)
        return 0
    sha = repo.read_ref("refs/heads/" + name) if valid_branch_name(name) else None
    if sha:
        checkout(repo, sha, branch=name)
        out("Mudou para o ramo '%s'" % name)
    else:
        target = repo.resolve(name)
        repo.read_commit(target)
        checkout(repo, target)
        out("HEAD destacado em %s" % target[:7])
    return 0


def is_ancestor(repo, ancestor, start):
    """ancestor é alcançável a partir de start seguindo os pais?"""
    pending, seen = [start], set()
    while pending:
        sha = pending.pop()
        if sha == ancestor:
            return True
        if sha in seen:
            continue
        seen.add(sha)
        pending.extend(repo.read_commit(sha).parents)
    return False


def cmd_branch(args):
    repo = repo_here()
    current, sha = repo.head()
    target = args.delete or args.force_delete
    if target:
        if target == current:
            raise GitError("não dá para apagar o ramo atual (%s)" % current)
        tip = repo.read_ref("refs/heads/" + target)
        if not tip:
            raise GitError("ramo não encontrado: %s" % target)
        if args.delete and not (sha and is_ancestor(repo, tip, sha)):
            raise GitError("o ramo %s tem commits que não estão no ramo atual; apagar o ramo "
                           "esconderia esses commits (use -D para apagar mesmo assim)" % target)
        repo.delete_ref("refs/heads/" + target)
        out("Ramo %s apagado (era %s)" % (target, tip[:7]))
        return 0
    if args.nome:
        if not valid_branch_name(args.nome):
            raise GitError("nome de ramo inválido: %s" % args.nome)
        if repo.read_ref("refs/heads/" + args.nome):
            raise GitError("o ramo %s já existe" % args.nome)
        start = repo.resolve(args.inicio or "HEAD")
        repo.read_commit(start)
        repo.update_ref("refs/heads/" + args.nome, start)
        return 0
    if current is None and sha:
        out("* (HEAD destacado em %s)" % sha[:7])
    for name in repo.branches():
        out(("* " if name == current else "  ") + name)
    return 0


def cmd_diff(args):
    repo = repo_here()
    index = {e.path: e for e in repo.read_index() if e.stage == 0}
    chunks = []
    if args.cached:
        head = head_files(repo)
        for path in sorted(set(head) | set(index), key=path_key):
            h, i = head.get(path), index.get(path)
            if h and i and h == (i.mode_text, i.sha):
                continue
            old_data = repo.read_object(h[1])[1] if h else None
            new_data = repo.read_object(i.sha)[1] if i else None
            chunks.append(file_diff(path, h[0] if h else None, h[1] if h else None, old_data,
                                    i.mode_text if i else None, i.sha if i else None, new_data))
    else:
        st = compute_status(repo, refresh=False)
        from .index import mode_for_stat
        from .objects import hash_object
        for path in sorted(st.unstaged, key=path_key):
            e = index[path]
            old_data = repo.read_object(e.sha)[1]
            full = abs_path(repo, path)
            if st.unstaged[path] == "D" or not os.path.lexists(full):
                chunks.append(file_diff(path, e.mode_text, e.sha, old_data, None, None, None))
                continue
            stinfo = os.lstat(full)
            data = read_file_data(full, stinfo)
            chunks.append(file_diff(path, e.mode_text, e.sha, old_data, "%o" % mode_for_stat(stinfo),
                                    hash_object(data), data))
    write_bytes("".join(chunks).encode("utf-8", "surrogateescape"))
    return 0


def cmd_ls_tree(args):
    repo = repo_here()
    root = repo.resolve(args.arvore + "^{tree}")
    if not args.caminhos:
        found = list(iter_tree(repo, root, "", args.r))
    else:
        found = []
        everything = list(iter_tree(repo, root, "", True)) + [
            (path, e) for path, e in _all_trees(repo, root)]
        for wanted in args.caminhos:
            folder = wanted.rstrip("/")
            for path, e in everything:
                if path != folder:
                    continue
                if e.is_tree and (wanted.endswith("/") or args.r):
                    found += list(iter_tree(repo, e.sha, folder + "/", args.r))
                else:
                    found.append((path, e))
        found.sort(key=lambda pe: path_key(pe[0]))
    for path, e in found:
        out(tree_line(path, e, args.name_only))
    return 0


def _all_trees(repo, tree_sha, prefix=""):
    """Todas as subárvores, com o caminho de cada uma."""
    for e in repo.read_tree(tree_sha):
        if e.is_tree:
            yield prefix + e.name, e
            yield from _all_trees(repo, e.sha, prefix + e.name + "/")


def cmd_config(args):
    if args.global_:
        path = global_config_paths()[-1] if not os.environ.get("GIT_CONFIG_GLOBAL") \
            else os.environ["GIT_CONFIG_GLOBAL"]
        values = None
    else:
        repo = repo_here()
        path = os.path.join(repo.git_dir, "config")
        values = repo.config()
    if args.valor is None:
        if values is None:
            from .repo import read_config_files
            values = read_config_files([path])
        value = values.get(args.chave.lower())
        if value is None:
            return 1
        out(value)
        return 0
    set_config_value(path, args.chave, args.valor)
    return 0


# --------------------------------------------------------------- argumentos

def build_parser():
    parser = PtParser(prog=PROG, description="Um mini Git em Python: grava objetos, índice e "
                      "referências no formato real do Git.",
                      epilog="Use \"mini-git <comando> -h\" para ver as opções de cada comando.")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__,
                        help="mostra a versão e sai")
    sub = parser.add_subparsers(dest="command", title="comandos", metavar="<comando>",
                                parser_class=PtParser)

    p = sub.add_parser("init", help="cria um repositório vazio", description="cria um repositório vazio")
    p.add_argument("pasta", nargs="?", help="pasta do repositório (padrão: a atual)")
    p.add_argument("-b", "--initial-branch", help="nome do primeiro ramo (padrão: main)")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("hash-object", help="calcula o SHA-1 de um arquivo como blob", description="calcula o SHA-1 de um arquivo como blob")
    p.add_argument("-w", dest="write", action="store_true", help="grava o objeto no repositório")
    p.add_argument("-t", dest="type", default="blob", metavar="TIPO", help="tipo do objeto (padrão: blob)")
    p.add_argument("--stdin", action="store_true", help="lê o conteúdo da entrada padrão")
    p.add_argument("arquivos", nargs="*")
    p.set_defaults(func=cmd_hash_object)

    p = sub.add_parser("cat-file", help="mostra um objeto", description="mostra um objeto")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("-p", action="store_true", help="conteúdo formatado")
    g.add_argument("-t", action="store_true", help="tipo")
    g.add_argument("-s", action="store_true", help="tamanho em bytes")
    p.add_argument("objeto", help="SHA-1 (pode ser abreviado), ramo, HEAD, HEAD~1...")
    p.set_defaults(func=cmd_cat_file)

    p = sub.add_parser("add", help="põe arquivos no índice", description="põe arquivos no índice")
    p.add_argument("caminhos", nargs="+", help="arquivos ou pastas (use . para tudo)")
    p.add_argument("-f", "--force", action="store_true", help="adiciona mesmo se estiver no .gitignore")
    p.add_argument("-v", "--verbose", action="store_true", help="mostra o que mudou")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("write-tree", help="grava as árvores do índice e mostra o SHA-1 da raiz", description="grava as árvores do índice e mostra o SHA-1 da raiz")
    p.set_defaults(func=cmd_write_tree)

    p = sub.add_parser("commit", help="grava um commit com o conteúdo do índice", description="grava um commit com o conteúdo do índice")
    p.add_argument("-m", dest="message", action="append", required=True, metavar="MENSAGEM",
                   help="mensagem (repita -m para mais parágrafos)")
    p.add_argument("--allow-empty", action="store_true", help="permite um commit sem mudanças")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("log", help="mostra o histórico (seguindo o primeiro pai)", description="mostra o histórico (seguindo o primeiro pai)")
    p.add_argument("revisao", nargs="?", default="HEAD", help="por onde começar (padrão: HEAD)")
    p.add_argument("-n", type=int, metavar="N", help="quantos commits mostrar")
    p.add_argument("--oneline", action="store_true", help="um commit por linha")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("status", help="mostra o que mudou", description="mostra o que mudou")
    p.add_argument("--porcelain", action="store_true", help="formato estável do git status --porcelain")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("checkout", help="troca de ramo ou vai para um commit", description="troca de ramo ou vai para um commit")
    p.add_argument("-b", action="store_true", help="cria o ramo e muda para ele")
    p.add_argument("alvo", help="ramo ou commit")
    p.set_defaults(func=cmd_checkout)

    p = sub.add_parser("branch", help="lista ou cria ramos", description="lista ou cria ramos")
    p.add_argument("nome", nargs="?", help="nome do ramo a criar")
    p.add_argument("inicio", nargs="?", help="commit inicial (padrão: HEAD)")
    p.add_argument("-d", dest="delete", metavar="RAMO", help="apaga um ramo já incorporado ao atual")
    p.add_argument("-D", dest="force_delete", metavar="RAMO", help="apaga um ramo mesmo com commits próprios")
    p.set_defaults(func=cmd_branch)

    p = sub.add_parser("diff", help="diferenças da pasta de trabalho para o índice", description="diferenças da pasta de trabalho para o índice")
    p.add_argument("--cached", action="store_true", help="compara o índice com o HEAD")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("ls-tree", help="lista uma árvore", description="lista uma árvore")
    p.add_argument("-r", action="store_true", help="desce nas subpastas")
    p.add_argument("--name-only", action="store_true", help="só os nomes")
    p.add_argument("arvore", help="árvore, commit ou ramo")
    p.add_argument("caminhos", nargs="*", help="limita a esses caminhos")
    p.set_defaults(func=cmd_ls_tree)

    p = sub.add_parser("config", help="lê ou grava uma configuração", description="lê ou grava uma configuração")
    p.add_argument("--global", dest="global_", action="store_true", help="usa a configuração global")
    p.add_argument("chave", help="por exemplo, user.name")
    p.add_argument("valor", nargs="?", help="novo valor (sem ele, mostra o atual)")
    p.set_defaults(func=cmd_config)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except (GitError, IndexFileError) as exc:
        sys.stderr.write("%s: erro: %s\n" % (PROG, exc))
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
