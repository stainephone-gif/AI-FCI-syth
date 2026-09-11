"""Команды редакторского чата. Пока только служебные; рабочие появятся на шагах 11–15."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy import func, select

from app.config import Settings
from app.db import Database, Post, Source
from app.db.models import PostStatus
from app.publish.scheduler import Scheduler

router = Router(name="editorial")

HELP = (
    "Синтетическая редакция. Команды:\n"
    "/status — состояние сервиса\n"
    "/queue — что стоит в очереди на публикацию\n"
    "/collect — собрать кандидатов из источников (появится на шаге 11)\n"
    "/help — эта справка\n\n"
    "Ничего не публикуется без нажатой кнопки."
)


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("status"))
async def cmd_status(
    message: Message, settings: Settings, db: Database, scheduler: Scheduler
) -> None:
    async with db.session() as s:
        sources_total = await s.scalar(select(func.count(Source.id)))
        sources_on = await s.scalar(
            select(func.count(Source.id)).where(Source.enabled.is_(True))
        )
        by_status = dict(
            (await s.execute(select(Post.status, func.count(Post.id)).group_by(Post.status))).all()
        )
    jobs = "\n".join(
        f"  {j.id}: {j.next_run_time:%d.%m %H:%M}" if j.next_run_time else f"  {j.id}: —"
        for j in scheduler.jobs()
    ) or "  нет задач"
    posts = ", ".join(f"{st.value} {n}" for st, n in by_status.items()) or "пока нет"
    db_kind = settings.database_url.split("+", 1)[0].split(":", 1)[0]
    text = (
        f"Редакторов в allowlist: {len(settings.editor_ids)}\n"
        f"Источников: {sources_on} включено из {sources_total}\n"
        f"Посты по статусам: {posts}\n"
        f"База: {db_kind}, часовой пояс: {settings.tz}\n"
        f"Задачи планировщика:\n{jobs}"
    )
    await message.answer(text)


@router.message(Command("queue"))
async def cmd_queue(message: Message, db: Database) -> None:
    async with db.session() as s:
        rows = (
            await s.scalars(
                select(Post)
                .where(Post.status.in_([PostStatus.approved, PostStatus.scheduled]))
                .order_by(Post.scheduled_at)
            )
        ).all()
    if not rows:
        await message.answer("Очередь пуста.")
        return
    lines = [
        f"#{p.id} {p.status.value} "
        + (f"на {p.scheduled_at:%d.%m %H:%M}" if p.scheduled_at else "время не задано")
        for p in rows
    ]
    await message.answer("\n".join(lines))


@router.message(Command("collect"))
async def cmd_collect(message: Message) -> None:
    await message.answer("Сбор источников появится на шаге 11. Пока команда ничего не делает.")
