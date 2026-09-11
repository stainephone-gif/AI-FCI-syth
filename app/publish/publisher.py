"""Публикация одобренных постов в канал. Единственное место, где бот пишет в канал."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.bot.telegram_html import sanitize, strip_tags
from app.config import Settings
from app.db import Database, Event, Post, PostStatus
from app.publish.scheduler import PUBLISH_GRACE_SECONDS, Scheduler

log = logging.getLogger(__name__)

# Telegram: 4096 знаков на сообщение, 1024 на подпись к фото.
MAX_MESSAGE = 4096


class ChannelSender(Protocol):
    """Тонкая обёртка над Bot API, чтобы публикатор тестировался без Telegram."""

    async def send_text(self, channel_id: str, html: str) -> int: ...

    async def send_photo(self, channel_id: str, photo_path: str, caption_html: str) -> int: ...


class AiogramSender:
    def __init__(self, bot) -> None:
        self.bot = bot

    async def send_text(self, channel_id: str, html: str) -> int:
        from aiogram.exceptions import TelegramBadRequest

        try:
            msg = await self.bot.send_message(channel_id, html, disable_web_page_preview=True)
        except TelegramBadRequest as exc:
            if "parse" not in str(exc).lower() and "entit" not in str(exc).lower():
                raise
            log.warning("Канал отверг HTML (%s), публикую без разметки", exc)
            msg = await self.bot.send_message(
                channel_id, strip_tags(html), parse_mode=None, disable_web_page_preview=True
            )
        return msg.message_id

    async def send_photo(self, channel_id: str, photo_path: str, caption_html: str) -> int:
        from aiogram.types import FSInputFile

        msg = await self.bot.send_photo(channel_id, FSInputFile(photo_path), caption=caption_html)
        return msg.message_id


@dataclass(slots=True)
class PublishResult:
    outcome: str  # published | skipped | failed
    message_id: int | None = None
    reason: str = ""


def render_channel_text(post: Post, footer: str) -> str:
    body = sanitize(post.draft.post_json.get("body", "")) if post.draft else ""
    if footer:
        body = f"{body}\n\n{sanitize(footer)}"
    return body[:MAX_MESSAGE]


class Publisher:
    def __init__(
        self,
        db: Database,
        scheduler: Scheduler,
        sender: ChannelSender,
        settings: Settings,
        notify=None,
    ) -> None:
        self.db = db
        self.scheduler = scheduler
        self.sender = sender
        self.settings = settings
        # async fn(text) -> None: сообщение в редакторский чат
        self.notify = notify

    @staticmethod
    def job_id(post_id: int) -> str:
        return f"publish:{post_id}"

    # --- очередь --------------------------------------------------------------

    async def schedule(self, post_id: int, when: datetime) -> None:
        """approved → scheduled, задача в планировщике."""
        async with self.db.session() as s:
            post = await s.get(Post, post_id)
            if post is None or post.status not in (PostStatus.approved, PostStatus.scheduled):
                raise ValueError(f"пост {post_id} не одобрен")
            post.scheduled_at = when
            if post.can_move_to(PostStatus.scheduled):
                post.status = PostStatus.scheduled
            s.add(
                Event(
                    post_id=post_id,
                    actor="system",
                    action="scheduled",
                    payload={"when": when.isoformat()},
                )
            )
            await s.commit()
        self.scheduler.add_once(self.job_id(post_id), when, self.publish, post_id)

    async def cancel(self, post_id: int, actor: int) -> bool:
        """scheduled → in_review, задача снята. Кнопки редактор получит заново."""
        removed = self.scheduler.remove(self.job_id(post_id))
        async with self.db.session() as s:
            post = await s.get(Post, post_id)
            if post is None or post.status not in (PostStatus.approved, PostStatus.scheduled):
                return False
            post.status = PostStatus.in_review
            post.approved_by = None
            post.second_approved_by = None
            post.scheduled_at = None
            s.add(
                Event(
                    post_id=post_id,
                    actor=str(actor),
                    action="cancelled",
                    payload={"job_removed": removed},
                )
            )
            await s.commit()
        return True

    async def rearm_from_db(self) -> tuple[list[int], list[int]]:
        """При старте: вернуть задачи из базы. Возвращает (поставлено, пропущено давно)."""
        now = datetime.now(UTC)
        armed, missed = [], []
        async with self.db.session() as s:
            posts = (
                await s.scalars(
                    select(Post).where(
                        Post.status.in_([PostStatus.approved, PostStatus.scheduled]),
                        Post.scheduled_at.is_not(None),
                    )
                )
            ).all()
            for p in posts:
                when = p.scheduled_at
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                if when < now - timedelta(seconds=PUBLISH_GRACE_SECONDS):
                    missed.append(p.id)
                    continue
                if p.can_move_to(PostStatus.scheduled):
                    p.status = PostStatus.scheduled
                self.scheduler.add_once(self.job_id(p.id), when, self.publish, p.id)
                armed.append(p.id)
            await s.commit()
        if missed:
            log.warning("Пропущенные публикации (слишком поздно): %s", missed)
        return armed, missed

    # --- отправка -------------------------------------------------------------

    async def publish(self, post_id: int) -> PublishResult:
        """Идемпотентно: второй вызов для того же поста ничего не отправит."""
        lock = uuid.uuid4().hex
        async with self.db.session() as s:
            # Захват: только один вызов получит rowcount == 1.
            res = await s.execute(
                update(Post)
                .where(
                    Post.id == post_id,
                    Post.status.in_([PostStatus.approved, PostStatus.scheduled]),
                    Post.publish_key.is_(None),
                )
                .values(publish_key=lock)
            )
            await s.commit()
            if res.rowcount != 1:
                log.info("Пост %s: публикация пропущена (уже отправлен или не одобрен)", post_id)
                return PublishResult("skipped", reason="уже отправлен или не одобрен")

            post = await s.get(
                Post, post_id, options=[selectinload(Post.draft)], populate_existing=True
            )
            text = render_channel_text(post, self.settings.post_footer)
            card = post.draft.card_path if post.draft else None

        try:
            if card and len(text) <= 1024:
                message_id = await self.sender.send_photo(self.settings.channel_id, card, text)
            else:
                message_id = await self.sender.send_text(self.settings.channel_id, text)
        except Exception as exc:  # noqa: BLE001 - любую ошибку отдаём редактору, лок снимаем
            log.exception("Пост %s: публикация не удалась", post_id)
            async with self.db.session() as s:
                await s.execute(
                    update(Post)
                    .where(Post.id == post_id, Post.publish_key == lock)
                    .values(publish_key=None)
                )
                s.add(
                    Event(
                        post_id=post_id,
                        actor="system",
                        action="publish_failed",
                        payload={"error": str(exc)[:500]},
                    )
                )
                await s.commit()
            if self.notify:
                await self.notify(
                    f"#{post_id}: публикация не удалась: {str(exc)[:200]}. "
                    f"Пост остался в очереди, попробуйте /publish {post_id}."
                )
            return PublishResult("failed", reason=str(exc))

        async with self.db.session() as s:
            post = await s.get(Post, post_id)
            post.status = PostStatus.published
            post.published_at = datetime.now(UTC)
            post.channel_message_id = message_id
            s.add(
                Event(
                    post_id=post_id,
                    actor="system",
                    action="published",
                    payload={"message_id": message_id},
                )
            )
            await s.commit()
        if self.notify:
            await self.notify(f"#{post_id} опубликован{self._link(message_id)}.")
        return PublishResult("published", message_id=message_id)

    def _link(self, message_id: int) -> str:
        ch = self.settings.channel_id
        if ch.startswith("@"):
            return f": https://t.me/{ch[1:]}/{message_id}"
        return ""
