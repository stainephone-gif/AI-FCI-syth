"""Allowlist: бот отвечает только редакторам. Чужие сообщения молча отбрасываются."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

log = logging.getLogger(__name__)


class EditorsOnlyMiddleware(BaseMiddleware):
    def __init__(self, editor_ids: frozenset[int]) -> None:
        self.editor_ids = editor_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.id not in self.editor_ids:
            # Ни ответа, ни логов с текстом: посторонний не должен узнать, что бот жив.
            log.debug("Отброшено событие от пользователя %s", user.id if user else None)
            return None
        return await handler(event, data)
