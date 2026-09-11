from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import Database, Item, Post, PostStatus, Source
from app.db.models import SourceKind
from app.draft import service
from app.draft.prompt import manual_system_prompt, manual_user_prompt
from app.draft.schemas import CardSpec, Claim, PostDraft
from app.ingest import feeds
from app.ingest.manual import MANUAL_SOURCE, extract_url, intake, make_title

ROOT = Path(__file__).resolve().parents[1]


def test_extract_url_and_title() -> None:
    text = "в четверг семинар с ИТМО про агентов, регистрация: https://itmo.ru/reg?x=1."
    assert extract_url(text) == "https://itmo.ru/reg?x=1"
    assert make_title(text) == "в четверг семинар с ИТМО про агентов, регистрация"
    assert extract_url("без ссылки") is None
    assert make_title("https://a.b/c") == "Без названия"


def test_manual_prompt_has_today_and_weekday() -> None:
    today = datetime(2026, 9, 11, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    p = manual_user_prompt(text="в четверг семинар", today=today)
    assert "Сегодня 11.09.2026, пятница." in p and "в четверг семинар" in p
    sys_prompt = manual_system_prompt(ROOT / "prompts")
    assert "пост-анонс" in sys_prompt and "# Стайлгайд канала" in sys_prompt


async def test_intake_creates_source_item_and_ranked_post(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_full_text(client, url):
        return "Семинар пройдёт 17 сентября в 18:00 в аудитории 305."

    monkeypatch.setattr(feeds, "fetch_full_text", fake_full_text)
    text = "в четверг семинар с ИТМО, ссылка https://itmo.ru/reg"
    pid = await intake(db, text, message_id=10, actor=111)
    pid2 = await intake(db, "набор на курс без ссылки", message_id=11, actor=111)
    pid3 = await intake(db, text, message_id=12, actor=222)  # та же ссылка второй раз

    async with db.session() as s:
        src = await s.scalar(select(Source).where(Source.name == MANUAL_SOURCE))
        assert src.kind == SourceKind.manual
        posts = (await s.scalars(select(Post).order_by(Post.id))).all()
        items = {i.id: i for i in (await s.scalars(select(Item))).all()}
    assert [p.id for p in posts] == [pid, pid2, pid3]
    assert all(p.status == PostStatus.ranked for p in posts)
    first = items[posts[0].item_id]
    assert first.url == "https://itmo.ru/reg" and "Текст по ссылке:" in first.text
    assert items[posts[1].item_id].url == "manual://111/11"
    assert len({i.url_hash for i in items.values()}) == 3


async def test_manual_draft_uses_manual_prompt_and_shows_dates(
    db: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.cards_enabled = False
    pid = await intake(
        db, "в четверг семинар с ИТМО в 18:00", message_id=1, actor=111, fetch_link=False
    )
    seen = {}

    async def fake_write(system, material):
        seen["system"], seen["material"] = system, material
        return PostDraft(
            headline="Семинар с ИТМО",
            body="<b>Семинар с ИТМО</b>\n\n<i>В четверг, 17.09, в 18:00.</i>",
            media_takeaway="",
            source_url="",
            claims=[Claim(text="в 18:00", quote="в 18:00")],
            card=CardSpec(title="Семинар с ИТМО", subtitle="17.09, 18:00"),
            confidence_notes=[],
            dates=["17.09.2026, четверг, 18:00"],
        )

    await service.draft_for_post(db, fake_write, pid, settings)
    assert "пост-анонс" in seen["system"] and "Сообщение редактора:" in seen["material"]
    assert "Сегодня " in seen["material"]
    async with db.session() as s:
        post = await service._load(s, pid)
        text = service.render_review(post)
    assert "📅 <b>Проверьте даты:</b>\n• 17.09.2026, четверг, 18:00" in text
    assert "Не нашёл в источнике" not in text
    assert "Редакция" in text
