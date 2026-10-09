"""Regras de .gitignore.

Suporta o que aparece na prática: comentários, ``!`` para negar, ``/`` no
fim (só pastas), ``/`` no começo ou no meio (padrão ancorado na pasta do
.gitignore), ``*``, ``?``, ``[a-z]`` e ``**``. A ordem de precedência é a do
Git: o arquivo global (core.excludesFile), depois .git/info/exclude, depois
os .gitignore da raiz até a pasta mais funda; a última regra que casa vence.
"""

import os
import re

from .objects import decode_path


def _glob_to_regex(glob):
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i) and (i == 0 or glob[i - 1] == "/"):
            out.append("(?:.*/)?")          # zero ou mais pastas
            i += 3
            continue
        if glob.startswith("**", i) and i + 2 == len(glob) and (i == 0 or glob[i - 1] == "/"):
            out.append(".*")                 # tudo dentro
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            # Um "]" logo depois de "[" (ou de "[!") é literal.
            j = i + 1
            if j < len(glob) and glob[j] in "!^":
                j += 1
            if j < len(glob) and glob[j] == "]":
                j += 1
            end = glob.find("]", j)
            if end < 0:
                out.append(re.escape(c))
            else:
                body = glob[i + 1:end]
                if body[:1] in ("!", "^"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = end
        elif c == "\\" and i + 1 < len(glob):
            i += 1
            out.append(re.escape(glob[i]))
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


class Rule:
    __slots__ = ("base", "negate", "dir_only", "anchored", "regex", "text")

    def __init__(self, line, base):
        self.text = line
        self.base = base                    # pasta do .gitignore ("" na raiz)
        self.negate = line.startswith("!")
        if self.negate:
            line = line[1:]
        elif line.startswith("\\!") or line.startswith("\\#"):
            line = line[1:]
        self.dir_only = line.endswith("/")
        line = line.rstrip("/")
        self.anchored = "/" in line
        line = line.lstrip("/")
        self.regex = re.compile(_glob_to_regex(line) + r"\Z", re.DOTALL)

    def matches(self, rel, is_dir):
        if self.dir_only and not is_dir:
            return False
        if self.base:
            if not rel.startswith(self.base + "/"):
                return False
            rel = rel[len(self.base) + 1:]
        if self.anchored:
            return self.regex.match(rel) is not None
        return self.regex.match(rel.rsplit("/", 1)[-1]) is not None


def parse_lines(text, base):
    rules = []
    for raw in text.splitlines():
        line = raw
        # Espaços no fim são ignorados, a não ser que escapados com "\ ".
        while line.endswith(" ") and not line.endswith("\\ "):
            line = line[:-1]
        if not line or line.startswith("#"):
            continue
        rules.append(Rule(line, base))
    return rules


def _read(path):
    try:
        with open(path, "rb") as f:
            return decode_path(f.read())
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError):
        return None


class IgnoreMatcher:
    def __init__(self, worktree, git_dir, config):
        self.worktree = worktree
        self.rules = []
        self.loaded = set()
        excludes = config.get("core.excludesfile")
        if excludes:
            excludes = os.path.expanduser(excludes)
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
            excludes = os.path.join(xdg, "git", "ignore")
        for path in (excludes, os.path.join(git_dir, "info", "exclude")):
            text = _read(path)
            if text:
                self.rules += parse_lines(text, "")

    def load_dir(self, rel_dir):
        """Carrega o .gitignore de uma pasta (uma vez só); as pastas mães vêm antes."""
        if rel_dir in self.loaded:
            return
        if rel_dir:
            self.load_dir(rel_dir.rsplit("/", 1)[0] if "/" in rel_dir else "")
        self.loaded.add(rel_dir)
        path = os.path.join(self.worktree, *rel_dir.split("/"), ".gitignore") if rel_dir \
            else os.path.join(self.worktree, ".gitignore")
        text = _read(path)
        if text:
            self.rules += parse_lines(text, rel_dir)

    def _match(self, rel, is_dir):
        ignored = False
        for rule in self.rules:
            if rule.matches(rel, is_dir):
                ignored = not rule.negate
        return ignored

    def is_ignored(self, rel, is_dir):
        """A entrada rel é ignorada, considerando só as regras dela (sem olhar as pastas mães)."""
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        self.load_dir(parent)
        return self._match(rel, is_dir)

    def is_ignored_path(self, rel, is_dir):
        """Como is_ignored, mas também ignora se alguma pasta mãe for ignorada."""
        parts = rel.split("/")
        for i in range(1, len(parts)):
            if self.is_ignored("/".join(parts[:i]), True):
                return True
        return self.is_ignored(rel, is_dir)
