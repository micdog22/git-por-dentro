"""Aspas em nomes de arquivo, como o Git faz (core.quotePath = true).

Nomes com caracteres de controle, aspas, barra invertida ou bytes acima de
127 saem entre aspas, no estilo de uma string em C: "relat\\303\\263rio.txt".
O ``git status --porcelain`` também põe aspas em nomes com espaço.
"""

from .objects import encode_path

_ESCAPES = {7: "\\a", 8: "\\b", 9: "\\t", 10: "\\n", 11: "\\v", 12: "\\f", 13: "\\r",
            0x22: '\\"', 0x5C: "\\\\"}


def quote_path(path, quote_space=False):
    raw = encode_path(path)
    needs = False
    out = []
    for b in raw:
        if b in _ESCAPES:
            needs = True
            out.append(_ESCAPES[b])
        elif b < 0x20 or b == 0x7F or b >= 0x80:
            needs = True
            out.append("\\%03o" % b)
        else:
            if b == 0x20 and quote_space:
                needs = True
            out.append(chr(b))
    return '"%s"' % "".join(out) if needs else path
