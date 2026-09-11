"""Точка входа: бот и планировщик в одном процессе."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from anthropic import AsyncAnthropic

from app.bot.handlers import router
from app.bot.middleware import EditorsOnlyMiddleware
from app.config import Settings, load_settings, setup_logging
from app.db import Database
from app.ingest.sources import sync_sources
from app.pipeline import collect_and_rank
from app.publish.scheduler import Scheduler
from app.rank.ranker import RankFn, make_claude_ranker

log = logging.getLogger("app")


def build_dispatcher(
    settings: Settings, db: Database, scheduler: Scheduler, rank_fn: RankFn
) -> Dispatcher:
    dp = Dispatcher()
    dp.update.outer_middleware(EditorsOnlyMiddleware(settings.editor_ids))
    dp.include_router(router)
    # Эти объекты попадают в аргументы хендлеров по имени.
    dp["settings"] = settings
    dp["db"] = db
    dp["scheduler"] = scheduler
    dp["rank_fn"] = rank_fn
    return dp


def build_scheduler(
    settings: Settings, db: Database, bot: Bot | None, rank_fn: RankFn
) -> Scheduler:
    scheduler = Scheduler(settings.tz)

    async def job_collect() -> None:
        report = await collect_and_rank(settings, db, rank_fn)
        if bot and settings.editor_chat_id:
            await bot.send_message(settings.editor_chat_id, report, disable_web_page_preview=True)

    async def job_digest() -> None:
        log.info("digest: утренний дайджест ещё не реализован (шаг 12)")

    scheduler.add_cron("collect", settings.collect_cron, job_collect)
    scheduler.add_cron("digest", settings.digest_cron, job_digest)
    return scheduler


async def run() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    if not settings.editor_ids:
        raise SystemExit("EDITOR_IDS пуст: бот не будет отвечать никому. Заполните .env.")

    db = Database(settings.database_url)
    await db.create_all()
    await sync_sources(db, settings.sources_file)

    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY пуст: ранжирование не заработает. Заполните .env.")
    rank_fn = make_claude_ranker(
        AsyncAnthropic(api_key=settings.anthropic_api_key), settings.rank_model
    )
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    scheduler = build_scheduler(settings, db, bot, rank_fn)
    dp = build_dispatcher(settings, db, scheduler, rank_fn)

    scheduler.start()
    log.info("Запуск. Редакторов: %d", len(settings.editor_ids))
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        scheduler.shutdown()
        await bot.session.close()
        await db.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
