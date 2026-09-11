from types import SimpleNamespace

from app.bot.middleware import EditorsOnlyMiddleware


async def _handler(event, data):
    return "handled"


async def test_editor_passes() -> None:
    mw = EditorsOnlyMiddleware(frozenset({111}))
    data = {"event_from_user": SimpleNamespace(id=111)}
    assert await mw(_handler, object(), data) == "handled"


async def test_stranger_is_dropped_silently() -> None:
    mw = EditorsOnlyMiddleware(frozenset({111}))
    data = {"event_from_user": SimpleNamespace(id=999)}
    assert await mw(_handler, object(), data) is None


async def test_no_user_is_dropped() -> None:
    mw = EditorsOnlyMiddleware(frozenset({111}))
    assert await mw(_handler, object(), {}) is None


async def test_whoami_passes_for_stranger() -> None:
    from datetime import datetime

    from aiogram.types import Chat, Message, Update

    mw = EditorsOnlyMiddleware(frozenset({111}))
    msg = Message(
        message_id=1, date=datetime.now(), chat=Chat(id=5, type="group"), text="/whoami@bot"
    )
    upd = Update(update_id=1, message=msg)
    data = {"event_from_user": SimpleNamespace(id=999)}
    assert await mw(_handler, upd, data) == "handled"
    msg2 = Message(message_id=2, date=datetime.now(), chat=Chat(id=5, type="group"), text="/status")
    assert await mw(_handler, Update(update_id=2, message=msg2), data) is None


async def test_whoami_reports_channel_from_forward() -> None:
    from datetime import datetime

    from aiogram.types import Chat, Message, MessageOriginChannel

    from app.bot.handlers import cmd_whoami
    from app.config import Settings

    replies: list[str] = []

    class Msg(Message):
        async def reply(self, text, **kw):  # type: ignore[override]
            replies.append(text)

    fwd = Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=-100, type="supergroup"),
        forward_origin=MessageOriginChannel(
            type="channel",
            date=datetime.now(),
            chat=Chat(id=-1009, type="channel"),
            message_id=7,
        ),
    )
    msg = Msg(
        message_id=2,
        date=datetime.now(),
        chat=Chat(id=-100, type="supergroup"),
        text="/whoami",
        reply_to_message=fwd,
    )
    settings = Settings(bot_token="1:x", editor_ids="", _env_file=None)
    await cmd_whoami(msg, settings)
    assert "<code>-1009</code>" in replies[0] and "<code>-100</code>" in replies[0]
