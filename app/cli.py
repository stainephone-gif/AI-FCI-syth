"""Отладка без Telegram: тот же конвейер, результат в терминал.

python -m app.cli collect          сбор и ранжирование, топ-10 в консоль
python -m app.cli digest           черновики для лучших кандидатов, текст в консоль
python -m app.cli draft 12         черновик для поста #12 (статус ranked или needs_edit)
python -m app.cli post "текст"     пост из сообщения редактора
python -m app.cli card "Заголовок" "Подзаголовок"   карточка в data/cards/test.png
python -m app.cli status           что в базе
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from html import unescape

from sqlalchemy import func, select

from app.bot.telegram_html import strip_tags
from app.config import Settings, setup_logging
from app.db import Database, Post
from app.draft import service
from app.draft.writer import make_claude_writer
from app.ingest.manual import intake
from app.ingest.sources import sync_sources
from app.pipeline import collect_and_rank
from app.rank.ranker import make_claude_ranker


def _plain(html: str) -> str:
    return unescape(strip_tags(html))


async def _setup(settings: Settings) -> Database:
    db = Database(settings.database_url)
    await db.create_all()
    await sync_sources(db, settings.sources_file)
    return db


def _client(settings: Settings):
    from anthropic import AsyncAnthropic

    if not settings.anthropic_api_key:
        sys.exit("ANTHROPIC_API_KEY пуст: заполните .env")
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


async def _print_post(db: Database, post_id: int) -> None:
    async with db.session() as s:
        post = await service._load(s, post_id)
        text = service.render_review(post)
        card = post.draft.card_path if post.draft else None
    print("=" * 72)
    print(_plain(text))
    if card:
        print(f"[карточка: {card}]")
    print("=" * 72)


async def cmd_collect(settings: Settings) -> None:
    db = await _setup(settings)
    rank_fn = make_claude_ranker(_client(settings), settings.rank_model)
    print(_plain(await collect_and_rank(settings, db, rank_fn)))
    await db.close()


async def cmd_digest(settings: Settings) -> None:
    db = await _setup(settings)
    write_fn = make_claude_writer(_client(settings), settings.draft_model, settings.draft_effort)
    drafts = await service.draft_top(db, write_fn, settings)
    if not drafts:
        print("Кандидатов выше порога нет. Сначала: python -m app.cli collect")
    for d in drafts:
        async with db.session() as s:
            pid = await s.scalar(select(Post.id).where(Post.draft_id == d.id))
        await _print_post(db, pid)
    await db.close()


async def cmd_draft(settings: Settings, post_id: int) -> None:
    db = await _setup(settings)
    write_fn = make_claude_writer(_client(settings), settings.draft_model, settings.draft_effort)
    await service.draft_for_post(db, write_fn, post_id, settings)
    await _print_post(db, post_id)
    await db.close()


async def cmd_post(settings: Settings, text: str) -> None:
    db = await _setup(settings)
    write_fn = make_claude_writer(_client(settings), settings.draft_model, settings.draft_effort)
    pid = await intake(db, text, message_id=0, actor=0, fetch_link=settings.fetch_full_text)
    await service.draft_for_post(db, write_fn, pid, settings)
    await _print_post(db, pid)
    await db.close()


async def cmd_card(settings: Settings, title: str, subtitle: str) -> None:
    from app.cards.render import CardData, render_png

    out = await render_png(
        CardData(title=title, subtitle=subtitle, topic="models", source_name="Тест"),
        f"{settings.cards_dir}/test.png",
        chromium_path=settings.chromium_path or None,
    )
    print(out)


async def cmd_status(settings: Settings) -> None:
    db = await _setup(settings)
    async with db.session() as s:
        rows = (
            await s.execute(select(Post.status, func.count(Post.id)).group_by(Post.status))
        ).all()
    print("Посты по статусам:", {st.value: n for st, n in rows} or "пусто")
    await db.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect")
    sub.add_parser("digest")
    sub.add_parser("status")
    sub.add_parser("draft").add_argument("post_id", type=int)
    sub.add_parser("post").add_argument("text")
    c = sub.add_parser("card")
    c.add_argument("title")
    c.add_argument("subtitle", nargs="?", default="")
    args = p.parse_args(argv)

    settings = Settings()  # type: ignore[call-arg]
    setup_logging(settings.log_level)
    match args.cmd:
        case "collect":
            asyncio.run(cmd_collect(settings))
        case "digest":
            asyncio.run(cmd_digest(settings))
        case "draft":
            asyncio.run(cmd_draft(settings, args.post_id))
        case "post":
            asyncio.run(cmd_post(settings, args.text))
        case "card":
            asyncio.run(cmd_card(settings, args.title, args.subtitle))
        case "status":
            asyncio.run(cmd_status(settings))


if __name__ == "__main__":
    main()
