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
