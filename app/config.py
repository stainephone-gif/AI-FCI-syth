"""Настройки сервиса. Всё приходит из переменных окружения или файла .env."""

from __future__ import annotations

import logging
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    anthropic_api_key: str = ""

    # Кто имеет право нажимать кнопки и писать боту. Все остальные игнорируются.
    editor_ids: Annotated[frozenset[int], NoDecode] = Field(default=frozenset())
    editor_chat_id: int | None = None
    channel_id: str = ""

    database_url: str = "sqlite+aiosqlite:///./data/syth.db"
    tz: str = "Europe/Moscow"

    collect_cron: str = "0 6 * * *"
    digest_cron: str = "0 9 * * *"

    sources_file: str = "sources.yaml"
    prompts_dir: str = "prompts"

    # Сбор и ранжирование
    freshness_hours: int = 72  # кандидаты старше окна не берём
    fetch_full_text: bool = True  # добирать полный текст статьи по ссылке
    rank_model: str = "claude-haiku-4-5"
    rank_concurrency: int = 4  # параллельных запросов к модели
    rank_min_relevance: int = 60  # ниже порога кандидат остаётся в базе, но не идёт дальше
    digest_top_n: int = 3  # сколько черновиков показывать редактору

    # Черновики
    draft_model: str = "claude-opus-5"
    draft_effort: str = "medium"  # low | medium | high
    draft_concurrency: int = 2
    claim_match_threshold: float = 0.85  # нечёткое совпадение цитаты с источником
    publish_slots: str = "12:00,18:00"  # кнопки «Опубликовать в …»

    # Публикация. Строка, которая добавляется в конец каждого поста (пометка о генерации).
    # Пусто, пока редакция не согласовала текст (шаг 9).
    post_footer: str = ""

    # Карточки
    cards_enabled: bool = True
    cards_dir: str = "data/cards"
    chromium_path: str = ""  # пусто: Chromium из установки Playwright

    log_level: str = "INFO"

    @field_validator("editor_ids", mode="before")
    @classmethod
    def _parse_editor_ids(cls, value: object) -> frozenset[int]:
        if value is None or value == "":
            return frozenset()
        if isinstance(value, str):
            parts = [p.strip() for p in value.replace(";", ",").split(",")]
            return frozenset(int(p) for p in parts if p)
        if isinstance(value, (list, tuple, set, frozenset)):
            return frozenset(int(v) for v in value)
        raise TypeError("EDITOR_IDS должен быть строкой вида '111,222'")

    def is_editor(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.editor_ids

    @property
    def slots(self) -> list[str]:
        return [x.strip() for x in self.publish_slots.split(",") if x.strip()]


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Токен бота попадает в URL запросов aiogram; на DEBUG его лучше не светить.
    logging.getLogger("aiogram.event").setLevel(logging.INFO)


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
