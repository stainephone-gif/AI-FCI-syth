"""Сценарии, которые запускают и команды бота, и планировщик."""

from __future__ import annotations

import logging
from html import escape

from app.config import Settings
from app.db import Database
from app.ingest.collector import collect
from app.rank.ranker import RankFn, rank_candidates, top_ranked

log = logging.getLogger(__name__)


async def collect_and_rank(settings: Settings, db: Database, rank_fn: RankFn) -> str:
    """Сбор + ранжирование. Возвращает отчёт в HTML для Telegram."""
    c = await collect(
        db, freshness_hours=settings.freshness_hours, fetch_full_text=settings.fetch_full_text
    )
    r = await rank_candidates(
        db,
        rank_fn,
        prompts_dir=settings.prompts_dir,
        model_name=settings.rank_model,
        concurrency=settings.rank_concurrency,
        min_relevance=settings.rank_min_relevance,
    )
    top = await top_ranked(db, limit=10)
    lines = [escape(c.summary()), escape(r.summary()), ""]
    if not top:
        lines.append("Кандидатов выше порога нет.")
    for item, rk in top:
        flag = " ⚠️" if rk.needs_fact_check else ""
        lines.append(
            f'<b>{rk.relevance}</b>{flag} <a href="{escape(item.url)}">{escape(item.title)}</a>'
            f"\n<i>{escape(item.source.name)} · {escape(rk.topic)}</i> — "
            f"{escape(rk.audience_angle)}"
        )
    return "\n".join(lines)
