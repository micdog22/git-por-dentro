"""Apoio para os testes: repositórios temporários, o mini-git em processo e o git real."""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from git_por_dentro import cli  # noqa: E402

HAS_GIT = shutil.which("git") is not None

IDENTITY = {
    "GIT_AUTHOR_NAME": "Ana Souza",
    "GIT_AUTHOR_EMAIL": "ana@exemplo.com.br",
    "GIT_COMMITTER_NAME": "Bruno Lima",
    "GIT_COMMITTER_EMAIL": "bruno@exemplo.com.br",
    "GIT_AUTHOR_DATE": "1700000000 -0300",
    "GIT_COMMITTER_DATE": "1700000100 -0300",
}

_ISOLATE = ("HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_DIR",
            "GIT_WORK_TREE", "GIT_INDEX_FILE", "EMAIL", "LANG", "LC_ALL", "TZ") + tuple(IDENTITY)


class RepoTestCase(unittest.TestCase):
    """Cria uma pasta temporária e isola o ambiente (configuração, identidade, idioma)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mini-git-teste-")
        self.saved_env = {k: os.environ.get(k) for k in _ISOLATE}
        self.saved_cwd = os.getcwd()
        home = os.path.join(self.tmp, "casa")
        os.makedirs(home)
        empty = os.path.join(home, "gitconfig")
        open(empty, "w").close()
        for k in _ISOLATE:
            os.environ.pop(k, None)
        os.environ.update({"HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"),
                           "GIT_CONFIG_GLOBAL": empty, "GIT_CONFIG_NOSYSTEM": "1",
                           "LC_ALL": "C", "TZ": "UTC"})
        os.environ.update(IDENTITY)

    def tearDown(self):
        os.chdir(self.saved_cwd)
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------- utilidades

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def write(self, repo, rel, content, mode=None):
        full = os.path.join(repo, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        with open(full, "wb") as f:
            f.write(data)
        if mode is not None:
            os.chmod(full, mode)
        return full

    def mini(self, repo, *args, stdin=None, expect=0):
        """Roda o mini-git dentro deste processo; devolve (saída, erros)."""
        old_out, old_err, old_in = sys.stdout, sys.stderr, sys.stdin
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="surrogateescape")
        err = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="surrogateescape")
        sys.stdout, sys.stderr = out, err
        if stdin is not None:
            sys.stdin = io.TextIOWrapper(io.BytesIO(stdin), encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            try:
                code = cli.main(list(args))
            except SystemExit as exc:
                code = exc.code
        finally:
            os.chdir(cwd)
            sys.stdout, sys.stderr, sys.stdin = old_out, old_err, old_in
        out.flush()
        err.flush()
        stdout = out.buffer.getvalue().decode("utf-8", "surrogateescape")
        stderr = err.buffer.getvalue().decode("utf-8", "surrogateescape")
        if expect is not None and code != expect:
            self.fail("mini-git %s saiu com %r (esperado %r)\nsaída: %s\nerros: %s"
                      % (" ".join(args), code, expect, stdout, stderr))
        return stdout, stderr

    def git(self, repo, *args, check=True):
        """Roda o git de verdade e devolve a saída como texto."""
        proc = subprocess.run(["git"] + list(args), cwd=repo, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        if check and proc.returncode != 0:
            self.fail("git %s falhou (%d): %s" % (" ".join(args), proc.returncode,
                                                  proc.stderr.decode("utf-8", "replace")))
        return proc.stdout.decode("utf-8", "surrogateescape")

    def git_bytes(self, repo, *args):
        return subprocess.run(["git"] + list(args), cwd=repo, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=True).stdout

    def new_repo(self, name="repo", tool="mini"):
        repo = self.path(name)
        os.makedirs(repo)
        if tool == "mini":
            self.mini(repo, "init")
        else:
            self.git(repo, "init", "-q")
        return repo

    def assert_fsck_clean(self, repo):
        proc = subprocess.run(["git", "fsck", "--strict", "--no-dangling"], cwd=repo,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        problems = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()
        self.assertEqual(problems, "", "git fsck --strict reclamou:\n" + problems)
