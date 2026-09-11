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
from pathlib import Path

from sqlalchemy import func, select

from app.bot.telegram_html import strip_tags
from app.config import Settings, setup_logging
from app.db import Database, Post
from app.draft import service
from app.ingest.manual import intake
from app.ingest.sources import sync_sources
from app.llm import build_model_functions, list_models
from app.pipeline import collect_and_rank


def _plain(html: str) -> str:
    return unescape(strip_tags(html))


async def _setup(settings: Settings) -> Database:
    db = Database(settings.database_url)
    await db.create_all()
    await sync_sources(db, settings.sources_file)
    return db


def _fns(settings: Settings):
    return build_model_functions(settings)


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
    rank_fn, _ = _fns(settings)
    print(_plain(await collect_and_rank(settings, db, rank_fn)))
    await db.close()


async def cmd_digest(settings: Settings) -> None:
    db = await _setup(settings)
    fns = _fns(settings)
    drafts = await service.draft_top(db, fns.write, settings, fns.fix_quotes)
    if not drafts:
        print("Кандидатов выше порога нет. Сначала: python -m app.cli collect")
    for d in drafts:
        async with db.session() as s:
            pid = await s.scalar(select(Post.id).where(Post.draft_id == d.id))
        await _print_post(db, pid)
    await db.close()


async def cmd_draft(settings: Settings, post_id: int) -> None:
    db = await _setup(settings)
    fns = _fns(settings)
    await service.draft_for_post(db, fns.write, post_id, settings, fns.fix_quotes)
    await _print_post(db, post_id)
    await db.close()


async def cmd_post(settings: Settings, text: str) -> None:
    db = await _setup(settings)
    fns = _fns(settings)
    pid = await intake(db, text, message_id=0, actor=0, fetch_link=settings.fetch_full_text)
    await service.draft_for_post(db, fns.write, pid, settings, fns.fix_quotes)
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


async def cmd_models(settings: Settings) -> None:
    """Проверка ключа: список моделей провайдера."""
    print(f"Провайдер из .env: {settings.model_provider}")
    names = await list_models(settings)
    print(f"Провайдер {settings.model_provider}, доступно моделей: {len(names)}")
    for n in names:
        print(" ", n)
    print(
        f"Выбрано: ранжирование {settings.rank_model_name}, черновики {settings.draft_model_name}"
    )


def cmd_reset(settings: Settings, yes: bool) -> None:
    """Удаляет SQLite-базу и папку карточек. Только для локальной отладки."""
    import shutil

    url = settings.database_url
    if not url.startswith("sqlite"):
        sys.exit("reset работает только с SQLite; для Postgres пересоздайте базу средствами СУБД")
    db_path = Path(url.split("///", 1)[-1])
    cards = Path(settings.cards_dir)
    if not yes:
        print(f"Будут удалены: {db_path} и папка {cards}. Повторите с флагом --yes.")
        return
    if db_path.exists():
        db_path.unlink()
        print(f"Удалена база {db_path}")
    if cards.exists():
        shutil.rmtree(cards)
        print(f"Удалена папка {cards}")
    print("Готово. Следующий шаг: python -m app.cli collect")


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
    sub.add_parser("models")
    sub.add_parser("reset").add_argument("--yes", action="store_true")
    sub.add_parser("draft").add_argument("post_id", type=int)
    sub.add_parser("post").add_argument("text")
    c = sub.add_parser("card")
    c.add_argument("title")
    c.add_argument("subtitle", nargs="?", default="")
    args = p.parse_args(argv)

    if not Path(".env").exists():
        print(
            "Файл .env не найден в текущей папке. Создайте его: copy .env.example .env, "
            "затем notepad .env. Проверьте, что файл не называется .env.txt."
        )
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
        case "models":
            asyncio.run(cmd_models(settings))
        case "reset":
            cmd_reset(settings, args.yes)


if __name__ == "__main__":
    main()
