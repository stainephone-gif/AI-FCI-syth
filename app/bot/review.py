"""Отправка черновика в редакторский чат с кнопками."""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

from app.bot.keyboards import CallbackSigner, review_keyboard
from app.bot.telegram_html import strip_tags
from app.config import Settings
from app.db import Database
from app.draft import service
from app.draft.service import _load

log = logging.getLogger(__name__)


async def send_review(
    bot: Bot, chat_id: int, db: Database, post_id: int, signer: CallbackSigner, settings: Settings
) -> int:
    async with db.session() as s:
        post = await _load(s, post_id)
        text = service.render_review(post)
        card = post.draft.card_path if post.draft else None
    kb = review_keyboard(signer, post_id, settings.slots)
    if card and Path(card).exists():
        try:
            await bot.send_photo(chat_id, FSInputFile(card))
        except Exception as exc:  # noqa: BLE001 - карточка вторична, черновик важнее
            log.warning("Пост %s: карточка не отправлена: %s", post_id, exc)
    try:
        msg = await bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        # Разметка от модели не прошла проверку Telegram: показываем без тегов, но показываем.
        log.warning("Пост %s: Telegram отверг HTML (%s), шлю без разметки", post_id, exc)
        msg = await bot.send_message(
            chat_id, strip_tags(text), reply_markup=kb, disable_web_page_preview=True
        )
    await service.mark_in_review(db, post_id, msg.message_id)
    return msg.message_id
