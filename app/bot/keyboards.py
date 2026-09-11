"""Кнопки редакторского чата. Callback подписан, чтобы нельзя было одобрить пост подделкой."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PREFIX = "p"
SEP = "|"  # слот времени содержит двоеточие, поэтому разделитель другой
SIG_LEN = 12


@dataclass(frozen=True, slots=True)
class Action:
    post_id: int
    action: str  # pub:<slot> | time | edit | reject
    arg: str = ""


class CallbackSigner:
    def __init__(self, secret: str) -> None:
        self._key = hashlib.sha256(secret.encode()).digest()

    def _sig(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()[:SIG_LEN]

    def pack(self, a: Action) -> str:
        payload = SEP.join((PREFIX, str(a.post_id), a.action, a.arg))
        data = f"{payload}{SEP}{self._sig(payload)}"
        if len(data.encode()) > 64:
            raise ValueError("callback_data длиннее 64 байт")
        return data

    def unpack(self, data: str) -> Action | None:
        try:
            prefix, post_id, action, arg, sig = data.split(SEP, 4)
            payload = SEP.join((prefix, post_id, action, arg))
            if prefix != PREFIX or not hmac.compare_digest(sig, self._sig(payload)):
                return None
            return Action(post_id=int(post_id), action=action, arg=arg)
        except ValueError:
            return None


def review_keyboard(signer: CallbackSigner, post_id: int, slots: list[str]) -> InlineKeyboardMarkup:
    row1 = [
        InlineKeyboardButton(
            text=f"Опубликовать {slot}",
            callback_data=signer.pack(Action(post_id, "pub", slot)),
        )
        for slot in slots
    ]
    row2 = [
        InlineKeyboardButton(
            text="Другое время", callback_data=signer.pack(Action(post_id, "time"))
        ),
        InlineKeyboardButton(
            text="Отредактировать", callback_data=signer.pack(Action(post_id, "edit"))
        ),
        InlineKeyboardButton(
            text="Отклонить", callback_data=signer.pack(Action(post_id, "reject"))
        ),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row1, row2])


def confirm_keyboard(signer: CallbackSigner, post_id: int, slot: str) -> InlineKeyboardMarkup:
    """Второе одобрение для поста с ⚠️: нажимает другой редактор."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Подтвердить публикацию {slot} ⚠️",
                    callback_data=signer.pack(Action(post_id, "confirm", slot)),
                ),
                InlineKeyboardButton(
                    text="Отклонить", callback_data=signer.pack(Action(post_id, "reject"))
                ),
            ]
        ]
    )
