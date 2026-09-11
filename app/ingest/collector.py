"""Сбор кандидатов: источники → фиды → дедупликация → items и posts(candidate)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import Database, Item, Post, PostStatus, Source
from app.db.models import SourceKind
from app.ingest import feeds
from app.ingest.normalize import normalize_url, title_hash, url_hash

log = logging.getLogger(__name__)


@dataclass
class CollectReport:
    sources_polled: int = 0
    entries_seen: int = 0
    new_items: int = 0
    duplicates: int = 0
    stale: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        line = (
            f"Опрошено источников: {self.sources_polled}, записей: {self.entries_seen}, "
            f"новых: {self.new_items}, дублей: {self.duplicates}, устаревших: {self.stale}"
        )
        if self.errors:
            line += "\nОшибки:\n" + "\n".join(f"  {e}" for e in self.errors)
        return line


async def collect(db: Database, *, freshness_hours: int, fetch_full_text: bool) -> CollectReport:
    report = CollectReport()
    cutoff = datetime.now(UTC) - timedelta(hours=freshness_hours)

    async with db.session() as s:
        sources = (await s.scalars(select(Source).where(Source.enabled.is_(True)))).all()

    async with feeds.make_client() as client:
        for src in sources:
            if src.kind not in (SourceKind.rss, SourceKind.arxiv):
                continue  # html-парсеры появятся в v1
            try:
                entries = await feeds.fetch_feed(client, src.url)
            except Exception as exc:  # noqa: BLE001 - один сломанный фид не должен ронять сбор
                report.errors.append(f"{src.name}: {exc}")
                log.warning("Источник %s: %s", src.name, exc)
                continue
            report.sources_polled += 1
            report.entries_seen += len(entries)

            for e in entries:
                if e.published_at and e.published_at < cutoff:
                    report.stale += 1
                    continue
                stored = await _store_entry(db, src, e, client, fetch_full_text)
                if stored is None:
                    report.duplicates += 1
                else:
                    report.new_items += 1

            async with db.session() as s:
                db_src = await s.get(Source, src.id)
                if db_src:
                    db_src.last_polled_at = datetime.now(UTC)
                    await s.commit()

    log.info(report.summary())
    return report


async def _store_entry(db, src, e, client, fetch_full_text) -> Item | None:
    uh = url_hash(e.url)
    th = title_hash(e.title)
    async with db.session() as s:
        if await s.scalar(select(Item.id).where(Item.url_hash == uh)):
            return None
        # Тот же заголовок из другого издания за последние дни — дубль, но ссылку сохраняем.
        twin_id = await s.scalar(
            select(Item.id).where(Item.title_hash == th, Item.duplicate_of_id.is_(None))
        )

        text = feeds.html_to_text(e.summary)
        if twin_id is None and fetch_full_text and src.kind == SourceKind.rss:
            full = await feeds.fetch_full_text(client, e.url)
            if full and len(full) > len(text):
                text = full

        item = Item(
            source_id=src.id,
            url=normalize_url(e.url),
            url_hash=uh,
            title=e.title[:500],
            title_hash=th,
            text=text,
            published_at=e.published_at,
            duplicate_of_id=twin_id,
        )
        s.add(item)
        await s.flush()
        if twin_id is None:
            s.add(Post(item_id=item.id, status=PostStatus.candidate))
        await s.commit()
        return item if twin_id is None else None
