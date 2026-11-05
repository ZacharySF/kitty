"""ATS-family grants (proposal-07 Step 4): narrow host+path patterns, one per family.

A family grant is the owner's one-time approval (Settings → Apply → Accounts, or
``kitty apply accounts grant``) for kitty to sign in, create a per-tenant account,
read that tenant's listed verification sender and reset its password on every
tenant whose URL matches the family's patterns. Nothing here reads the database
or configuration: this module only knows the patterns, so ``config.py`` can ask
whether a redirect stays inside the granted family without an import cycle.

Pattern grammar (deliberately small): ``https://HOST/PATH``. A host label that is
exactly ``*`` matches one or more DNS labels; a ``*`` inside a label matches one or
more label characters; the port is the default unless the pattern names one. A
``*`` in the path matches any characters; everything else is literal. URLs whose
path carries dot segments or encoded separators never match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Family:
    id: str
    label: str
    patterns: tuple[str, ...]


#: The families a grant can name, with the patterns from proposal-07 Step 4.
FAMILIES: dict[str, Family] = {family.id: family for family in (
    Family("workday", "Workday", (
        "https://*.myworkdayjobs.com/*",
        "https://*.wd*.myworkdaysite.com/*",
    )),
    Family("oracle", "Oracle Recruiting Cloud", (
        "https://*.fa.*.oraclecloud.com/hcmUI/CandidateExperience/*",
    )),
    Family("eightfold", "Eightfold", (
        "https://*.eightfold.ai/careers*",
    )),
    Family("icims", "iCIMS", (
        "https://careers-*.icims.com/*",
    )),
)}

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"


@lru_cache(maxsize=64)
def _compile(pattern: str) -> tuple[re.Pattern[str], int | None, re.Pattern[str]]:
    match = re.fullmatch(r"https://([^/:]+)(?::(\d+))?(/.*)", pattern)
    if match is None:
        raise ValueError(f"unsupported grant pattern {pattern!r}")
    host, port, path = match.groups()
    labels = []
    for label in host.split("."):
        if label == "*":
            labels.append(_LABEL + r"(?:\." + _LABEL + r")*")
        else:
            labels.append(r"[a-z0-9-]+".join(re.escape(part) for part in label.split("*")))
    path_re = ".*".join(re.escape(part) for part in path.split("*"))
    return re.compile(r"\.".join(labels)), int(port) if port else None, re.compile(path_re)


def pattern_matches(pattern: str, url: str) -> bool:
    """Whether `url` (HTTPS, no userinfo, canonical host) lies inside `pattern`."""
    from kitty.config import _authentication_origin

    origin = _authentication_origin(url)
    if origin is None:
        return False
    host, _, port = origin.removeprefix("https://").partition(":")
    path = urlsplit(url).path or "/"
    if re.search(r"%(?:2f|5c|2e)", path, re.I) or any(seg in (".", "..") for seg in path.split("/")):
        return False
    host_re, wanted_port, path_re = _compile(pattern)
    if (int(port) if port else None) != wanted_port:
        return False
    return bool(host_re.fullmatch(host) and path_re.fullmatch(path))


def family_of(url: str) -> str | None:
    """The family whose patterns contain `url`, granted or not."""
    for family in FAMILIES.values():
        if any(pattern_matches(pattern, url) for pattern in family.patterns):
            return family.id
    return None


def in_family(family: str, url: str) -> bool:
    """Whether `url` stays inside `family`'s grant patterns (redirects, emailed links)."""
    spec = FAMILIES.get(family)
    return spec is not None and any(pattern_matches(pattern, url) for pattern in spec.patterns)
