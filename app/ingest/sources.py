"""Загрузка списка источников из sources.yaml в базу."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from sqlalchemy import select

from app.db import Database, Source
from app.db.models import SourceKind

log = logging.getLogger(__name__)


def read_sources_file(path: str | Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sources = data.get("sources", [])
    for s in sources:
        for key in ("name", "kind", "url"):
            if key not in s:
                raise ValueError(f"В источнике {s} нет поля {key}")
        SourceKind(s["kind"])  # проверка, что kind допустимый
    return sources


async def sync_sources(db: Database, path: str | Path) -> int:
    """Добавляет новые источники, обновляет url/note/enabled у существующих. Возвращает число."""
    wanted = read_sources_file(path)
    async with db.session() as s:
        existing = {src.name: src for src in (await s.scalars(select(Source))).all()}
        for w in wanted:
            src = existing.get(w["name"])
            if src is None:
                src = Source(name=w["name"])
                s.add(src)
            src.kind = SourceKind(w["kind"])
            src.url = w["url"]
            src.note = w.get("note", "")
            src.enabled = bool(w.get("enabled", True))
        await s.commit()
    log.info("Источники синхронизированы: %d", len(wanted))
    return len(wanted)
