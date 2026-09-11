"""Чтение RSS/Atom (в том числе arXiv API) и добор полного текста."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import mktime

import feedparser
import httpx
import trafilatura

log = logging.getLogger(__name__)

USER_AGENT = "ai-fci-syth/0.1 (+editorial bot; contact via channel admin)"
TIMEOUT = httpx.Timeout(20.0)
MAX_TEXT_CHARS = 20_000


@dataclass(slots=True)
class FeedEntry:
    url: str
    title: str
    summary: str
    published_at: datetime | None


def parse_feed(content: bytes | str) -> list[FeedEntry]:
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Не удалось разобрать фид: {parsed.bozo_exception}")
    entries: list[FeedEntry] = []
    for e in parsed.entries:
        link = getattr(e, "link", None) or ""
        title = (getattr(e, "title", None) or "").strip()
        if not link or not title:
            continue
        struct = e.get("published_parsed") if "published_parsed" in e else e.get("updated_parsed")
        published = datetime.fromtimestamp(mktime(struct), tz=UTC) if struct else None
        summary = getattr(e, "summary", None) or ""
        entries.append(FeedEntry(url=link, title=title, summary=summary, published_at=published))
    return entries


async def fetch_feed(client: httpx.AsyncClient, url: str) -> list[FeedEntry]:
    r = await client.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    r.raise_for_status()
    return parse_feed(r.content)


async def fetch_full_text(client: httpx.AsyncClient, url: str) -> str | None:
    """Полный текст статьи через trafilatura. None, если не получилось."""
    try:
        r = await client.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        log.info("Полный текст не скачан %s: %s", url, exc)
        return None
    text = trafilatura.extract(r.text, include_comments=False, include_tables=False)
    return text[:MAX_TEXT_CHARS] if text else None


def html_to_text(html: str) -> str:
    """Summary в фидах часто с HTML; для базы нужен плоский текст."""
    text = trafilatura.extract(f"<html><body>{html}</body></html>") if html else None
    if text:
        return text
    import re

    return re.sub(r"<[^>]+>", " ", html).strip()


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT)
