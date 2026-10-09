"""Datas no formato do Git.

Dentro dos objetos, o Git guarda o instante como segundos desde 1970 (UTC)
seguidos do fuso de quem fez o commit: ``1700000000 -0300``. As variáveis
GIT_AUTHOR_DATE e GIT_COMMITTER_DATE aceitam esse formato e também ISO 8601
e RFC 2822.
"""

import re
import time
from datetime import datetime, timedelta, timezone

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_TZ = r"(?P<tz>Z|[+-]\d{2}:?\d{2})"
_RAW = re.compile(r"^@?(?P<ts>\d+)(?:\s+(?P<tz>[+-]\d{4}))?$")
_ISO = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})[T ](?P<h>\d{2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?"
    r"(?:\.\d+)?\s*" + _TZ + r"?$"
)
_RFC2822 = re.compile(
    r"^(?:[A-Za-z]{3},\s*)?(?P<d>\d{1,2})\s+(?P<mon>[A-Za-z]{3})\s+(?P<y>\d{4})\s+"
    r"(?P<h>\d{2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?\s+(?P<tz>[+-]\d{4})$"
)


def format_tz(minutes):
    """-180 vira "-0300"; 330 vira "+0530"."""
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return "%s%02d%02d" % (sign, minutes // 60, minutes % 60)


def parse_tz(text):
    """"+0530", "+05:30" e "Z" viram minutos a leste de Greenwich."""
    if text in (None, "", "Z"):
        return 0
    m = re.match(r"^([+-])(\d{2}):?(\d{2})$", text)
    if not m:
        raise ValueError("fuso horário inválido: %r" % text)
    minutes = int(m.group(2)) * 60 + int(m.group(3))
    return -minutes if m.group(1) == "-" else minutes


def parse_git_date(text):
    """Converte uma data aceita pelo Git em (segundos desde 1970, fuso em minutos)."""
    text = text.strip()
    m = _RAW.match(text)
    if m:
        return int(m.group("ts")), parse_tz(m.group("tz"))
    m = _ISO.match(text)
    if m:
        tz = parse_tz(m.group("tz"))
        when = datetime(int(m.group("y")), int(m.group("mo")), int(m.group("d")),
                        int(m.group("h")), int(m.group("mi")), int(m.group("s") or 0),
                        tzinfo=timezone(timedelta(minutes=tz)))
        return int(when.timestamp()), tz
    m = _RFC2822.match(text)
    if m and m.group("mon").title() in MONTHS:
        tz = parse_tz(m.group("tz"))
        when = datetime(int(m.group("y")), MONTHS.index(m.group("mon").title()) + 1, int(m.group("d")),
                        int(m.group("h")), int(m.group("mi")), int(m.group("s") or 0),
                        tzinfo=timezone(timedelta(minutes=tz)))
        return int(when.timestamp()), tz
    raise ValueError("data em formato desconhecido: %r (use, por exemplo, '1700000000 -0300' "
                     "ou '2023-11-14T19:13:20-03:00')" % text)


def now():
    """Agora, com o fuso horário local."""
    ts = int(time.time())
    return ts, time.localtime(ts).tm_gmtoff // 60


def format_default(ts, tz):
    """Formato do ``git log``: "Tue Nov 14 19:13:20 2023 -0300", no fuso do autor."""
    when = datetime.fromtimestamp(ts, timezone(timedelta(minutes=tz)))
    return "%s %s %d %02d:%02d:%02d %d %s" % (
        WEEKDAYS[when.weekday()], MONTHS[when.month - 1], when.day,
        when.hour, when.minute, when.second, when.year, format_tz(tz))
