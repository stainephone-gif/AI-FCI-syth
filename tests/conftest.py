from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("BOT_TOKEN", "123456:test")
    monkeypatch.setenv("EDITOR_IDS", "111, 222")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("PROMPTS_DIR", str(Path(__file__).resolve().parents[1] / "prompts"))
    return Settings(_env_file=None)


@pytest.fixture
async def db() -> Database:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.close()
