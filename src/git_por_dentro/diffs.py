"""Diferenças no formato unificado, com o difflib da biblioteca padrão.

O cabeçalho imita o do ``git diff``; os blocos (hunks) vêm do difflib, que
usa um algoritmo diferente do Git (Ratcliff-Obershelp em vez de Myers), então
em mudanças complicadas os blocos podem sair um pouco diferentes.
"""

import difflib

from .quoting import quote_path


def is_binary(data):
    """Mesma regra do Git: um byte zero nos primeiros 8000 bytes."""
    return b"\0" in data[:8000]


def _lines(data):
    return data.decode("utf-8", "surrogateescape").splitlines(keepends=True)


def _name(prefix, path):
    quoted = quote_path(prefix + path)
    # O Git põe um tab depois de nomes com espaço nas linhas ---/+++.
    return quoted + ("\t" if " " in path and not quoted.startswith('"') else "")


def file_diff(path, old_mode, old_sha, old_data, new_mode, new_sha, new_data):
    """Texto do diff de um arquivo. old_* ou new_* podem ser None (arquivo novo ou apagado)."""
    out = ["diff --git %s %s" % (quote_path("a/" + path), quote_path("b/" + path))]
    if old_mode is None:
        out.append("new file mode %s" % new_mode)
    elif new_mode is None:
        out.append("deleted file mode %s" % old_mode)
    elif old_mode != new_mode:
        out.append("old mode %s" % old_mode)
        out.append("new mode %s" % new_mode)
    if old_sha == new_sha:
        return "\n".join(out) + "\n"   # só mudou o modo

    short_old = (old_sha or "0" * 40)[:7]
    short_new = (new_sha or "0" * 40)[:7]
    same_mode = old_mode is not None and new_mode is not None and old_mode == new_mode
    out.append("index %s..%s%s" % (short_old, short_new, " " + old_mode if same_mode else ""))
    old_data = old_data or b""
    new_data = new_data or b""
    a_name = _name("a/", path) if old_mode is not None else "/dev/null"
    b_name = _name("b/", path) if new_mode is not None else "/dev/null"
    if is_binary(old_data) or is_binary(new_data):
        out.append("Binary files %s and %s differ" % (
            quote_path("a/" + path) if old_mode is not None else "/dev/null",
            quote_path("b/" + path) if new_mode is not None else "/dev/null"))
        return "\n".join(out) + "\n"

    out.append("--- " + a_name)
    out.append("+++ " + b_name)
    body = list(difflib.unified_diff(_lines(old_data), _lines(new_data), n=3, lineterm="\n"))[2:]
    text = "\n".join(out) + "\n"
    for line in body:
        if line.endswith("\n"):
            text += line
        else:
            text += line + "\n\\ No newline at end of file\n"
    return text
