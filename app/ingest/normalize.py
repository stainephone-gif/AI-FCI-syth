"""Нормализация ссылок и заголовков для дедупликации."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "yclid", "mc_", "ref", "source")
_WORD = re.compile(r"[^\w]+", re.UNICODE)
_STOP = frozenset(
    "the a an of to in on for and or with by from at is are new "
    "вот это как для и в на с о об".split()
)


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    # arXiv: abs/2409.01234v2 и abs/2409.01234 — одна работа
    path = re.sub(r"(/abs/\d{4}\.\d{4,5})v\d+$", r"\1", parts.path.rstrip("/"))
    # http и https считаем одной ссылкой
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def normalize_title(title: str) -> str:
    words = [w for w in _WORD.sub(" ", title.lower()).split() if w not in _STOP]
    return " ".join(words)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def url_hash(url: str) -> str:
    return sha(normalize_url(url))


def title_hash(title: str) -> str:
    return sha(normalize_title(title))
