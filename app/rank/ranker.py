"""Оценка кандидатов моделью. Один запрос на кандидата, параллельно с ограничением."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import Database, Item, Post, PostStatus, Ranking
from app.rank.prompt import PROMPT_VERSION, system_prompt, user_prompt
from app.rank.schemas import RankResult

log = logging.getLogger(__name__)

# Функция «система + материал → результат». Подменяется в тестах.
RankFn = Callable[[str, str], Awaitable[RankResult]]


@dataclass
class RankReport:
    ranked: int = 0
    failed: int = 0
    above_threshold: int = 0

    def summary(self) -> str:
        return f"Оценено: {self.ranked}, ошибок: {self.failed}, выше порога: {self.above_threshold}"


def make_claude_ranker(client: AsyncAnthropic, model: str) -> RankFn:
    async def rank(system: str, material: str) -> RankResult:
        response = await client.messages.parse(
            model=model,
            max_tokens=1024,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": material}],
            output_format=RankResult,
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Модель отказалась оценивать материал")
        return response.parsed_output

    return rank


async def rank_candidates(
    db: Database,
    rank_fn: RankFn,
    *,
    prompts_dir: str,
    model_name: str,
    concurrency: int,
    min_relevance: int,
) -> RankReport:
    report = RankReport()
    system = system_prompt(prompts_dir)

    async with db.session() as s:
        posts = (
            await s.scalars(
                select(Post)
                .where(Post.status == PostStatus.candidate)
                .options(selectinload(Post.item).selectinload(Item.source))
            )
        ).all()

    sem = asyncio.Semaphore(concurrency)

    async def one(post: Post) -> None:
        item = post.item
        if item is None:
            return
        material = user_prompt(
            title=item.title,
            source_name=item.source.name,
            source_note=item.source.note,
            text=item.text,
        )
        async with sem:
            try:
                result = await rank_fn(system, material)
            except Exception as exc:  # noqa: BLE001 - одна ошибка не останавливает батч
                report.failed += 1
                log.warning("Ранжирование %s: %s", item.id, exc)
                return
        async with db.session() as s:
            s.add(
                Ranking(
                    item_id=item.id,
                    relevance=result.relevance,
                    audience_angle=result.audience_angle,
                    topic=result.topic,
                    needs_fact_check=result.needs_fact_check,
                    model=f"{model_name}/{PROMPT_VERSION}",
                )
            )
            db_post = await s.get(Post, post.id)
            if db_post and db_post.can_move_to(PostStatus.ranked):
                db_post.status = PostStatus.ranked
            await s.commit()
        report.ranked += 1
        if result.relevance >= min_relevance:
            report.above_threshold += 1

    await asyncio.gather(*(one(p) for p in posts))
    log.info(report.summary())
    return report


async def top_ranked(db: Database, limit: int) -> list[tuple[Item, Ranking]]:
    """Лучшие кандидаты в статусе ranked, для дайджеста и команды /collect."""
    async with db.session() as s:
        rows = await s.execute(
            select(Item, Ranking)
            .join(Ranking, Ranking.item_id == Item.id)
            .join(Post, Post.item_id == Item.id)
            .where(Post.status == PostStatus.ranked)
            .options(selectinload(Item.source))
            .order_by(Ranking.relevance.desc(), Item.published_at.desc())
            .limit(limit)
        )
        return [(item, ranking) for item, ranking in rows.all()]
