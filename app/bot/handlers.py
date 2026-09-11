"""Команды и кнопки редакторского чата."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.bot.fsm import EditFlow
from app.bot.keyboards import CallbackSigner, confirm_keyboard
from app.bot.review import send_review
from app.config import Settings
from app.db import Database, Post, Source
from app.db.models import PostStatus
from app.draft import service
from app.draft.writer import WriteFn
from app.ingest.manual import intake
from app.pipeline import collect_and_rank
from app.publish.publisher import Publisher
from app.publish.scheduler import Scheduler
from app.rank.ranker import RankFn

log = logging.getLogger(__name__)
router = Router(name="editorial")

HELP = (
    "Синтетическая редакция. Команды:\n"
    "/status — состояние сервиса\n"
    "/collect — собрать кандидатов из источников и оценить их\n"
    "/digest — написать черновики для лучших кандидатов и прислать сюда\n"
    "/post текст — пост из вашего сообщения: событие, набор, работа студентов\n"
    "/queue — что стоит в очереди на публикацию\n"
    "/cancel N — снять пост #N из очереди, вернуть кнопки\n"
    "/publish N — опубликовать одобренный пост #N прямо сейчас\n"
    "/help — эта справка\n\n"
    "Правка: ответьте на сообщение с черновиком текстом замечания.\n"
    "Ничего не публикуется без нажатой кнопки."
)


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("whoami"))
async def cmd_whoami(message: Message, settings: Settings) -> None:
    """Доступна всем: нужна один раз, чтобы заполнить EDITOR_IDS и EDITOR_CHAT_ID."""
    uid = message.from_user.id if message.from_user else None
    role = "редактор" if settings.is_editor(uid) else "не в списке редакторов"
    lines = [
        f"Ваш id: <code>{uid}</code> ({role})",
        f"Id этого чата: <code>{message.chat.id}</code>",
    ]
    origin = getattr(message.reply_to_message, "forward_origin", None)
    chat = getattr(origin, "chat", None)
    if chat is not None and getattr(chat, "type", "") == "channel":
        lines.append(f"Id канала, откуда переслан пост: <code>{chat.id}</code>")
    lines.append("Скопируйте числа в .env: EDITOR_IDS, EDITOR_CHAT_ID, CHANNEL_ID.")
    await message.reply("\n".join(lines))


@router.message(Command("status"))
async def cmd_status(
    message: Message, settings: Settings, db: Database, scheduler: Scheduler
) -> None:
    async with db.session() as s:
        sources_total = await s.scalar(select(func.count(Source.id)))
        sources_on = await s.scalar(select(func.count(Source.id)).where(Source.enabled.is_(True)))
        by_status = dict(
            (await s.execute(select(Post.status, func.count(Post.id)).group_by(Post.status))).all()
        )
    jobs = (
        "\n".join(
            f"  {j.id}: {j.next_run_time:%d.%m %H:%M}" if j.next_run_time else f"  {j.id}: —"
            for j in scheduler.jobs()
        )
        or "  нет задач"
    )
    posts = ", ".join(f"{st.value} {n}" for st, n in by_status.items()) or "пока нет"
    db_kind = settings.database_url.split("+", 1)[0].split(":", 1)[0]
    await message.answer(
        f"Редакторов в allowlist: {len(settings.editor_ids)}\n"
        f"Источников: {sources_on} включено из {sources_total}\n"
        f"Посты по статусам: {posts}\n"
        f"База: {db_kind}, часовой пояс: {settings.tz}\n"
        f"Задачи планировщика:\n{jobs}"
    )


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
    await message.answer(
        "\n".join(
            f"#{p.id} {p.status.value} "
            + (f"на {p.scheduled_at:%d.%m %H:%M}" if p.scheduled_at else "время не задано")
            for p in rows
        )
    )


@router.message(Command("post"))
async def cmd_post(
    message: Message,
    bot: Bot,
    settings: Settings,
    db: Database,
    write_fn: WriteFn,
    signer: CallbackSigner,
) -> None:
    text = (message.text or "").split(maxsplit=1)
    if len(text) < 2 or len(text[1].strip()) < 10:
        await message.reply(
            "Напишите, о чём пост, одним сообщением после команды. "
            "Например: /post в четверг семинар с ИТМО про агентов, ссылка на регистрацию: …"
        )
        return
    pid = await intake(
        db,
        text[1],
        message_id=message.message_id,
        actor=message.from_user.id,
        fetch_link=settings.fetch_full_text,
    )
    await message.reply(f"Пишу пост #{pid}. Даты проверьте отдельно, они в конце черновика.")
    try:
        await service.draft_for_post(db, write_fn, pid, settings)
    except Exception as exc:  # noqa: BLE001
        log.exception("Ручной пост %s", pid)
        await message.reply(f"#{pid}: черновик не получился: {str(exc)[:200]}")
        return
    await send_review(bot, message.chat.id, db, pid, signer, settings)


@router.message(Command("cancel"))
async def cmd_cancel(
    message: Message,
    bot: Bot,
    settings: Settings,
    db: Database,
    publisher: Publisher,
    signer: CallbackSigner,
) -> None:
    pid = _arg_int(message.text)
    if pid is None:
        await message.reply("Формат: /cancel 12")
        return
    if not await publisher.cancel(pid, message.from_user.id):
        await message.reply(f"#{pid} не в очереди.")
        return
    await message.reply(f"#{pid} снят с публикации. Ниже черновик с кнопками.")
    await send_review(bot, message.chat.id, db, pid, signer, settings)


@router.message(Command("publish"))
async def cmd_publish(message: Message, publisher: Publisher) -> None:
    pid = _arg_int(message.text)
    if pid is None:
        await message.reply("Формат: /publish 12")
        return
    publisher.scheduler.remove(Publisher.job_id(pid))
    r = await publisher.publish(pid)
    if r.outcome == "skipped":
        await message.reply(f"#{pid}: {r.reason}. Публикую только одобренные посты.")
    elif r.outcome == "failed":
        await message.reply(f"#{pid}: не удалось, {r.reason[:200]}")
    # об успехе публикатор сообщает сам


def _arg_int(text: str | None) -> int | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    try:
        return int(parts[1].lstrip("#"))
    except ValueError:
        return None


@router.message(Command("collect"))
async def cmd_collect(message: Message, settings: Settings, db: Database, rank_fn: RankFn) -> None:
    await message.answer("Собираю источники и оцениваю кандидатов, это займёт пару минут.")
    report = await collect_and_rank(settings, db, rank_fn)
    await message.answer(report, disable_web_page_preview=True)


@router.message(Command("digest"))
async def cmd_digest(
    message: Message,
    bot: Bot,
    settings: Settings,
    db: Database,
    write_fn: WriteFn,
    signer: CallbackSigner,
) -> None:
    await message.answer("Пишу черновики для лучших кандидатов.")
    n = await run_digest(bot, message.chat.id, settings, db, write_fn, signer)
    if n == 0:
        await message.answer("Кандидатов выше порога нет. Запустите /collect или подождите утра.")


async def run_digest(
    bot: Bot,
    chat_id: int,
    settings: Settings,
    db: Database,
    write_fn: WriteFn,
    signer: CallbackSigner,
) -> int:
    drafts = await service.draft_top(db, write_fn, settings)
    for d in drafts:
        async with db.session() as s:
            post_id = await s.scalar(select(Post.id).where(Post.draft_id == d.id))
        if post_id:
            await send_review(bot, chat_id, db, post_id, signer, settings)
    return len(drafts)


# --- кнопки -------------------------------------------------------------------


@router.callback_query(F.data.startswith("p|"))
async def on_action(
    cq: CallbackQuery,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    db: Database,
    signer: CallbackSigner,
    publisher: Publisher,
) -> None:
    action = signer.unpack(cq.data or "")
    if action is None:
        await cq.answer("Кнопка устарела или повреждена.", show_alert=True)
        return
    actor = cq.from_user.id
    msg = cq.message
    pid = action.post_id

    if action.action == "pub":
        when = service.next_slot(action.arg, settings.tz)
        d = await service.approve(db, pid, when, actor)
        await _after_decision(cq, bot, signer, pid, d, settings, publisher)
    elif action.action == "confirm":
        d = await service.confirm(db, pid, actor)
        await _after_decision(cq, bot, signer, pid, d, settings, publisher)
    elif action.action == "reject":
        d = await service.reject(db, pid, actor)
        await _after_decision(cq, bot, signer, pid, d, settings, publisher)
    elif action.action == "time":
        await state.set_state(EditFlow.time)
        await state.update_data(post_id=pid)
        await cq.answer()
        await msg.reply(f"Когда опубликовать #{pid}? Напишите «15:30» или «14.09 15:30».")
    elif action.action == "edit":
        await state.set_state(EditFlow.remark)
        await state.update_data(post_id=pid)
        await cq.answer()
        await msg.reply(f"Что поправить в #{pid}? Напишите замечание одним сообщением.")
    else:
        await cq.answer("Неизвестное действие.")


async def _after_decision(
    cq, bot, signer, pid, d: service.Decision, settings: Settings, publisher: Publisher
) -> None:
    msg = cq.message
    if d.outcome == "approved":
        await publisher.schedule(pid, d.scheduled_at)
        await cq.answer("В очереди.")
        await msg.edit_reply_markup(reply_markup=None)
        await msg.reply(f"#{pid} одобрен, публикация {d.scheduled_at:%d.%m в %H:%M}.")
    elif d.outcome == "needs_second":
        await cq.answer("Нужно подтверждение второго редактора.")
        await msg.edit_reply_markup(
            reply_markup=confirm_keyboard(signer, pid, f"{d.scheduled_at:%H:%M}")
        )
        await msg.reply(
            f"#{pid} помечен ⚠️: есть неподтверждённые факты. "
            f"Публикация {d.scheduled_at:%d.%m в %H:%M} после подтверждения другим редактором."
        )
    elif d.outcome == "rejected":
        await cq.answer("Отклонён.")
        await msg.edit_reply_markup(reply_markup=None)
        await msg.reply(f"#{pid} отклонён.")
    else:
        await cq.answer(d.reason or "Действие невозможно.", show_alert=True)


# --- правка и время текстом ----------------------------------------------------


@router.message(EditFlow.time, F.text)
async def on_time_text(
    message: Message,
    state: FSMContext,
    settings: Settings,
    db: Database,
    signer: CallbackSigner,
    publisher: Publisher,
) -> None:
    data = await state.get_data()
    pid = data.get("post_id")
    when = service.parse_time(message.text or "", settings.tz)
    if when is None:
        await message.reply("Не понял время. Формат: «15:30» или «14.09 15:30».")
        return
    await state.clear()
    d = await service.approve(db, pid, when, message.from_user.id)
    if d.outcome == "approved":
        await publisher.schedule(pid, when)
        await message.reply(f"#{pid} одобрен, публикация {when:%d.%m в %H:%M}.")
    elif d.outcome == "needs_second":
        await message.reply(
            f"#{pid} помечен ⚠️. Публикация {when:%d.%m в %H:%M} "
            "после подтверждения другим редактором.",
            reply_markup=confirm_keyboard(signer, pid, f"{when:%H:%M}"),
        )
    else:
        await message.reply(d.reason)


@router.message(EditFlow.remark, F.text)
async def on_remark_text(
    message: Message,
    state: FSMContext,
    settings: Settings,
    db: Database,
    bot: Bot,
    write_fn: WriteFn,
    signer: CallbackSigner,
) -> None:
    data = await state.get_data()
    await state.clear()
    await _revise_and_resend(message, data["post_id"], settings, db, bot, write_fn, signer)


@router.message(F.reply_to_message, F.text)
async def on_reply_to_draft(
    message: Message,
    settings: Settings,
    db: Database,
    bot: Bot,
    write_fn: WriteFn,
    signer: CallbackSigner,
) -> None:
    """Ответ текстом на сообщение с черновиком = замечание."""
    pid = await service.post_by_review_message(db, message.reply_to_message.message_id)
    if pid is None:
        return
    await _revise_and_resend(message, pid, settings, db, bot, write_fn, signer)


async def _revise_and_resend(message, pid, settings, db, bot, write_fn, signer) -> None:
    await message.reply(f"Правлю #{pid}.")
    try:
        await service.revise(db, write_fn, pid, message.text, str(message.from_user.id), settings)
    except service.WrongState as exc:
        await message.reply(f"Не получилось: {exc}")
        return
    await send_review(bot, message.chat.id, db, pid, signer, settings)
