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
