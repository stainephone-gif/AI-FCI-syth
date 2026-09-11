import pytest

from app import cli


def test_cli_help_lists_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for cmd in ("collect", "digest", "draft", "post", "card", "status"):
        assert cmd in out


async def test_cli_status_on_empty_db(settings, capsys: pytest.CaptureFixture[str]) -> None:
    await cli.cmd_status(settings)
    assert "пусто" in capsys.readouterr().out


def test_reset_requires_yes_and_deletes(settings, tmp_path, capsys) -> None:
    db = tmp_path / "x.db"
    db.write_bytes(b"x")
    cards = tmp_path / "cards"
    cards.mkdir()
    settings.database_url = f"sqlite+aiosqlite:///{db}"
    settings.cards_dir = str(cards)
    cli.cmd_reset(settings, yes=False)
    assert db.exists() and "--yes" in capsys.readouterr().out
    cli.cmd_reset(settings, yes=True)
    assert not db.exists() and not cards.exists()
