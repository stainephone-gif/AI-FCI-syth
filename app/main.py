"""Точка входа: бот и планировщик в одном процессе."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from app.bot.handlers import router
from app.bot.middleware import EditorsOnlyMiddleware
from app.config import Settings, load_settings, setup_logging
from app.db import Database
from app.ingest.sources import sync_sources
from app.publish.scheduler import Scheduler, job_collect, job_digest

log = logging.getLogger("app")


def build_dispatcher(settings: Settings, db: Database, scheduler: Scheduler) -> Dispatcher:
    dp = Dispatcher()
    dp.update.outer_middleware(EditorsOnlyMiddleware(settings.editor_ids))
    dp.include_router(router)
    # Эти объекты попадают в аргументы хендлеров по имени.
    dp["settings"] = settings
    dp["db"] = db
    dp["scheduler"] = scheduler
    return dp


def build_scheduler(settings: Settings) -> Scheduler:
    scheduler = Scheduler(settings.tz)
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

    scheduler = build_scheduler(settings)
    dp = build_dispatcher(settings, db, scheduler)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))

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
