"""Ручной ввод: сообщение редактора становится кандидатом, минуя сбор и ранжирование."""

from __future__ import annotations

import logging
import re

from sqlalchemy import select

from app.db import Database, Item, Post, PostStatus, Ranking, Source
from app.db.models import SourceKind
from app.ingest import feeds
from app.ingest.normalize import normalize_url, sha, title_hash, url_hash

log = logging.getLogger(__name__)

MANUAL_SOURCE = "Редакция"
_URL = re.compile(r"https?://[^\s<>\"')\]]+")


def extract_url(text: str) -> str | None:
    m = _URL.search(text)
    return m.group(0).rstrip(".,;:!?") if m else None


def make_title(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else "Без названия"
    first = _URL.sub("", first).strip(" -—:") or "Без названия"
    return first[:120]


async def _manual_source(db: Database) -> Source:
    async with db.session() as s:
        src = await s.scalar(select(Source).where(Source.name == MANUAL_SOURCE))
        if src is None:
            src = Source(
                name=MANUAL_SOURCE,
                kind=SourceKind.manual,
                url="",
                note="Сообщения редакторов: события лаборатории, наборы, студенческие работы.",
            )
            s.add(src)
            await s.commit()
        return src


async def intake(
    db: Database, text: str, *, message_id: int, actor: int, fetch_link: bool = True
) -> int:
    """Возвращает id поста в статусе ranked, готового к черновику."""
    src = await _manual_source(db)
    link = extract_url(text)
    linked_text = None
    if link and fetch_link:
        async with feeds.make_client() as client:
            linked_text = await feeds.fetch_full_text(client, link)

    body = text.strip()
    if linked_text:
        body += "\n\nТекст по ссылке:\n" + linked_text
    # Для дедупликации ссылка редактора, а без ссылки уникальный псевдо-URL сообщения.
    url = normalize_url(link) if link else f"manual://{actor}/{message_id}"
    uh = url_hash(url)

    async with db.session() as s:
        existing = await s.scalar(select(Item.id).where(Item.url_hash == uh))
        if existing:
            # Та же ссылка уже есть (например, из сбора): делаем новый кандидат на её основе,
            # но с уникальным хешем, чтобы сообщение редактора не потерялось.
            uh = sha(f"{url}#manual-{actor}-{message_id}")  # без нормализации: фрагмент важен
        item = Item(
            source_id=src.id,
            url=url,
            url_hash=uh,
            title=make_title(text),
            title_hash=title_hash(f"{make_title(text)} {message_id}"),
            text=body,
        )
        s.add(item)
        await s.flush()
        s.add(
            Ranking(
                item_id=item.id,
                relevance=100,
                audience_angle="сообщение редакции",
                topic="lab",
                needs_fact_check=False,
                model="manual",
            )
        )
        post = Post(item_id=item.id, status=PostStatus.ranked)
        s.add(post)
        await s.commit()
        log.info("Ручной ввод: пост %s от %s", post.id, actor)
        return post.id
