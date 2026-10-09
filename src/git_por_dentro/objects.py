"""Os objetos do Git: blob, tree e commit.

Todo objeto é guardado do mesmo jeito: um cabeçalho ``<tipo> <tamanho>\\0``,
seguido do conteúdo. O SHA-1 desse conjunto é o nome do objeto, e o
conjunto comprimido com zlib vai para ``.git/objects/<2 primeiros>/<38 restantes>``.
"""

import hashlib
import os
import re
import tempfile
import zlib

OBJECT_TYPES = ("blob", "tree", "commit", "tag")
TREE_MODE = "40000"
MODES = ("100644", "100755", "120000", "160000", TREE_MODE)


class ObjectError(Exception):
    """Objeto ausente, corrompido ou em formato inesperado."""


def encode_path(path):
    """Caminho do sistema (str) para bytes, preservando bytes inválidos em UTF-8."""
    return path.encode("utf-8", "surrogateescape")


def decode_path(raw):
    return raw.decode("utf-8", "surrogateescape")


def header(obj_type, size):
    return ("%s %d" % (obj_type, size)).encode("ascii") + b"\0"


def hash_object(data, obj_type="blob"):
    """O nome de um objeto: SHA-1 de ``<tipo> <tamanho>\\0<conteúdo>``."""
    return hashlib.sha1(header(obj_type, len(data)) + data).hexdigest()


class ObjectStore:
    """Objetos soltos (loose objects) em .git/objects. Packfiles não são lidos."""

    def __init__(self, git_dir):
        self.root = os.path.join(git_dir, "objects")

    def path(self, sha):
        return os.path.join(self.root, sha[:2], sha[2:])

    def exists(self, sha):
        return os.path.isfile(self.path(sha))

    def write(self, obj_type, data):
        raw = header(obj_type, len(data)) + data
        sha = hashlib.sha1(raw).hexdigest()
        path = self.path(sha)
        if os.path.exists(path):
            return sha
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        # Grava num arquivo temporário e renomeia: um objeto nunca fica pela metade.
        fd, tmp = tempfile.mkstemp(dir=folder, prefix="tmp_obj_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(zlib.compress(raw))
            os.chmod(tmp, 0o444)  # objetos são imutáveis, como no Git
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return sha

    def read(self, sha):
        """Devolve (tipo, conteúdo), conferindo cabeçalho, tamanho e SHA-1."""
        path = self.path(sha)
        try:
            with open(path, "rb") as f:
                compressed = f.read()
        except FileNotFoundError:
            pack_dir = os.path.join(self.root, "pack")
            if os.path.isdir(pack_dir) and any(n.endswith(".pack") for n in os.listdir(pack_dir)):
                raise ObjectError("objeto %s não está solto em .git/objects; ele deve estar num "
                                  "packfile, formato que o mini-git não lê" % sha) from None
            raise ObjectError("objeto não encontrado: %s" % sha) from None
        try:
            raw = zlib.decompress(compressed)
        except zlib.error as exc:
            raise ObjectError("objeto %s corrompido (zlib: %s)" % (sha, exc)) from None
        nul = raw.find(b"\0")
        m = re.match(rb"^(blob|tree|commit|tag) (\d+)$", raw[:nul]) if nul > 0 else None
        if not m:
            raise ObjectError("objeto %s com cabeçalho inválido" % sha)
        obj_type, size = m.group(1).decode(), int(m.group(2))
        data = raw[nul + 1:]
        if len(data) != size:
            raise ObjectError("objeto %s com tamanho errado (%d em vez de %d)" % (sha, len(data), size))
        if hashlib.sha1(raw).hexdigest() != sha:
            raise ObjectError("objeto %s corrompido: o SHA-1 do conteúdo não confere" % sha)
        return obj_type, data

    def find_prefix(self, prefix):
        """Todos os objetos cujo nome começa com prefix (ao menos 4 caracteres hexadecimais)."""
        prefix = prefix.lower()
        folder = os.path.join(self.root, prefix[:2])
        if len(prefix) < 4 or not os.path.isdir(folder):
            return []
        rest = prefix[2:]
        return sorted(prefix[:2] + name for name in os.listdir(folder)
                      if name.startswith(rest) and len(name) == 38)


# ------------------------------------------------------------------- árvores

class TreeEntry:
    __slots__ = ("mode", "name", "sha")

    def __init__(self, mode, name, sha):
        self.mode = mode
        self.name = name
        self.sha = sha

    @property
    def is_tree(self):
        return self.mode == TREE_MODE

    @property
    def type(self):
        if self.mode == TREE_MODE:
            return "tree"
        if self.mode == "160000":
            return "commit"
        return "blob"

    def __eq__(self, other):
        return (self.mode, self.name, self.sha) == (other.mode, other.name, other.sha)

    def __repr__(self):
        return "TreeEntry(%r, %r, %r)" % (self.mode, self.name, self.sha)


def tree_sort_key(entry):
    """O Git ordena as entradas comparando nomes de pastas como se tivessem '/' no fim."""
    name = encode_path(entry.name)
    return name + b"/" if entry.is_tree else name


def serialize_tree(entries):
    """Cada entrada vira ``<modo> <nome>\\0<20 bytes do SHA-1>``, em ordem."""
    out = []
    for e in sorted(entries, key=tree_sort_key):
        if e.mode not in MODES:
            raise ObjectError("modo inválido numa árvore: %s" % e.mode)
        out.append(e.mode.encode("ascii") + b" " + encode_path(e.name) + b"\0" + bytes.fromhex(e.sha))
    return b"".join(out)


def parse_tree(data):
    entries = []
    pos = 0
    while pos < len(data):
        space = data.find(b" ", pos)
        nul = data.find(b"\0", space + 1)
        if space < 0 or nul < 0 or nul + 21 > len(data):
            raise ObjectError("árvore em formato inválido")
        mode = data[pos:space].decode("ascii")
        name = decode_path(data[space + 1:nul])
        sha = data[nul + 1:nul + 21].hex()
        entries.append(TreeEntry(mode, name, sha))
        pos = nul + 21
    return entries


# ------------------------------------------------------------------- commits

class Commit:
    def __init__(self, tree, parents, author, committer, message, extra=None):
        self.tree = tree
        self.parents = list(parents)
        self.author = author          # "Nome <email> 1700000000 -0300"
        self.committer = committer
        self.message = message        # texto, normalmente terminando em "\n"
        self.extra = extra or []      # outros cabeçalhos (gpgsig, encoding...), preservados


def serialize_commit(c):
    lines = ["tree %s" % c.tree]
    lines += ["parent %s" % p for p in c.parents]
    lines.append("author %s" % c.author)
    lines.append("committer %s" % c.committer)
    for key, value in c.extra:
        lines.append("%s %s" % (key, value.replace("\n", "\n ")))
    return ("\n".join(lines) + "\n\n" + c.message).encode("utf-8", "surrogateescape")


def parse_commit(data):
    text = data.decode("utf-8", "surrogateescape")
    head, sep, message = text.partition("\n\n")
    if not sep:
        head, message = text.rstrip("\n"), ""
    tree, parents, author, committer, extra = None, [], None, None, []
    for line in head.split("\n"):
        if line.startswith(" ") and extra:
            key, value = extra[-1]          # continuação de cabeçalho com várias linhas
            extra[-1] = (key, value + "\n" + line[1:])
            continue
        key, _, value = line.partition(" ")
        if key == "tree":
            tree = value
        elif key == "parent":
            parents.append(value)
        elif key == "author":
            author = value
        elif key == "committer":
            committer = value
        else:
            extra.append((key, value))
    if tree is None or author is None or committer is None:
        raise ObjectError("commit em formato inválido")
    return Commit(tree, parents, author, committer, message, extra)


# ------------------------------------------------------------- identidades

_CRUD = set(range(0, 33)) | set(b",:;<>\"\\'")


def _without_crud(text):
    """Limpa nome ou email como o Git: tira lixo das pontas e os delimitadores < > e \\n."""
    raw = text.encode("utf-8", "surrogateescape")
    start, end = 0, len(raw)
    while start < end and raw[start] in _CRUD:
        start += 1
    while end > start and raw[end - 1] in _CRUD:
        end -= 1
    cleaned = bytes(b for b in raw[start:end] if b not in b"\n<>")
    return cleaned.decode("utf-8", "surrogateescape")


def format_ident(name, email, timestamp, tz_text):
    return "%s <%s> %d %s" % (_without_crud(name), _without_crud(email), timestamp, tz_text)


_IDENT = re.compile(r"^(?P<name>.*?) ?<(?P<email>[^<>]*)> (?P<ts>-?\d+) (?P<tz>[+-]\d{4})$")


def parse_ident(text):
    """"Ana <ana@x> 1700000000 -0300" vira (nome, email, segundos, fuso)."""
    m = _IDENT.match(text)
    if not m:
        raise ObjectError("identidade inválida: %r" % text)
    return m.group("name"), m.group("email"), int(m.group("ts")), m.group("tz")


def cleanup_message(message):
    """Limpa a mensagem como ``git commit -m``: tira espaços no fim das linhas e linhas em
    branco no começo e no fim, junta linhas em branco seguidas e termina com uma quebra."""
    lines = [line.rstrip() for line in message.split("\n")]
    out = []
    for line in lines:
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n" if out else ""
