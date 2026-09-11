"""Команды бота с подставными объектами: ловим несостыковки сигнатур до запуска."""

from types import SimpleNamespace

import pytest

from app.bot import handlers
from app.bot.keyboards import CallbackSigner
from app.config import Settings
from app.db import Database
from app.draft.schemas import Claim
from tests.test_draft import _draft, _seed


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(("text", chat_id, text, kw.get("reply_markup")))
        return SimpleNamespace(message_id=len(self.sent))

    async def send_photo(self, chat_id, photo, **kw):
        self.sent.append(("photo", chat_id, kw.get("caption")))
        return SimpleNamespace(message_id=len(self.sent))


class FakeMessage:
    def __init__(self, text: str, user_id: int = 111, chat_id: int = -100) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=chat_id)
        self.message_id = 1
        self.reply_to_message = None
        self.replies: list[str] = []

    async def answer(self, text, **kw):
        self.replies.append(text)

    async def reply(self, text, **kw):
        self.replies.append(text)


async def fake_write(system, material):
    d = _draft()
    d.claims = [Claim(text="Ошибок меньше на 40%", quote="40% fewer factual errors")]
    return d


async def fake_fix(source_text, claims):
    return claims


@pytest.fixture
def ctx(settings: Settings):
    settings.cards_enabled = False
    return dict(
        settings=settings,
        write_fn=fake_write,
        fix_fn=fake_fix,
        signer=CallbackSigner("k"),
    )


async def test_cmd_digest_sends_drafts_with_buttons(db: Database, ctx) -> None:
    await _seed(db)
    bot, msg = FakeBot(), FakeMessage("/digest")
    await handlers.cmd_digest(msg, bot, db=db, **ctx)
    kinds = [s[0] for s in bot.sent]
    assert kinds == ["text"]
    assert bot.sent[0][3] is not None  # клавиатура с кнопками
    assert "Claude Foo" in bot.sent[0][2]


async def test_cmd_post_makes_announcement(db: Database, ctx, monkeypatch) -> None:
    from app.ingest import feeds

    async def no_fetch(client, url):
        return None

    monkeypatch.setattr(feeds, "fetch_full_text", no_fetch)
    bot, msg = FakeBot(), FakeMessage("/post в четверг семинар с ИТМО в 18:00")
    await handlers.cmd_post(msg, bot, db=db, **ctx)
    assert any("Пишу пост #" in r for r in msg.replies)
    assert bot.sent and bot.sent[0][0] == "text"


async def test_cmd_post_rejects_empty(db: Database, ctx) -> None:
    bot, msg = FakeBot(), FakeMessage("/post")
    await handlers.cmd_post(msg, bot, db=db, **ctx)
    assert msg.replies and "одним сообщением" in msg.replies[0]
    assert bot.sent == []


async def test_reply_to_draft_revises(db: Database, ctx) -> None:
    pid = await _seed(db)
    bot = FakeBot()
    await handlers.cmd_digest(FakeMessage("/digest"), bot, db=db, **ctx)
    review_message_id = bot.sent[0][3] and 1
    msg = FakeMessage("короче")
    msg.reply_to_message = SimpleNamespace(message_id=review_message_id)
    await handlers.on_reply_to_draft(msg, bot=bot, db=db, **ctx)
    assert any(f"Правлю #{pid}" in r for r in msg.replies)
    assert len(bot.sent) == 2  # пришла новая версия
