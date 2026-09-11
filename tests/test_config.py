from app.config import Settings


def test_editor_ids_parsed_from_comma_string(settings: Settings) -> None:
    assert settings.editor_ids == frozenset({111, 222})
    assert settings.is_editor(111)
    assert not settings.is_editor(333)
    assert not settings.is_editor(None)


def test_empty_editor_ids(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("EDITOR_IDS", "")
    s = Settings(_env_file=None)
    assert s.editor_ids == frozenset()
