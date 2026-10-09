"""Compatibilidade com o Git de verdade.

O mini-git grava o formato real do Git; então o git precisa ler tudo o que
ele cria (fsck, log, cat-file, status) e o mini-git precisa ler o que o git
cria. Com a mesma identidade e as mesmas datas, os dois produzem
exatamente os mesmos SHA-1 de commit.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from helpers import HAS_GIT, RepoTestCase  # noqa: E402


@unittest.skipUnless(HAS_GIT, "git não está instalado")
class SameHashesTest(RepoTestCase):
    """O mesmo conteúdo, commitado pelo git e pelo mini-git, dá os mesmos commits."""

    def both(self, action):
        action(self.ours)
        action(self.theirs)

    def commit_both(self, *messages, env=None):
        args = []
        for m in messages:
            args += ["-m", m]
        saved = {k: os.environ.get(k) for k in (env or {})}
        os.environ.update(env or {})
        try:
            self.mini(self.ours, "add", ".")
            self.mini(self.ours, "commit", *args)
            self.git(self.theirs, "add", "-A")
            self.git(self.theirs, "commit", "-q", *args)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        ours = self.git(self.ours, "rev-parse", "HEAD").strip()
        theirs = self.git(self.theirs, "rev-parse", "HEAD").strip()
        self.assertEqual(ours, theirs, "commits diferentes:\n%s\n---\n%s" % (
            self.git(self.ours, "cat-file", "-p", "HEAD"), self.git(self.theirs, "cat-file", "-p", "HEAD")))
        return ours

    def test_sequence_of_commits(self):
        self.ours = self.new_repo("nosso")
        self.theirs = self.new_repo("deles", tool="git")

        def first(repo):
            self.write(repo, "README.md", "# Projeto\n\nOlá, mundo!\n")
            self.write(repo, "src/main.py", "print('oi')\n")
            self.write(repo, "src/util/ajuda.py", "def ajuda():\n    return 42\n")
            self.write(repo, "scripts/rodar.sh", "#!/bin/sh\necho rodando\n", mode=0o755)
            self.write(repo, "vazio.txt", "")
            self.write(repo, "nome com espaço.txt", "espaço\n")
            self.write(repo, "relatório.txt", "acentuação\n")
            self.write(repo, "dados.bin", bytes(range(256)) * 4)
            os.symlink("README.md", os.path.join(repo, "atalho"))
            os.makedirs(os.path.join(repo, "pasta-vazia"))
        self.both(first)
        h1 = self.commit_both("Primeiro commit")

        def second(repo):
            self.write(repo, "README.md", "# Projeto\n\nOlá de novo!\n")
            os.unlink(os.path.join(repo, "src", "util", "ajuda.py"))
            self.write(repo, "docs/guia.md", "Guia\n")
            os.chmod(os.path.join(repo, "scripts", "rodar.sh"), 0o644)
        self.both(second)
        self.commit_both("  Segundo commit   ", "Corpo com espaços no fim   \n\n\n\nfim\n\n",
                         env={"GIT_AUTHOR_DATE": "2024-03-01T12:30:45+02:00",
                              "GIT_COMMITTER_DATE": "Fri, 01 Mar 2024 10:31:00 -0300"})

        def third(repo):
            # Ordem das entradas: "a-b", "a.txt", a pasta "a" (comparada como "a/") e "a0".
            self.write(repo, "a.txt", "a\n")
            self.write(repo, "a/dentro.txt", "dentro\n")
            self.write(repo, "a-b", "hífen\n")
            self.write(repo, "a0", "zero\n")
            os.unlink(os.path.join(repo, "atalho"))
            self.write(repo, "atalho", "agora sou um arquivo\n")   # link vira arquivo
        self.both(third)
        h3 = self.commit_both("Terceiro", env={"GIT_AUTHOR_NAME": "Carla Dias",
                                               "GIT_AUTHOR_EMAIL": "carla@exemplo.com.br",
                                               "GIT_AUTHOR_DATE": "@1710000000 +0545"})
        self.assertNotEqual(h1, h3)
        self.assert_fsck_clean(self.ours)
        self.assertEqual(self.git(self.ours, "log", "--format=%H %an %ad %s", "--date=raw"),
                         self.git(self.theirs, "log", "--format=%H %an %ad %s", "--date=raw"))

    def test_hash_object(self):
        repo = self.new_repo()
        f = self.write(repo, "arquivo.txt", "conteúdo qualquer\n")
        out, _ = self.mini(repo, "hash-object", "arquivo.txt")
        self.assertEqual(out.strip(), self.git(repo, "hash-object", f).strip())
        out, _ = self.mini(repo, "hash-object", "--stdin", stdin=b"de entrada\n")
        expected = subprocess.run(["git", "hash-object", "--stdin"], input=b"de entrada\n",
                                  stdout=subprocess.PIPE, check=True).stdout.decode().strip()
        self.assertEqual(out.strip(), expected)
        out, _ = self.mini(repo, "hash-object", "-w", "arquivo.txt")
        self.assertEqual(self.git(repo, "cat-file", "-p", out.strip()), "conteúdo qualquer\n")


@unittest.skipUnless(HAS_GIT, "git não está instalado")
class GitReadsMiniTest(RepoTestCase):
    """O git lê o repositório criado pelo mini-git."""

    def build(self):
        repo = self.new_repo()
        self.write(repo, "a.txt", "um\n")
        self.write(repo, "pasta/b.txt", "dois\n")
        self.write(repo, "pasta/exec.sh", "#!/bin/sh\n", mode=0o755)
        self.mini(repo, "add", ".")
        self.mini(repo, "commit", "-m", "inicial")
        self.mini(repo, "branch", "experimento")
        self.write(repo, "a.txt", "um\ndois\n")
        self.write(repo, "relatório final.md", "texto\n")
        self.mini(repo, "add", ".")
        os.environ["GIT_COMMITTER_DATE"] = "1700009999 +0100"
        self.mini(repo, "commit", "-m", "segundo commit", "-m", "com um corpo\nde duas linhas")
        return repo

    def test_fsck_log_and_objects(self):
        repo = self.build()
        self.assert_fsck_clean(repo)
        self.assertEqual(self.mini(repo, "log")[0], self.git(repo, "log", "--no-decorate"))
        self.assertEqual(self.mini(repo, "log", "--oneline")[0],
                         self.git(repo, "log", "--oneline", "--no-decorate"))
        self.assertEqual(self.mini(repo, "log", "-n", "1")[0],
                         self.git(repo, "log", "-n", "1", "--no-decorate"))
        self.assertEqual(self.mini(repo, "branch")[0], self.git(repo, "branch"))

        listing = self.git(repo, "rev-list", "--objects", "--all").split("\n")
        shas = [line.split(" ")[0] for line in listing if line]
        self.assertGreaterEqual(len(shas), 8)
        for sha in shas:
            for flag in ("-t", "-s"):
                self.assertEqual(self.mini(repo, "cat-file", flag, sha)[0],
                                 self.git(repo, "cat-file", flag, sha), sha)
            ours = self.mini(repo, "cat-file", "-p", sha)[0].encode("utf-8", "surrogateescape")
            self.assertEqual(ours, self.git_bytes(repo, "cat-file", "-p", sha), sha)
        for args in (["HEAD"], ["-r", "HEAD"], ["--name-only", "-r", "HEAD"], ["HEAD", "pasta"],
                     ["HEAD", "pasta/"], ["-r", "HEAD", "pasta"], ["HEAD~1"]):
            self.assertEqual(self.mini(repo, "ls-tree", *args)[0], self.git(repo, "ls-tree", *args),
                             args)
        # Revisões: HEAD~1, ^{tree}, SHA-1 abreviado e nome de ramo.
        for rev in ("HEAD~1", "HEAD^", "HEAD^{tree}", "experimento"):
            self.assertEqual(self.mini(repo, "cat-file", "-p", rev)[0], self.git(repo, "cat-file", "-p", rev))
        short = self.git(repo, "rev-parse", "--short=6", "HEAD").strip()
        self.assertEqual(self.mini(repo, "cat-file", "-t", short)[0], "commit\n")

    def test_status_matches_git(self):
        repo = self.build()

        def same():
            ours = self.mini(repo, "status", "--porcelain")[0]
            self.assertEqual(ours, self.git(repo, "status", "--porcelain"))
            return ours

        self.assertEqual(same(), "")
        self.write(repo, "a.txt", "mudou\n")                          # modificado
        os.chmod(os.path.join(repo, "pasta", "b.txt"), 0o755)         # só o modo
        os.unlink(os.path.join(repo, "pasta", "exec.sh"))             # apagado
        self.write(repo, "solto.txt", "?\n")                         # não rastreado
        self.write(repo, "nova pasta/x.txt", "x\n")                  # pasta inteira nova
        self.write(repo, "nova pasta/sub/y.txt", "y\n")
        self.write(repo, "pasta/novo.txt", "z\n")                    # novo dentro de pasta rastreada
        self.write(repo, 'aspas".txt', "q\n")
        same()
        self.mini(repo, "add", "a.txt", "pasta/novo.txt")
        self.write(repo, "pasta/novo.txt", "z2\n")                   # AM
        same()
        self.mini(repo, "add", "pasta")                              # registra o apagado
        os.unlink(os.path.join(repo, "a.txt"))                        # MD
        same()
        self.mini(repo, "add", "relatório final.md")
        os.unlink(os.path.join(repo, "relatório final.md"))
        os.symlink("solto.txt", os.path.join(repo, "relatório final.md"))   # tipo mudou
        same()

    def test_gitignore(self):
        repo = self.build()
        self.write(repo, ".gitignore", "*.log\n/build/\ntemp*\n!temp-manter.txt\n")
        self.write(repo, "erro.log", "x")
        self.write(repo, "pasta/fundo.log", "x")
        self.write(repo, "build/saida.o", "x")
        self.write(repo, "src/build/manter.txt", "x")      # /build só vale na raiz
        self.write(repo, "temp1.txt", "x")
        self.write(repo, "temp-manter.txt", "x")
        self.write(repo, "docs/.gitignore", "rascunho/\n*.tmp\n")
        self.write(repo, "docs/rascunho/a.md", "x")
        self.write(repo, "docs/final.md", "x")
        self.write(repo, "docs/x.tmp", "x")
        self.write(repo, "so-ignorados/a.log", "x")        # pasta com só ignorados: some
        with open(os.path.join(repo, ".git", "info", "exclude"), "w") as f:
            f.write("segredo.txt\n")
        self.write(repo, "segredo.txt", "x")
        ours = self.mini(repo, "status", "--porcelain")[0]
        self.assertEqual(ours, self.git(repo, "status", "--porcelain"))
        self.assertNotIn("erro.log", ours)
        self.assertIn("temp-manter.txt", ours)
        out, err = self.mini(repo, "add", "erro.log", expect=1)
        self.assertIn(".gitignore", err)
        self.mini(repo, "add", "-f", "erro.log")
        self.assertEqual(self.mini(repo, "status", "--porcelain")[0], self.git(repo, "status", "--porcelain"))


@unittest.skipUnless(HAS_GIT, "git não está instalado")
class MiniReadsGitTest(RepoTestCase):
    """O mini-git lê o repositório criado pelo git (objetos soltos, índice e refs)."""

    def test_read_git_repo(self):
        repo = self.new_repo(tool="git")
        self.write(repo, "x.txt", "x\n")
        self.write(repo, "d/y.txt", "y\n")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-q", "-m", "do git")
        self.git(repo, "tag", "v1")
        self.git(repo, "checkout", "-q", "-b", "outro")
        self.write(repo, "d/y.txt", "y2\n")
        self.git(repo, "commit", "-q", "-am", "segundo do git\n\ncorpo")
        self.assertEqual(self.mini(repo, "log")[0], self.git(repo, "log", "--no-decorate"))
        self.assertEqual(self.mini(repo, "status", "--porcelain")[0], "")
        self.assertEqual(self.mini(repo, "cat-file", "-p", "v1")[0], self.git(repo, "cat-file", "-p", "v1"))
        objects = []
        for folder in os.listdir(os.path.join(repo, ".git", "objects")):
            if len(folder) == 2:
                objects += [folder + name for name in os.listdir(os.path.join(repo, ".git", "objects", folder))]
        self.assertGreaterEqual(len(objects), 7)
        for sha in objects:
            self.assertEqual(self.mini(repo, "cat-file", "-t", sha)[0], self.git(repo, "cat-file", "-t", sha))
            ours = self.mini(repo, "cat-file", "-p", sha)[0].encode("utf-8", "surrogateescape")
            self.assertEqual(ours, self.git_bytes(repo, "cat-file", "-p", sha))
        # Commit do mini-git em cima do histórico do git.
        self.write(repo, "x.txt", "x3\n")
        self.mini(repo, "add", "x.txt")
        self.mini(repo, "commit", "-m", "do mini-git")
        self.assert_fsck_clean(repo)
        self.assertEqual(self.git(repo, "log", "--format=%s"), "do mini-git\nsegundo do git\ndo git\n")
        self.assertEqual(self.git(repo, "status", "--porcelain"), "")

    def test_packfiles_are_reported(self):
        repo = self.new_repo(tool="git")
        self.write(repo, "x.txt", "x\n")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-q", "-m", "x")
        self.git(repo, "gc", "-q")
        _, err = self.mini(repo, "log", expect=1)
        self.assertIn("packfile", err)


@unittest.skipUnless(HAS_GIT, "git não está instalado")
class CheckoutTest(RepoTestCase):
    def test_switch_branches(self):
        repo = self.new_repo()
        self.write(repo, "comum.txt", "base\n")
        self.write(repo, "so-main/a.txt", "a\n")
        self.mini(repo, "add", ".")
        self.mini(repo, "commit", "-m", "base")
        out, _ = self.mini(repo, "checkout", "-b", "recurso")
        self.assertIn("recurso", out)
        os.unlink(os.path.join(repo, "so-main", "a.txt"))
        self.write(repo, "comum.txt", "mudado no recurso\n")
        self.write(repo, "novo/b.sh", "#!/bin/sh\n", mode=0o755)
        self.mini(repo, "add", ".")
        self.mini(repo, "commit", "-m", "recurso")
        self.assertEqual(self.git(repo, "symbolic-ref", "HEAD"), "refs/heads/recurso\n")

        self.mini(repo, "checkout", "main")
        self.assertEqual(self.git(repo, "symbolic-ref", "HEAD"), "refs/heads/main\n")
        with open(os.path.join(repo, "comum.txt")) as f:
            self.assertEqual(f.read(), "base\n")
        self.assertTrue(os.path.exists(os.path.join(repo, "so-main", "a.txt")))
        self.assertFalse(os.path.exists(os.path.join(repo, "novo")))      # pasta vazia removida
        self.assertEqual(self.git(repo, "status", "--porcelain"), "")

        self.mini(repo, "checkout", "recurso")
        self.assertTrue(os.access(os.path.join(repo, "novo", "b.sh"), os.X_OK))
        self.assertFalse(os.path.exists(os.path.join(repo, "so-main")))
        self.assertEqual(self.git(repo, "status", "--porcelain"), "")
        self.assertEqual(self.mini(repo, "branch")[0], "  main\n* recurso\n")

        # HEAD destacado num commit.
        first = self.git(repo, "rev-parse", "main").strip()
        self.mini(repo, "checkout", first[:8])
        self.assertEqual(self.git(repo, "rev-parse", "HEAD").strip(), first)
        with open(os.path.join(repo, ".git", "HEAD")) as f:
            self.assertEqual(f.read().strip(), first)
        self.assertIn("HEAD destacado", self.mini(repo, "status")[0])
        self.assertEqual(self.git(repo, "status", "--porcelain"), "")

    def test_refuses_to_lose_changes(self):
        repo = self.new_repo()
        self.write(repo, "a.txt", "1\n")
        self.mini(repo, "add", ".")
        self.mini(repo, "commit", "-m", "um")
        self.mini(repo, "branch", "outro")
        self.write(repo, "b.txt", "2\n")
        self.mini(repo, "add", ".")
        self.mini(repo, "commit", "-m", "dois")

        self.write(repo, "a.txt", "mudança local\n")
        _, err = self.mini(repo, "checkout", "outro", expect=1)
        self.assertIn("mudanças locais", err)
        self.assertEqual(self.git(repo, "symbolic-ref", "HEAD"), "refs/heads/main\n")
        self.write(repo, "a.txt", "1\n")

        # Arquivo não rastreado que seria sobrescrito.
        self.mini(repo, "checkout", "outro")
        self.write(repo, "b.txt", "não rastreado\n")
        _, err = self.mini(repo, "checkout", "main", expect=1)
        self.assertIn("não rastreados seriam sobrescritos", err)
        with open(os.path.join(repo, "b.txt")) as f:
            self.assertEqual(f.read(), "não rastreado\n")


if __name__ == "__main__":
    unittest.main()
