from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.bot.keyboards import Action, CallbackSigner, review_keyboard
from app.bot.telegram_html import sanitize
from app.config import Settings
from app.db import Database, Draft, Event, Item, Post, PostStatus, Ranking, Source
from app.db.models import SourceKind
from app.draft import service
from app.draft.claims import check_claims, match_score
from app.draft.prompt import draft_system_prompt
from app.draft.schemas import CardSpec, Claim, PostDraft
from app.ingest.normalize import title_hash, url_hash

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    "Today we are releasing Claude Foo, a model that writes better headlines. "
    "In our tests it produced 40% fewer factual errors than the previous version. "
    "The model is available to all paid users starting today."
)


# --- цитаты ------------------------------------------------------------------


def test_match_exact_and_fuzzy() -> None:
    assert match_score("40% fewer factual errors", SOURCE) == 1.0
    assert match_score("40 % fewer factual  errors", SOURCE) >= 0.85  # пробелы и регистр
    assert match_score("available to all users from tomorrow", SOURCE) < 0.85
    assert match_score("the model doubled revenue in Europe", SOURCE) < 0.6


def test_check_claims_flags_invented_quote() -> None:
    claims = [
        Claim(text="Ошибок меньше на 40%", quote="40% fewer factual errors"),
        Claim(text="Модель бесплатна для студентов", quote="free for all students"),
    ]
    checks = check_claims(claims, SOURCE, 0.85)
    assert [c.confirmed for c in checks] == [True, False]


# --- html --------------------------------------------------------------------


def test_sanitize_keeps_allowed_and_drops_rest() -> None:
    raw = (
        '<b>Заголовок</b><p>Текст <span style="x">внутри</span> &amp; '
        '<a href="javascript:x">плохо</a> <a href="https://a.b/c?d=1&e=2">ок</a></p><i>хвост'
    )
    out = sanitize(raw)
    assert out.startswith("<b>Заголовок</b>\nТекст внутри &amp; плохо")
    assert '<a href="https://a.b/c?d=1&amp;e=2">ок</a>' in out
    assert out.endswith("<i>хвост</i>")
    assert "<span" not in out and "javascript" not in out


def test_sanitize_fixes_nesting() -> None:
    assert sanitize("<b><i>x</b></i>") == "<b><i>x</i></b>"


# --- кнопки ------------------------------------------------------------------


def test_callback_roundtrip_and_tamper() -> None:
    signer = CallbackSigner("123:secret")
    data = signer.pack(Action(42, "pub", "12:00"))
    assert len(data.encode()) <= 64
    assert signer.unpack(data) == Action(42, "pub", "12:00")
    forged = data.replace("|42|", "|43|")
    assert signer.unpack(forged) is None
    assert CallbackSigner("other").unpack(data) is None
    assert signer.unpack("garbage") is None


def test_review_keyboard_layout() -> None:
    kb = review_keyboard(CallbackSigner("k"), 7, ["12:00", "18:00"])
    assert [b.text for b in kb.inline_keyboard[0]] == ["Опубликовать 12:00", "Опубликовать 18:00"]
    assert [b.text for b in kb.inline_keyboard[1]] == [
        "Другое время",
        "Отредактировать",
        "Отклонить",
    ]


# --- время -------------------------------------------------------------------


def test_next_slot_and_parse_time() -> None:
    tz = "Europe/Moscow"
    now = datetime(2026, 9, 11, 13, 0, tzinfo=ZoneInfo(tz))
    assert service.next_slot("12:00", tz, now) == datetime(2026, 9, 12, 12, 0, tzinfo=ZoneInfo(tz))
    assert service.next_slot("18:00", tz, now) == datetime(2026, 9, 11, 18, 0, tzinfo=ZoneInfo(tz))
    assert service.parse_time("15:30", tz, now) == datetime(
        2026, 9, 11, 15, 30, tzinfo=ZoneInfo(tz)
    )
    assert service.parse_time("14.09 09:05", tz, now) == datetime(
        2026, 9, 14, 9, 5, tzinfo=ZoneInfo(tz)
    )
    assert service.parse_time("01.01 10:00", tz, now).year == 2027
    assert service.parse_time("в четверг", tz, now) is None
    assert service.parse_time("31.02 10:00", tz, now) is None


# --- сквозной сценарий -------------------------------------------------------


def _draft(body_extra: str = "", invented: bool = False) -> PostDraft:
    return PostDraft(
        headline="Claude Foo пишет заголовки",
        body="<b>Claude Foo пишет заголовки</b>\n\n<i>Anthropic выпустила модель.</i>\n\n"
        "💜 Ошибок меньше на 40%.\n\n💜 Что это значит для медийщика: проверяйте.\n\n"
        '<i>Источники:</i>\n<i>— <a href="https://s/0">Anthropic // Claude Foo</a></i>'
        f"{body_extra}",
        media_takeaway="проверяйте",
        source_url="https://s/0",
        claims=[
            Claim(text="Ошибок меньше на 40%", quote="40% fewer factual errors"),
            *([Claim(text="Бесплатно студентам", quote="free for students")] if invented else []),
        ],
        card=CardSpec(title="Claude Foo", subtitle="меньше ошибок"),
        confidence_notes=[],
    )


async def _seed(db: Database, *, relevance: int = 90, flagged: bool = False) -> int:
    async with db.session() as s:
        src = Source(name="Anthropic News", kind=SourceKind.rss, url="https://s/rss")
        s.add(src)
        await s.flush()
        item = Item(
            source_id=src.id,
            url="https://s/0",
            url_hash=url_hash("https://s/0"),
            title="Claude Foo",
            title_hash=title_hash("Claude Foo"),
            text=SOURCE,
        )
        s.add(item)
        await s.flush()
        s.add(
            Ranking(
                item_id=item.id,
                relevance=relevance,
                audience_angle="угол",
                topic="models",
                needs_fact_check=flagged,
            )
        )
        post = Post(item_id=item.id, status=PostStatus.ranked)
        s.add(post)
        await s.commit()
        return post.id


async def test_draft_revise_approve_flow(db: Database, settings: Settings) -> None:
    pid = await _seed(db)
    calls: list[str] = []

    async def fake_write(system: str, material: str) -> PostDraft:
        calls.append(material)
        return _draft(body_extra="\n\n(v2)" if "Замечание редактора" in material else "")

    drafts = await service.draft_top(db, fake_write, settings)
    assert len(drafts) == 1 and drafts[0].version == 1
    assert "Текст источника:" in calls[0] and "Угол для аудитории (из отбора): угол" in calls[0]

    async with db.session() as s:
        post = await service._load(s, pid)
        assert post.status == PostStatus.drafted
        text = service.render_review(post)
    assert "Claude Foo пишет заголовки" in text and "⚠️" not in text and "#" in text

    await service.mark_in_review(db, pid, message_id=555)
    assert await service.post_by_review_message(db, 555) == pid

    d2 = await service.revise(db, fake_write, pid, "короче", actor="111", settings=settings)
    assert d2.version == 2 and "(v2)" in d2.post_json["body"]
    assert "Замечание редактора:\nкороче" in calls[1]

    await service.mark_in_review(db, pid, message_id=556)
    when = datetime(2026, 9, 12, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    d = await service.approve(db, pid, when, actor=111)
    assert d.outcome == "approved" and d.scheduled_at == when

    # второй раз одобрить нельзя, отклонить из approved тоже нельзя
    assert (await service.approve(db, pid, when, actor=222)).outcome == "invalid"
    assert (await service.reject(db, pid, actor=222)).outcome == "invalid"

    async with db.session() as s:
        actions = (
            await s.scalars(select(Event.action).where(Event.post_id == pid).order_by(Event.id))
        ).all()
        versions = (await s.scalars(select(Draft.version).order_by(Draft.version))).all()
    assert actions == ["drafted", "edit_requested", "drafted", "approved"]
    assert versions == [1, 2]


async def test_flagged_post_needs_two_editors(db: Database, settings: Settings) -> None:
    pid = await _seed(db, flagged=True)

    async def fake_write(system: str, material: str) -> PostDraft:
        return _draft(invented=True)

    await service.draft_for_post(db, fake_write, pid, settings)
    async with db.session() as s:
        post = await service._load(s, pid)
        assert service.needs_second_approval(post)
        text = service.render_review(post)
    assert "Не нашёл в источнике" in text and "Бесплатно студентам" in text

    await service.mark_in_review(db, pid, 1)
    when = datetime(2026, 9, 12, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    assert (await service.approve(db, pid, when, actor=111)).outcome == "needs_second"
    assert (await service.confirm(db, pid, actor=111)).outcome == "invalid"  # тот же редактор
    assert (await service.confirm(db, pid, actor=222)).outcome == "approved"
    async with db.session() as s:
        post = await service._load(s, pid)
    assert post.status == PostStatus.approved
    assert (post.approved_by, post.second_approved_by) == (111, 222)


async def test_low_relevance_gets_no_draft(db: Database, settings: Settings) -> None:
    await _seed(db, relevance=30)

    async def never(system, material):
        raise AssertionError("не должно вызываться")

    assert await service.draft_top(db, never, settings) == []


def test_draft_prompt_bundles_styleguide() -> None:
    text = draft_system_prompt(ROOT / "prompts")
    assert "черновик поста" in text and "# Стайлгайд канала" in text
    assert "Что это значит для медийщика" in text


def test_markdown_from_model_becomes_telegram_html() -> None:
    from app.bot.telegram_html import markdown_to_html

    raw = (
        "**Заголовок**\n\n*Курсив с запятой, да.*\n\n💜 5*3 не курсив\n\n[Источник](https://a.b/c)"
    )
    out = sanitize(raw)
    assert out.startswith("<b>Заголовок</b>")
    assert "<i>Курсив с запятой, да.</i>" in out
    assert "5*3" in out
    assert '<a href="https://a.b/c">Источник</a>' in out
    assert markdown_to_html("## Заголовок") == "<b>Заголовок</b>"
    # настоящий HTML проходит без изменений
    assert sanitize("<b>x</b> и <i>y</i>") == "<b>x</b> и <i>y</i>"


def test_claim_check_accepts_swapped_fields() -> None:
    swapped = [Claim(text="40% fewer factual errors", quote="Ошибок меньше на 40%")]
    assert check_claims(swapped, SOURCE, 0.85)[0].confirmed


async def test_quote_fix_pass_confirms_translated_claims(db: Database, settings: Settings) -> None:
    settings.cards_enabled = False
    pid = await _seed(db)

    async def fake_write(system, material):
        d = _draft()
        d.claims = [Claim(text="Ошибок меньше на 40%", quote="сорок процентов меньше ошибок")]
        return d

    calls = []

    async def fake_fix(source_text, claims):
        calls.append([c.text for c in claims])
        return [Claim(text=c.text, quote="40% fewer factual errors") for c in claims]

    draft = await service.draft_for_post(db, fake_write, pid, settings, fake_fix)
    assert calls == [["Ошибок меньше на 40%"]]
    assert all(c["confirmed"] for c in draft.post_json["checks"])
    assert draft.post_json["claims"][0]["quote"] == "40% fewer factual errors"
    assert "style_notes" in draft.post_json
