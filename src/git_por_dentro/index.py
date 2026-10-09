"""O índice (.git/index), a "área de preparação" do Git, no formato versão 2.

    cabeçalho   "DIRC" | versão (4 bytes) | número de entradas (4 bytes)
    entradas    ctime, mtime, dev, ino, modo, uid, gid, tamanho (32 bits cada),
                SHA-1 (20 bytes), flags (16 bits) e o caminho, completado com
                1 a 8 bytes zero para a entrada ter tamanho múltiplo de 8
    extensões   opcionais (o Git grava, por exemplo, o cache de árvores "TREE")
    final       SHA-1 de tudo o que veio antes

Todos os números são big-endian. Os dados de stat (datas, inode, tamanho)
permitem saber que um arquivo não mudou sem precisar ler o conteúdo.
"""

import hashlib
import os
import stat
import struct

from .objects import decode_path, encode_path

SIGNATURE = b"DIRC"
_FIXED = struct.Struct(">10I20sH")   # 62 bytes antes do caminho


class IndexFileError(Exception):
    """Índice ausente, corrompido ou em versão não suportada."""


class IndexEntry:
    __slots__ = ("ctime_s", "ctime_ns", "mtime_s", "mtime_ns", "dev", "ino", "mode",
                 "uid", "gid", "size", "sha", "flags", "path")

    def __init__(self, path, sha, mode, ctime_s=0, ctime_ns=0, mtime_s=0, mtime_ns=0,
                 dev=0, ino=0, uid=0, gid=0, size=0, flags=0):
        self.path = path
        self.sha = sha
        self.mode = mode          # inteiro: 0o100644, 0o100755, 0o120000 ou 0o160000
        self.ctime_s, self.ctime_ns = ctime_s, ctime_ns
        self.mtime_s, self.mtime_ns = mtime_s, mtime_ns
        self.dev, self.ino, self.uid, self.gid = dev, ino, uid, gid
        self.size = size
        self.flags = flags

    @property
    def stage(self):
        return (self.flags >> 12) & 3

    @property
    def mode_text(self):
        return "%o" % self.mode

    @classmethod
    def from_stat(cls, path, sha, mode, st):
        u32 = 0xFFFFFFFF
        return cls(path, sha, mode,
                   ctime_s=int(st.st_ctime) & u32, ctime_ns=st.st_ctime_ns % 1000000000,
                   mtime_s=int(st.st_mtime) & u32, mtime_ns=st.st_mtime_ns % 1000000000,
                   dev=st.st_dev & u32, ino=st.st_ino & u32, uid=st.st_uid & u32,
                   gid=st.st_gid & u32, size=st.st_size & u32)

    def stat_matches(self, st):
        """Os dados de stat batem? (Se batem, o conteúdo quase certamente é o mesmo.)"""
        u32 = 0xFFFFFFFF
        return (self.mtime_s == int(st.st_mtime) & u32 and self.mtime_ns == st.st_mtime_ns % 1000000000
                and self.ctime_s == int(st.st_ctime) & u32 and self.ctime_ns == st.st_ctime_ns % 1000000000
                and self.size == st.st_size & u32 and self.ino == st.st_ino & u32)


def mode_for_stat(st):
    """Modo do Git para um arquivo: link simbólico, executável ou comum."""
    if stat.S_ISLNK(st.st_mode):
        return 0o120000
    if st.st_mode & stat.S_IXUSR:
        return 0o100755
    return 0o100644


def sort_key(entry):
    return encode_path(entry.path), entry.stage


def read_index(path):
    """Lê o índice; devolve a lista de entradas (vazia se o arquivo não existe)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return []
    if len(data) < 32 or data[:4] != SIGNATURE:
        raise IndexFileError("índice corrompido: assinatura DIRC ausente")
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3):
        raise IndexFileError("índice na versão %d; o mini-git lê as versões 2 e 3" % version)
    body, checksum = data[:-20], data[-20:]
    if checksum != b"\0" * 20 and hashlib.sha1(body).digest() != checksum:
        raise IndexFileError("índice corrompido: o SHA-1 do final não confere")

    entries = []
    pos = 12
    for _ in range(count):
        if pos + _FIXED.size > len(body):
            raise IndexFileError("índice truncado")
        fields = _FIXED.unpack_from(body, pos)
        flags = fields[11]
        start = pos + _FIXED.size
        if version >= 3 and flags & 0x4000:
            start += 2   # flags estendidas (intent-to-add, skip-worktree)
        end = body.find(b"\0", start)
        if end < 0:
            raise IndexFileError("índice com caminho sem terminador")
        entry = IndexEntry(decode_path(body[start:end]), fields[10].hex(), fields[6],
                           ctime_s=fields[0], ctime_ns=fields[1], mtime_s=fields[2],
                           mtime_ns=fields[3], dev=fields[4], ino=fields[5], uid=fields[7],
                           gid=fields[8], size=fields[9], flags=flags)
        entries.append(entry)
        # Entrada inteira (a partir do ctime) com tamanho múltiplo de 8.
        pos += (end - pos + 8) & ~7
    # O que sobra até o checksum são extensões, que o mini-git ignora.
    return entries


def write_index(path, entries):
    """Grava o índice na versão 2, de forma atômica (via index.lock, como o Git)."""
    entries = sorted(entries, key=sort_key)
    out = [SIGNATURE, struct.pack(">II", 2, len(entries))]
    for e in entries:
        raw_path = encode_path(e.path)
        flags = (e.flags & 0x3000) | min(len(raw_path), 0xFFF)
        packed = _FIXED.pack(e.ctime_s, e.ctime_ns, e.mtime_s, e.mtime_ns, e.dev, e.ino, e.mode,
                             e.uid, e.gid, e.size, bytes.fromhex(e.sha), flags)
        length = (_FIXED.size + len(raw_path) + 8) & ~7     # pelo menos um byte zero
        out.append(packed + raw_path + b"\0" * (length - _FIXED.size - len(raw_path)))
    body = b"".join(out)
    data = body + hashlib.sha1(body).digest()

    lock = path + ".lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise IndexFileError("o arquivo %s existe: outro processo do Git está rodando? Se não "
                          "estiver, apague esse arquivo" % lock) from None
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(lock, path)
    except BaseException:
        if os.path.exists(lock):
            os.unlink(lock)
        raise
