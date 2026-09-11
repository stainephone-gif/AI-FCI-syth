"""Схема данных. Описание таблиц см. docs/ARCHITECTURE.md, раздел 5."""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SourceKind(enum.StrEnum):
    rss = "rss"
    arxiv = "arxiv"
    html = "html"
    manual = "manual"


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[SourceKind] = mapped_column(Enum(SourceKind, native_enum=False, length=16))
    url: Mapped[str] = mapped_column(String(1000))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Зачем этот источник каналу. Уходит в промпт ранжирования.
    note: Mapped[str] = mapped_column(Text, default="")
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[Item]] = relationship(back_populates="source")


class Item(Base):
    """Кандидат: одна единица контента из источника."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("url_hash", name="uq_items_url_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    url: Mapped[str] = mapped_column(String(2000))
    url_hash: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(500))
    # Хеш нормализованного заголовка: один релиз в трёх изданиях схлопывается.
    title_hash: Mapped[str] = mapped_column(String(64), index=True, default="")
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    text: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[Source] = relationship(back_populates="items")
    ranking: Mapped[Ranking | None] = relationship(back_populates="item", uselist=False)
    drafts: Mapped[list[Draft]] = relationship(back_populates="item")


class Ranking(Base):
    __tablename__ = "rankings"

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
    relevance: Mapped[int] = mapped_column(Integer)
    audience_angle: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(32), default="")
    needs_fact_check: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[Item] = relationship(back_populates="ranking")


class Draft(Base):
    """Версия черновика. Правка создаёт новую строку, старая остаётся."""

    __tablename__ = "drafts"
    __table_args__ = (UniqueConstraint("item_id", "version", name="uq_drafts_item_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    post_json: Mapped[dict] = mapped_column(JSON, default=dict)
    card_path: Mapped[str | None] = mapped_column(String(500))
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[Item] = relationship(back_populates="drafts")


class PostStatus(enum.StrEnum):
    candidate = "candidate"
    ranked = "ranked"
    drafted = "drafted"
    in_review = "in_review"
    needs_edit = "needs_edit"
    approved = "approved"
    scheduled = "scheduled"
    published = "published"
    rejected = "rejected"


# Разрешённые переходы. Всё, чего здесь нет, запрещено.
POST_TRANSITIONS: dict[PostStatus, frozenset[PostStatus]] = {
    PostStatus.candidate: frozenset({PostStatus.ranked, PostStatus.rejected}),
    PostStatus.ranked: frozenset({PostStatus.drafted, PostStatus.rejected}),
    PostStatus.drafted: frozenset({PostStatus.in_review}),
    PostStatus.in_review: frozenset(
        {PostStatus.approved, PostStatus.needs_edit, PostStatus.rejected}
    ),
    PostStatus.needs_edit: frozenset({PostStatus.drafted, PostStatus.rejected}),
    PostStatus.approved: frozenset({PostStatus.scheduled, PostStatus.in_review}),
    PostStatus.scheduled: frozenset({PostStatus.published, PostStatus.in_review}),
    PostStatus.published: frozenset(),
    PostStatus.rejected: frozenset(),
}

# В эти статусы переводит только человек через callback в редакторском чате.
HUMAN_ONLY_STATUSES = frozenset({PostStatus.approved, PostStatus.needs_edit, PostStatus.rejected})


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"), unique=True)
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("drafts.id"))
    status: Mapped[PostStatus] = mapped_column(
        Enum(PostStatus, native_enum=False, length=16), default=PostStatus.candidate, index=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channel_message_id: Mapped[int | None] = mapped_column(Integer)
    review_message_id: Mapped[int | None] = mapped_column(Integer)
    approved_by: Mapped[int | None] = mapped_column(Integer)
    second_approved_by: Mapped[int | None] = mapped_column(Integer)
    publish_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    item: Mapped[Item | None] = relationship()
    draft: Mapped[Draft | None] = relationship()
    events: Mapped[list[Event]] = relationship(back_populates="post")

    def can_move_to(self, new_status: PostStatus) -> bool:
        return new_status in POST_TRANSITIONS[self.status]


class Event(Base):
    """Журнал: кто, что и когда сделал с постом."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id"), index=True)
    # Telegram user id редактора или "system"
    actor: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    post: Mapped[Post | None] = relationship(back_populates="events")
