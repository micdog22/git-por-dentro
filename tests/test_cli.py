"""Testes da linha de comando: mensagens, erros, diff, config, ramos e log."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from git_por_dentro import __version__  # noqa: E402

from helpers import RepoTestCase  # noqa: E402


class CliTest(RepoTestCase):
    def test_init(self):
        repo = self.path("novo")
        os.makedirs(repo)
        out, _ = self.mini(repo, "init")
        self.assertIn("Repositório Git vazio criado em", out)
        for part in ("HEAD", "config", "objects", "refs/heads", "refs/tags"):
            self.assertTrue(os.path.exists(os.path.join(repo, ".git", part)), part)
        with open(os.path.join(repo, ".git", "HEAD")) as f:
            self.assertEqual(f.read(), "ref: refs/heads/main\n")
        out, _ = self.mini(repo, "init")
        self.assertIn("nada mudou", out)
        other = self.path("outro")
        os.makedirs(other)
        self.mini(other, "init", "-b", "principal")
        with open(os.path.join(other, ".git", "HEAD")) as f:
            self.assertEqual(f.read(), "ref: refs/heads/principal\n")

    def test_errors(self):
        outside = self.path("fora")
        os.makedirs(outside)
        _, err = self.mini(outside, "status", expect=1)
        self.assertIn("não é um repositório git", err)
        repo = self.new_repo()
        _, err = self.mini(repo, "log", expect=1)
        self.assertIn("ainda não tem commits", err)
        _, err = self.mini(repo, "commit", "-m", "nada", expect=1)
        self.assertIn("nada para commitar", err)
        _, err = self.mini(repo, "add", "fantasma.txt", expect=1)
        self.assertIn("não corresponde a nenhum arquivo", err)
        self.write(repo, "a.txt", "a\n")
        self.mini(repo, "add", "a.txt")
        _, err = self.mini(repo, "commit", "-m", "   ", expect=1)
        self.assertIn("mensagem de commit vazia", err)
        self.mini(repo, "commit", "-m", "a")
        _, err = self.mini(repo, "commit", "-m", "de novo", expect=1)
        self.assertIn("nada para commitar", err)
        self.mini(repo, "commit", "--allow-empty", "-m", "vazio de propósito")
        _, err = self.mini(repo, "cat-file", "-p", "naoexiste", expect=1)
        self.assertIn("revisão desconhecida", err)
        _, err = self.mini(repo, "cat-file", "-p", "HEAD~5", expect=1)
        self.assertIn("ancestrais", err)
        _, err = self.mini(repo, "branch", "nome inválido", expect=1)
        self.assertIn("nome de ramo inválido", err)
        _, err = self.mini(repo, "add", "../fora", expect=1)
        self.assertIn("fora do repositório", err)
        # Sem identidade.
        for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            del os.environ[k]
        self.write(repo, "b.txt", "b\n")
        self.mini(repo, "add", "b.txt")
        _, err = self.mini(repo, "commit", "-m", "b", expect=1)
        self.assertIn("diga quem você é", err)
        self.mini(repo, "config", "user.name", "Dani Rocha")
        self.mini(repo, "config", "user.email", "dani@exemplo.com.br")
        self.assertEqual(self.mini(repo, "config", "user.name")[0], "Dani Rocha\n")
        self.mini(repo, "commit", "-m", "b")
        out, _ = self.mini(repo, "cat-file", "-p", "HEAD")
        self.assertIn("author Dani Rocha <dani@exemplo.com.br> 1700000000 -0300", out)

    def test_usage_errors_in_portuguese(self):
        repo = self.new_repo()
        _, err = self.mini(repo, "cat-file", "HEAD", expect=2)
        self.assertIn("uso:", err)
        self.assertIn("erro:", err)
        _, err = self.mini(repo, "comando-que-nao-existe", expect=2)
        self.assertIn("opção inválida", err)
        out, _ = self.mini(repo, "--version")
        self.assertEqual(out.strip(), "mini-git " + __version__)
        out, _ = self.mini(repo, "add", "-h")
        self.assertIn("põe arquivos no índice", out)

    def test_diff(self):
        repo = self.new_repo()
        self.write(repo, "texto.txt", "linha 1\nlinha 2\nlinha 3\nlinha 4\nlinha 5\n")
        self.write(repo, "some.txt", "vai sumir\n")
        self.write(repo, "exec.sh", "echo\n")
        self.write(repo, "binario.dat", b"\x00\x01\x02")
        self.write(repo, "sem-fim.txt", "sem quebra")
        self.mini(repo, "add", ".")
        self.mini(repo, "commit", "-m", "base")
        self.assertEqual(self.mini(repo, "diff")[0], "")

        self.write(repo, "texto.txt", "linha 1\nlinha dois\nlinha 3\nlinha 4\nlinha 5\nlinha 6\n")
        os.unlink(os.path.join(repo, "some.txt"))
        os.chmod(os.path.join(repo, "exec.sh"), 0o755)
        self.write(repo, "binario.dat", b"\x00\x09")
        self.write(repo, "sem-fim.txt", "sem quebra, mudou")
        out, _ = self.mini(repo, "diff")
        expected = (
            "diff --git a/binario.dat b/binario.dat\n"
            "index 8352675..d0c3599 100644\n"
            "Binary files a/binario.dat and b/binario.dat differ\n"
            "diff --git a/exec.sh b/exec.sh\n"
            "old mode 100644\n"
            "new mode 100755\n"
            "diff --git a/sem-fim.txt b/sem-fim.txt\n"
            "index 91372d5..4281ded 100644\n"
            "--- a/sem-fim.txt\n"
            "+++ b/sem-fim.txt\n"
            "@@ -1 +1 @@\n"
            "-sem quebra\n"
            "\\ No newline at end of file\n"
            "+sem quebra, mudou\n"
            "\\ No newline at end of file\n"
            "diff --git a/some.txt b/some.txt\n"
            "deleted file mode 100644\n"
            "index 3bd690d..0000000\n"
            "--- a/some.txt\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-vai sumir\n"
            "diff --git a/texto.txt b/texto.txt\n"
            "index 5017e1b..bded1e3 100644\n"
            "--- a/texto.txt\n"
            "+++ b/texto.txt\n"
            "@@ -1,5 +1,6 @@\n"
            " linha 1\n"
            "-linha 2\n"
            "+linha dois\n"
            " linha 3\n"
            " linha 4\n"
            " linha 5\n"
            "+linha 6\n")
        self.assertEqual(out, expected)

        self.write(repo, "novo.txt", "novo\n")
        self.mini(repo, "add", "novo.txt")
        out, _ = self.mini(repo, "diff", "--cached")
        self.assertEqual(out, "diff --git a/novo.txt b/novo.txt\nnew file mode 100644\n"
                              "index 0000000..745c683\n--- /dev/null\n+++ b/novo.txt\n"
                              "@@ -0,0 +1 @@\n+novo\n")

    def test_log_and_branches(self):
        repo = self.new_repo()
        for i in range(1, 4):
            self.write(repo, "n.txt", "%d\n" % i)
            self.mini(repo, "add", ".")
            self.mini(repo, "commit", "-m", "commit número %d" % i)
        out, _ = self.mini(repo, "log", "--oneline")
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].endswith(" commit número 3"))
        self.assertEqual(len(self.mini(repo, "log", "-n", "2", "--oneline")[0].splitlines()), 2)
        self.assertEqual(self.mini(repo, "log", "--oneline", "HEAD~2")[0].splitlines(), lines[2:])
        out, _ = self.mini(repo, "log", "-n", "1")
        self.assertIn("Author: Ana Souza <ana@exemplo.com.br>", out)
        self.assertIn("Date:   Tue Nov 14 19:13:20 2023 -0300", out)
        self.assertIn("    commit número 3", out)

        self.mini(repo, "branch", "antigo", "HEAD~2")
        self.assertEqual(self.mini(repo, "branch")[0], "  antigo\n* main\n")
        _, err = self.mini(repo, "branch", "-d", "main", expect=1)
        self.assertIn("ramo atual", err)
        self.mini(repo, "branch", "-d", "antigo")             # já está na história do main
        self.assertEqual(self.mini(repo, "branch")[0], "* main\n")

        # Um ramo com commits próprios só sai com -D.
        self.mini(repo, "checkout", "-b", "experimento")
        self.write(repo, "n.txt", "experimental\n")
        self.mini(repo, "add", ".")
        self.mini(repo, "commit", "-m", "teste no ramo")
        self.mini(repo, "checkout", "main")
        _, err = self.mini(repo, "branch", "-d", "experimento", expect=1)
        self.assertIn("use -D", err)
        out, _ = self.mini(repo, "branch", "-D", "experimento")
        self.assertIn("Ramo experimento apagado", out)

    def test_status_human(self):
        repo = self.new_repo()
        out, _ = self.mini(repo, "status")
        self.assertIn("No ramo main", out)
        self.assertIn("Nenhum commit ainda", out)
        self.write(repo, "a.txt", "a\n")
        self.write(repo, "b.txt", "b\n")
        self.mini(repo, "add", "a.txt")
        out, _ = self.mini(repo, "status")
        self.assertIn("Mudanças prontas para o commit:\n\tnovo:        a.txt", out)
        self.assertIn("Arquivos não rastreados (use mini-git add):\n\tb.txt", out)
        self.mini(repo, "add", "b.txt")
        self.mini(repo, "commit", "-m", "x")
        out, _ = self.mini(repo, "status")
        self.assertIn("nada para commitar, a pasta de trabalho está limpa", out)


if __name__ == "__main__":
    unittest.main()
