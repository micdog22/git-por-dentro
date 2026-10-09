"""Testes das peças puras: hashes, árvores, commits, datas, aspas, .gitignore e config."""

import os
import sys
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from git_por_dentro import dates  # noqa: E402
from git_por_dentro.ignore import parse_lines  # noqa: E402
from git_por_dentro.objects import (Commit, ObjectError, ObjectStore, TreeEntry,  # noqa: E402
                                    cleanup_message, format_ident, hash_object, parse_commit,
                                    parse_ident, parse_tree, serialize_commit, serialize_tree)
from git_por_dentro.quoting import quote_path  # noqa: E402
from git_por_dentro.repo import parse_config, valid_branch_name  # noqa: E402

from helpers import RepoTestCase  # noqa: E402


class HashTest(unittest.TestCase):
    def test_known_hashes(self):
        # Os mesmos valores de "git hash-object".
        self.assertEqual(hash_object(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
        self.assertEqual(hash_object(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")
        self.assertEqual(hash_object(b"what is up, doc?"), "bd9dbf5aae1a3862dd1526723246b20206e5fc37")
        self.assertEqual(hash_object(b"", "tree"), "4b825dc642cb6eb9a060e54bf8d69288fbee4904")

    def test_header_is_part_of_the_hash(self):
        import hashlib
        data = "olá, mundo\n".encode("utf-8")
        expected = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        self.assertEqual(hash_object(data), expected)
        self.assertNotEqual(hash_object(data, "blob"), hash_object(data, "commit"))


class StoreTest(RepoTestCase):
    def test_write_read_and_layout(self):
        store = ObjectStore(self.tmp)
        sha = store.write("blob", b"hello\n")
        path = os.path.join(self.tmp, "objects", "ce", "013625030ba8dba906f756967f9e9ca394464a")
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as f:
            self.assertEqual(zlib.decompress(f.read()), b"blob 6\0hello\n")
        self.assertEqual(store.read(sha), ("blob", b"hello\n"))
        self.assertEqual(store.write("blob", b"hello\n"), sha)   # idempotente
        self.assertEqual(store.find_prefix("ce0136"), [sha])
        self.assertEqual(store.find_prefix("ce0"), [])            # curto demais

    def test_corruption_is_detected(self):
        store = ObjectStore(self.tmp)
        sha = store.write("blob", b"conteudo original\n")
        path = store.path(sha)
        os.chmod(path, 0o644)
        with open(path, "wb") as f:
            f.write(zlib.compress(b"blob 18\0conteudo alterado\n"))
        with self.assertRaises(ObjectError) as ctx:
            store.read(sha)
        self.assertIn("SHA-1", str(ctx.exception))
        with open(path, "wb") as f:
            f.write(b"isto nao e zlib")
        with self.assertRaises(ObjectError):
            store.read(sha)
        with self.assertRaises(ObjectError):
            store.read("0" * 40)


class TreeTest(unittest.TestCase):
    def test_round_trip_and_order(self):
        blob = hash_object(b"x")
        entries = [TreeEntry("100644", "b.txt", blob), TreeEntry("40000", "a", blob),
                   TreeEntry("100644", "a.txt", blob), TreeEntry("100755", "a-b", blob),
                   TreeEntry("120000", "link", blob)]
        data = serialize_tree(entries)
        names = [e.name for e in parse_tree(data)]
        # "a.txt" vem antes da pasta "a", que é comparada como "a/"; "-" vem antes de ".".
        self.assertEqual(names, ["a-b", "a.txt", "a", "b.txt", "link"])
        self.assertEqual(parse_tree(data)[2], TreeEntry("40000", "a", blob))
        self.assertIn(b"40000 a\0", data)      # sem zero à esquerda no modo
        with self.assertRaises(ObjectError):
            serialize_tree([TreeEntry("040000", "x", blob)])
        with self.assertRaises(ObjectError):
            parse_tree(b"100644 truncado\0abc")


class CommitTest(unittest.TestCase):
    def test_round_trip(self):
        c = Commit("4b825dc642cb6eb9a060e54bf8d69288fbee4904", ["a" * 40],
                   "Ana <ana@x.com> 1700000000 -0300", "Bia <bia@x.com> 1700000100 +0000",
                   "assunto\n\ncorpo\n")
        data = serialize_commit(c)
        self.assertTrue(data.startswith(b"tree 4b825dc6"))
        self.assertIn(b"\nparent " + b"a" * 40 + b"\n", data)
        back = parse_commit(data)
        self.assertEqual((back.tree, back.parents, back.author, back.committer, back.message),
                         (c.tree, c.parents, c.author, c.committer, c.message))

    def test_extra_headers_survive(self):
        raw = (b"tree " + b"1" * 40 + b"\nauthor A <a@b> 1 +0000\ncommitter A <a@b> 1 +0000\n"
               b"gpgsig -----BEGIN-----\n linha 2\n -----END-----\n\nmsg\n")
        c = parse_commit(raw)
        self.assertEqual(c.extra, [("gpgsig", "-----BEGIN-----\nlinha 2\n-----END-----")])
        self.assertEqual(serialize_commit(c), raw)

    def test_ident(self):
        self.assertEqual(format_ident("  Ana Souza ", "<ana@x.com>", 1700000000, "-0300"),
                         "Ana Souza <ana@x.com> 1700000000 -0300")
        self.assertEqual(format_ident("Jo<a>o", "j@x", 1, "+0000"), "Joao <j@x> 1 +0000")
        self.assertEqual(parse_ident("Ana Souza <ana@x.com> 1700000000 -0300"),
                         ("Ana Souza", "ana@x.com", 1700000000, "-0300"))
        with self.assertRaises(ObjectError):
            parse_ident("sem email 123 +0000")

    def test_message_cleanup(self):
        self.assertEqual(cleanup_message("oi"), "oi\n")
        self.assertEqual(cleanup_message("\n\n  Título  \n\n\n\ncorpo   \n\n"), "  Título\n\ncorpo\n")
        self.assertEqual(cleanup_message("   \n\n"), "")
        self.assertEqual(cleanup_message("a\n\n\nb"), "a\n\nb\n")


class DatesTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(dates.parse_git_date("1700000000 -0300"), (1700000000, -180))
        self.assertEqual(dates.parse_git_date("@1700000000 +0530"), (1700000000, 330))
        self.assertEqual(dates.parse_git_date("@1700000000"), (1700000000, 0))
        self.assertEqual(dates.parse_git_date("2023-11-14T19:13:20-03:00"), (1700000000, -180))
        self.assertEqual(dates.parse_git_date("2023-11-14 22:13:20 +0000"), (1700000000, 0))
        self.assertEqual(dates.parse_git_date("2023-11-14T22:13:20Z"), (1700000000, 0))
        self.assertEqual(dates.parse_git_date("Tue, 14 Nov 2023 19:13:20 -0300"), (1700000000, -180))
        self.assertEqual(dates.parse_git_date("2023-11-05T08:07:06+05:30"), (1699151826, 330))
        with self.assertRaises(ValueError):
            dates.parse_git_date("ontem à tarde")

    def test_format(self):
        self.assertEqual(dates.format_tz(-180), "-0300")
        self.assertEqual(dates.format_tz(330), "+0530")
        self.assertEqual(dates.format_tz(0), "+0000")
        self.assertEqual(dates.parse_tz("-0300"), -180)
        self.assertEqual(dates.format_default(1700000000, -180), "Tue Nov 14 19:13:20 2023 -0300")
        self.assertEqual(dates.format_default(1699151826, 330), "Sun Nov 5 08:07:06 2023 +0530")


class QuotingTest(unittest.TestCase):
    def test_quote(self):
        self.assertEqual(quote_path("simples.txt"), "simples.txt")
        self.assertEqual(quote_path("a b.txt"), "a b.txt")
        self.assertEqual(quote_path("a b.txt", quote_space=True), '"a b.txt"')
        self.assertEqual(quote_path("relatório.txt"), '"relat\\303\\263rio.txt"')
        self.assertEqual(quote_path('aspas".txt'), '"aspas\\".txt"')
        self.assertEqual(quote_path("tab\tx"), '"tab\\tx"')
        self.assertEqual(quote_path("bell\x07"), '"bell\\a"')
        self.assertEqual(quote_path("ctrl\x01"), '"ctrl\\001"')


class IgnoreTest(unittest.TestCase):
    def check(self, text, path, is_dir=False, base=""):
        ignored = False
        for rule in parse_lines(text, base):
            if rule.matches(path, is_dir):
                ignored = not rule.negate
        return ignored

    def test_patterns(self):
        self.assertTrue(self.check("*.log", "debug.log"))
        self.assertTrue(self.check("*.log", "a/b/debug.log"))
        self.assertFalse(self.check("*.log", "debug.log.txt"))
        self.assertTrue(self.check("/build", "build", True))
        self.assertFalse(self.check("/build", "src/build", True))
        self.assertTrue(self.check("build/", "src/build", True))
        self.assertFalse(self.check("build/", "build", False))
        self.assertTrue(self.check("doc/*.txt", "doc/a.txt"))
        self.assertFalse(self.check("doc/*.txt", "doc/sub/a.txt"))
        self.assertTrue(self.check("doc/**/*.txt", "doc/sub/x/a.txt"))
        self.assertTrue(self.check("**/temp", "a/b/temp", True))
        self.assertTrue(self.check("logs/**", "logs/a/b.txt"))
        self.assertTrue(self.check("*.log\n!importante.log", "x.log"))
        self.assertFalse(self.check("*.log\n!importante.log", "importante.log"))
        self.assertTrue(self.check("arquivo?.txt", "arquivo1.txt"))
        self.assertTrue(self.check("[ab]*.txt", "beta.txt"))
        self.assertFalse(self.check("[!ab]*.txt", "beta.txt"))
        self.assertTrue(self.check("[]x]", "]"))
        self.assertFalse(self.check("# comentário\n\n", "# comentário"))
        self.assertTrue(self.check("\\#literal", "#literal"))
        self.assertTrue(self.check("tmp/", "sub/tmp", True, base="sub"))
        self.assertFalse(self.check("/x.txt", "outra/x.txt", base="sub"))
        self.assertTrue(self.check("/x.txt", "sub/x.txt", base="sub"))


class ConfigTest(unittest.TestCase):
    def test_parse(self):
        text = """
# comentário
[user]
    name = Ana Souza ; comentário no fim
    email = "ana@exemplo.com.br"
[core]
    bare
    autocrlf = false
[remote "origin"]
    url = https://exemplo.com/repo.git
"""
        cfg = parse_config(text)
        self.assertEqual(cfg["user.name"], "Ana Souza")
        self.assertEqual(cfg["user.email"], "ana@exemplo.com.br")
        self.assertEqual(cfg["core.bare"], "true")
        self.assertEqual(cfg["remote.origin.url"], "https://exemplo.com/repo.git")

    def test_branch_names(self):
        for good in ("main", "feature/login", "v1.2", "correção"):
            self.assertTrue(valid_branch_name(good), good)
        for bad in ("", "-x", "a..b", "a b", "a~1", "a^", "a:b", "x.lock", "a/", "/a", "a//b",
                    ".escondido", "a/.b", "@", "a@{1}", "fim."):
            self.assertFalse(valid_branch_name(bad), bad)


if __name__ == "__main__":
    unittest.main()
