"""Черновики и редакторские действия над постом. Никакой отправки в канал здесь нет."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.telegram_html import sanitize
from app.cards.render import CardData, render_png
from app.config import Settings
from app.db import Database, Draft, Event, Item, Post, PostStatus
from app.db.models import SourceKind
from app.draft.claims import check_claims
from app.draft.lint import lint
from app.draft.prompt import (
    PROMPT_VERSION,
    draft_system_prompt,
    draft_user_prompt,
    edit_system_prompt,
    edit_user_prompt,
    manual_system_prompt,
    manual_user_prompt,
)
from app.draft.quotes import FixQuotesFn
from app.draft.schemas import Claim, PostDraft
from app.draft.writer import WriteFn
from app.rank.ranker import top_ranked

log = logging.getLogger(__name__)


class PostNotFound(Exception):
    pass


class WrongState(Exception):
    pass


async def _load(s: AsyncSession, post_id: int) -> Post:
    post = await s.get(
        Post,
        post_id,
        options=[
            selectinload(Post.item).selectinload(Item.source),
            selectinload(Post.item).selectinload(Item.ranking),
            selectinload(Post.draft),
        ],
    )
    if post is None:
        raise PostNotFound(post_id)
    return post


def _event(s: AsyncSession, post_id: int, actor: str, action: str, **payload) -> None:
    s.add(Event(post_id=post_id, actor=actor, action=action, payload=payload))


# --- черновики -------------------------------------------------------------


async def _store_draft(
    db: Database,
    post_id: int,
    result: PostDraft,
    source_text: str,
    settings: Settings,
    fix_fn: FixQuotesFn | None = None,
) -> Draft:
    checks = check_claims(result.claims, source_text, settings.claim_match_threshold)
    bad = [c for c in checks if not c.confirmed]
    if bad and fix_fn is not None:
        # Второй проход: модель ищет дословные фрагменты только для неподтверждённых.
        try:
            fixed = await fix_fn(source_text, [Claim(text=c.text, quote=c.quote) for c in bad])
            by_text = {c.text: c.quote for c in fixed}
            result.claims = [
                Claim(text=c.text, quote=by_text.get(c.text, c.quote)) for c in result.claims
            ]
            checks = check_claims(result.claims, source_text, settings.claim_match_threshold)
        except Exception as exc:  # noqa: BLE001 - не удалось уточнить, оставляем первую проверку
            log.warning("Уточнение цитат для поста %s не удалось: %s", post_id, exc)
    payload = result.model_dump()
    payload["checks"] = [asdict(c) for c in checks]
    payload["style_notes"] = lint(result.body)
    async with db.session() as s:
        post = await _load(s, post_id)
        last = await s.scalar(
            select(Draft.version)
            .where(Draft.item_id == post.item_id)
            .order_by(Draft.version.desc())
        )
        draft = Draft(
            item_id=post.item_id,
            version=(last or 0) + 1,
            post_json=payload,
            model=settings.draft_model_name,
            prompt_version=PROMPT_VERSION,
        )
        s.add(draft)
        await s.flush()
        post.draft_id = draft.id
        if post.can_move_to(PostStatus.drafted):
            post.status = PostStatus.drafted
        _event(
            s,
            post.id,
            "system",
            "drafted",
            version=draft.version,
            unconfirmed=sum(1 for c in checks if not c.confirmed),
        )
        await s.commit()
        card_data = CardData(
            title=result.card.title or result.headline,
            subtitle=result.card.subtitle,
            topic=(post.item.ranking.topic if post.item and post.item.ranking else "other"),
            source_name=post.item.source.name if post.item else "",
            date=post.item.published_at if post.item else None,
        )
        draft_id, version, item_id = draft.id, draft.version, draft.item_id
    card_path = await _render_card(card_data, item_id, version, settings)
    if card_path:
        async with db.session() as s:
            d = await s.get(Draft, draft_id)
            d.card_path = card_path
            await s.commit()
            draft = d
    return draft


async def _render_card(
    data: CardData, item_id: int, version: int, settings: Settings
) -> str | None:
    """Карточка не должна ломать черновик: при любой ошибке черновик идёт без неё."""
    if not settings.cards_enabled:
        return None
    out = Path(settings.cards_dir) / f"{item_id}_v{version}.png"
    try:
        await render_png(data, out, chromium_path=settings.chromium_path or None)
    except Exception as exc:  # noqa: BLE001
        log.warning("Карточка для %s v%s не отрисована: %s", item_id, version, exc)
        return None
    return str(out)


async def draft_for_post(
    db: Database,
    write_fn: WriteFn,
    post_id: int,
    settings: Settings,
    fix_fn: FixQuotesFn | None = None,
) -> Draft:
    async with db.session() as s:
        post = await _load(s, post_id)
        item = post.item
        if item is None:
            raise WrongState("у поста нет материала")
        if post.status not in (PostStatus.ranked, PostStatus.needs_edit):
            raise WrongState(f"нельзя писать черновик из статуса {post.status.value}")
        manual = item.source.kind == SourceKind.manual
        if manual:
            material = manual_user_prompt(text=item.text, today=datetime.now(ZoneInfo(settings.tz)))
            system = manual_system_prompt(settings.prompts_dir)
        else:
            material = draft_user_prompt(
                title=item.title,
                url=item.url,
                source_name=item.source.name,
                published_at=item.published_at,
                audience_angle=item.ranking.audience_angle if item.ranking else "",
                text=item.text,
            )
            system = draft_system_prompt(settings.prompts_dir)
        source_text = item.text
    result = await write_fn(system, material)
    return await _store_draft(db, post_id, result, source_text, settings, fix_fn)


async def draft_top(
    db: Database, write_fn: WriteFn, settings: Settings, fix_fn: FixQuotesFn | None = None
) -> list[Draft]:
    """Черновики для лучших кандидатов выше порога."""
    top = await top_ranked(db, limit=settings.digest_top_n)
    chosen = [item for item, rk in top if rk.relevance >= settings.rank_min_relevance]
    if not chosen:
        return []
    async with db.session() as s:
        ids = (
            await s.scalars(select(Post.id).where(Post.item_id.in_([i.id for i in chosen])))
        ).all()
    sem = asyncio.Semaphore(settings.draft_concurrency)

    async def one(pid: int) -> Draft | None:
        async with sem:
            try:
                return await draft_for_post(db, write_fn, pid, settings, fix_fn)
            except Exception as exc:  # noqa: BLE001
                log.warning("Черновик для поста %s не написан: %s", pid, exc)
                return None

    drafts = await asyncio.gather(*(one(pid) for pid in ids))
    return [d for d in drafts if d is not None]


async def revise(
    db: Database,
    write_fn: WriteFn,
    post_id: int,
    remark: str,
    actor: str,
    settings: Settings,
    fix_fn: FixQuotesFn | None = None,
) -> Draft:
    async with db.session() as s:
        post = await _load(s, post_id)
        if post.draft is None or post.item is None:
            raise WrongState("у поста нет черновика")
        if post.status == PostStatus.in_review and post.can_move_to(PostStatus.needs_edit):
            post.status = PostStatus.needs_edit
        elif post.status != PostStatus.needs_edit:
            raise WrongState(f"нельзя править из статуса {post.status.value}")
        _event(s, post.id, actor, "edit_requested", remark=remark, version=post.draft.version)
        material = edit_user_prompt(
            previous_body=post.draft.post_json.get("body", ""),
            remark=remark,
            source_text=post.item.text,
            url=post.item.url,
        )
        source_text = post.item.text
        await s.commit()
    result = await write_fn(edit_system_prompt(settings.prompts_dir), material)
    return await _store_draft(db, post_id, result, source_text, settings, fix_fn)


# --- показ редактору ---------------------------------------------------------


def needs_second_approval(post: Post) -> bool:
    pj = post.draft.post_json if post.draft else {}
    unconfirmed = any(not c.get("confirmed") for c in pj.get("checks", []))
    notes = bool(pj.get("confidence_notes"))
    flagged = bool(post.item and post.item.ranking and post.item.ranking.needs_fact_check)
    return unconfirmed or notes or flagged


def render_review(post: Post) -> str:
    """Текст сообщения в редакторский чат: пост + предупреждения + служебная строка."""
    pj = post.draft.post_json if post.draft else {}
    body = sanitize(pj.get("body", ""))
    parts = [body]
    bad = [c for c in pj.get("checks", []) if not c.get("confirmed")]
    if bad:
        parts.append(
            "⚠️ <b>Не нашёл в источнике:</b>\n" + "\n".join(f"• {escape(c['text'])}" for c in bad)
        )
    notes = pj.get("confidence_notes") or []
    if notes:
        parts.append("⚠️ <b>Модель не уверена:</b>\n" + "\n".join(f"• {escape(n)}" for n in notes))
    style = pj.get("style_notes") or []
    if style:
        parts.append("✏️ <b>Стиль:</b>\n" + "\n".join(f"• {escape(n)}" for n in style))
    dates = pj.get("dates") or []
    is_manual = bool(post.item and post.item.source.kind == SourceKind.manual)
    if dates and is_manual:  # для анонсов даты критичны, для новостей это дата публикации
        parts.append("📅 <b>Проверьте даты:</b>\n" + "\n".join(f"• {escape(d)}" for d in dates))
    rk = post.item.ranking if post.item else None
    meta = [f"#{post.id}", f"v{post.draft.version if post.draft else 0}"]
    if rk:
        meta.append(f"оценка {rk.relevance}")
    if post.item:
        meta.append(escape(post.item.source.name))
    parts.append("<i>" + " · ".join(meta) + "</i>")
    return "\n\n".join(parts)


async def mark_in_review(db: Database, post_id: int, message_id: int) -> None:
    async with db.session() as s:
        post = await _load(s, post_id)
        post.review_message_id = message_id
        if post.can_move_to(PostStatus.in_review):
            post.status = PostStatus.in_review
        await s.commit()


async def post_by_review_message(db: Database, message_id: int) -> int | None:
    async with db.session() as s:
        return await s.scalar(select(Post.id).where(Post.review_message_id == message_id))


# --- решения редактора --------------------------------------------------------


@dataclass(slots=True)
class Decision:
    outcome: str  # approved | needs_second | rejected | invalid
    scheduled_at: datetime | None = None
    reason: str = ""


def next_slot(slot: str, tz: str, now: datetime | None = None) -> datetime:
    zone = ZoneInfo(tz)
    now = (now or datetime.now(zone)).astimezone(zone)
    hh, mm = (int(x) for x in slot.split(":"))
    when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if when <= now:
        when += timedelta(days=1)
    return when


_TIME = re.compile(r"^(?:(\d{1,2})\.(\d{1,2})\s+)?(\d{1,2}):(\d{2})$")


def parse_time(text: str, tz: str, now: datetime | None = None) -> datetime | None:
    """«15:30» → ближайшие 15:30; «14.09 15:30» → конкретная дата этого года."""
    m = _TIME.match(text.strip())
    if not m:
        return None
    day, month, hh, mm = m.groups()
    zone = ZoneInfo(tz)
    now = (now or datetime.now(zone)).astimezone(zone)
    try:
        if day is None:
            return next_slot(f"{hh}:{mm}", tz, now)
        when = now.replace(
            month=int(month), day=int(day), hour=int(hh), minute=int(mm), second=0, microsecond=0
        )
    except ValueError:
        return None
    if when <= now:
        when = when.replace(year=when.year + 1)
    return when


async def approve(db: Database, post_id: int, when: datetime, actor: int) -> Decision:
    async with db.session() as s:
        post = await _load(s, post_id)
        if post.status != PostStatus.in_review:
            return Decision("invalid", reason=f"пост уже в статусе {post.status.value}")
        if needs_second_approval(post):
            post.approved_by = actor
            post.scheduled_at = when
            _event(s, post.id, str(actor), "first_approval", when=when.isoformat())
            await s.commit()
            return Decision("needs_second", scheduled_at=when)
        post.approved_by = actor
        post.scheduled_at = when
        post.status = PostStatus.approved
        _event(s, post.id, str(actor), "approved", when=when.isoformat())
        await s.commit()
        return Decision("approved", scheduled_at=when)


async def confirm(db: Database, post_id: int, actor: int) -> Decision:
    async with db.session() as s:
        post = await _load(s, post_id)
        if post.status != PostStatus.in_review or post.approved_by is None:
            return Decision("invalid", reason="пост не ждёт второго одобрения")
        if post.approved_by == actor:
            return Decision("invalid", reason="подтвердить должен другой редактор")
        post.second_approved_by = actor
        post.status = PostStatus.approved
        _event(
            s,
            post.id,
            str(actor),
            "approved",
            second=True,
            when=post.scheduled_at.isoformat() if post.scheduled_at else None,
        )
        await s.commit()
        return Decision("approved", scheduled_at=post.scheduled_at)


async def reject(db: Database, post_id: int, actor: int, reason: str = "") -> Decision:
    async with db.session() as s:
        post = await _load(s, post_id)
        if not post.can_move_to(PostStatus.rejected):
            return Decision("invalid", reason=f"нельзя отклонить из статуса {post.status.value}")
        post.status = PostStatus.rejected
        _event(s, post.id, str(actor), "rejected", reason=reason)
        await s.commit()
        return Decision("rejected")
