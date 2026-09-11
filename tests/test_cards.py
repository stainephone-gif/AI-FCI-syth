import os
from datetime import datetime
from pathlib import Path

import pytest

from app.cards.render import CardData, render_html, render_png
from app.config import Settings
from app.db import Database, Draft, Post, PostStatus
from app.draft import service
from app.draft.schemas import CardSpec, Claim, PostDraft
from tests.test_draft import SOURCE, _seed

CHROMIUM = "/opt/pw-browsers/chromium"


def test_render_html_escapes_and_picks_topic() -> None:
    html = render_html(
        CardData(
            title="<script>x</script> & заголовок",
            subtitle="подзаголовок",
            topic="media",
            source_name="S",
            date=datetime(2026, 9, 11),
        )
    )
    assert "&lt;script&gt;" in html and "<script>x" not in html
    assert "#F25C8A" in html and "Медиа" in html and "11.09.2026" in html
    assert 'class="xl"' in html


def test_title_size_scales_with_length() -> None:
    assert 'class="md"' in render_html(CardData(title="x" * 90, subtitle=""))
    assert "Источник:" not in render_html(CardData(title="t", subtitle=""))


@pytest.mark.skipif(not os.path.exists(CHROMIUM), reason="нет Chromium")
async def test_render_png_produces_image(tmp_path: Path) -> None:
    out = await render_png(
        CardData(title="Тест", subtitle="карточки", topic="models"),
        tmp_path / "c.png",
        chromium_path=CHROMIUM,
    )
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 10_000


async def test_draft_attaches_card_and_survives_render_failure(
    db: Database, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.cards_dir = str(tmp_path)
    pid = await _seed(db)
    calls = []

    async def fake_render(data, out, *, chromium_path=None):
        calls.append((data.title, data.topic, data.source_name))
        Path(out).write_bytes(b"png")
        return Path(out)

    monkeypatch.setattr(service, "render_png", fake_render)

    async def fake_write(system, material):
        return PostDraft(
            headline="Заголовок",
            body="<b>Заголовок</b>",
            media_takeaway="",
            source_url="https://s/0",
            claims=[Claim(text="t", quote="40% fewer factual errors")],
            card=CardSpec(title="Карточка", subtitle="под"),
            confidence_notes=[],
        )

    draft = await service.draft_for_post(db, fake_write, pid, settings)
    assert draft.card_path and draft.card_path.endswith("_v1.png")
    assert calls == [("Карточка", "models", "Anthropic News")]

    async def broken(data, out, *, chromium_path=None):
        raise RuntimeError("no browser")

    monkeypatch.setattr(service, "render_png", broken)
    await service.mark_in_review(db, pid, 1)
    d2 = await service.revise(db, fake_write, pid, "короче", "111", settings)
    assert d2.version == 2 and d2.card_path is None  # черновик есть, карточки нет
    async with db.session() as s:
        post = await s.get(Post, pid)
        assert post.status == PostStatus.drafted
        assert (await s.get(Draft, post.draft_id)).id == d2.id
    assert SOURCE  # используем импорт, чтобы линтер не ругался
