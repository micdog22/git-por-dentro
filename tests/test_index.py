"""Testes do índice (.git/index) no formato versão 2."""

import hashlib
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from git_por_dentro.index import (IndexEntry, IndexFileError, read_index,  # noqa: E402
                                  write_index)
from git_por_dentro.objects import hash_object  # noqa: E402
from git_por_dentro.quoting import quote_path  # noqa: E402

from helpers import HAS_GIT, RepoTestCase  # noqa: E402


def entry(path, data=b"x", mode=0o100644):
    return IndexEntry(path, hash_object(data), mode, ctime_s=1, ctime_ns=2, mtime_s=3, mtime_ns=4,
                      dev=5, ino=6, uid=7, gid=8, size=len(data))


class IndexFormatTest(RepoTestCase):
    def test_round_trip_and_padding(self):
        # Caminhos de 1 a 20 bytes passam por todas as quantidades de preenchimento.
        entries = [entry("p" * n, data=str(n).encode()) for n in range(1, 21)]
        entries.append(entry("pasta/sub/arquivo com espaço.txt", mode=0o100755))
        entries.append(entry("relatório.txt"))
        path = self.path("index")
        write_index(path, entries)
        with open(path, "rb") as f:
            data = f.read()
        self.assertEqual(data[:4], b"DIRC")
        self.assertEqual(struct.unpack(">II", data[4:12]), (2, len(entries)))
        self.assertEqual(hashlib.sha1(data[:-20]).digest(), data[-20:])

        back = read_index(path)
        self.assertEqual([e.path for e in back],
                         sorted((e.path for e in entries), key=lambda p: p.encode("utf-8")))
        by_path = {e.path: e for e in back}
        e = by_path["pasta/sub/arquivo com espaço.txt"]
        self.assertEqual((e.mode, e.mtime_s, e.ino, e.uid, e.size), (0o100755, 3, 6, 7, 1))
        self.assertEqual(e.sha, hash_object(b"x"))

        # Cada entrada ocupa um múltiplo de 8 bytes, com pelo menos um byte zero.
        pos = 12
        for e in back:
            raw = e.path.encode("utf-8")
            length = (62 + len(raw) + 8) & ~7
            self.assertEqual(data[pos + 62:pos + 62 + len(raw)], raw)
            self.assertEqual(data[pos + 62 + len(raw):pos + length], b"\0" * (length - 62 - len(raw)))
            pos += length
        self.assertEqual(pos, len(data) - 20)

    def test_errors(self):
        path = self.path("index")
        self.assertEqual(read_index(path), [])           # sem índice: vazio
        write_index(path, [entry("a")])
        with open(path, "r+b") as f:
            f.seek(70)
            f.write(b"Z")
        with self.assertRaises(IndexFileError):
            read_index(path)
        with open(path, "wb") as f:
            f.write(b"XXXX" + b"\0" * 40)
        with self.assertRaises(IndexFileError):
            read_index(path)
        body = b"DIRC" + struct.pack(">II", 4, 0)
        with open(path, "wb") as f:
            f.write(body + hashlib.sha1(body).digest())
        with self.assertRaises(IndexFileError) as ctx:
            read_index(path)
        self.assertIn("versão 4", str(ctx.exception))

    def test_lock_protects_index(self):
        path = self.path("index")
        open(path + ".lock", "w").close()
        with self.assertRaises(IndexFileError) as ctx:
            write_index(path, [entry("a")])
        self.assertIn("index.lock", str(ctx.exception))


@unittest.skipUnless(HAS_GIT, "git não está instalado")
class IndexCompatTest(RepoTestCase):
    def fill(self, repo):
        self.write(repo, "a.txt", "um\n")
        self.write(repo, "pasta/b.txt", "dois\n")
        self.write(repo, "pasta/funda/c.sh", "#!/bin/sh\n", mode=0o755)
        self.write(repo, "z com espaço.txt", "tres\n")
        os.symlink("a.txt", os.path.join(repo, "atalho"))

    def test_mini_reads_index_written_by_git(self):
        repo = self.new_repo(tool="git")
        self.fill(repo)
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-q", "-m", "x")       # o git grava também a extensão TREE
        self.write(repo, "novo.txt", "novo\n")
        self.git(repo, "add", "novo.txt")
        expected = self.git(repo, "ls-files", "-s").splitlines()
        entries = read_index(os.path.join(repo, ".git", "index"))
        got = ["%s %s %d\t%s" % (e.mode_text, e.sha, e.stage, quote_path(e.path)) for e in entries]
        self.assertEqual(got, expected)

    def test_git_reads_index_written_by_mini(self):
        repo = self.new_repo()
        self.fill(repo)
        self.mini(repo, "add", ".")
        listing = self.git(repo, "ls-files", "-s", "--debug")
        self.assertIn("100755", listing)
        self.assertIn("120000", listing)
        # Sem commits, os dois mostram tudo como adicionado ("A ").
        status = self.git(repo, "status", "--porcelain")
        self.assertEqual(status, self.mini(repo, "status", "--porcelain")[0])
        self.assertEqual(len(status.splitlines()), 5)
        self.assertTrue(all(line.startswith("A  ") for line in status.splitlines()))
        # O write-tree do git, lendo o nosso índice, dá a mesma árvore que o nosso.
        ours, _ = self.mini(repo, "write-tree")
        self.assertEqual(self.git(repo, "write-tree").strip(), ours.strip())


if __name__ == "__main__":
    unittest.main()
