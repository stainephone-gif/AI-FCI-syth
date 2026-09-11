"""GigaChat (Сбер). Сначала встроенный разбор по схеме, при сбое обычный JSON с проверкой."""

from __future__ import annotations

import asyncio
import logging

from gigachat import GigaChat
from gigachat.exceptions import (
    BadRequestError,
    RateLimitError,
    UnprocessableEntityError,
)
from gigachat.models import Chat, Messages, MessagesRole
from pydantic import BaseModel

from app.config import Settings
from app.draft.schemas import PostDraft
from app.draft.writer import WriteFn
from app.llm.structured import structured_call
from app.rank.ranker import RankFn
from app.rank.schemas import RankResult

log = logging.getLogger(__name__)

DEFAULT_RANK_MODEL = "GigaChat-2"
DEFAULT_DRAFT_MODEL = "GigaChat-2-Max"

_ROLES = {
    "system": MessagesRole.SYSTEM,
    "user": MessagesRole.USER,
    "assistant": MessagesRole.ASSISTANT,
}


class GigaChatProvider:
    def __init__(self, settings: Settings) -> None:
        if not settings.gigachat_credentials:
            raise SystemExit(
                "GIGACHAT_CREDENTIALS пуст: вставьте Authorization key из личного кабинета"
            )
        kwargs = dict(
            credentials=settings.gigachat_credentials,
            scope=settings.gigachat_scope,
            verify_ssl_certs=settings.gigachat_verify_ssl,
            timeout=120,
            # 429 приходит сразу при втором параллельном запросе; SDK ждёт и повторяет сам.
            max_retries=6,
            retry_backoff_factor=1.5,
        )
        if settings.gigachat_ca_bundle:
            kwargs["ca_bundle_file"] = settings.gigachat_ca_bundle
        self.client = GigaChat(**kwargs)
        self.schema_mode = True  # выключается, если модель не поддерживает response_format
        # Тариф пропускает один запрос за раз, поэтому все вызовы идут по очереди.
        self._lock = asyncio.Semaphore(1)

    def _messages(self, msgs: list[dict[str, str]]) -> list[Messages]:
        return [Messages(role=_ROLES[m["role"]], content=m["content"]) for m in msgs]

    async def chat(self, model: str, msgs: list[dict[str, str]]) -> str:
        r = await self.client.achat(
            Chat(model=model, messages=self._messages(msgs), temperature=0.3)
        )
        return r.choices[0].message.content or ""

    async def structured(
        self, model: str, system: str, user: str, schema: type[BaseModel]
    ) -> BaseModel:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if self.schema_mode:
            try:
                async with self._lock:
                    _, parsed = await self.client.achat_parse(
                        Chat(model=model, messages=self._messages(msgs), temperature=0.3),
                        response_format=schema,
                    )
                return parsed
            except RateLimitError:
                raise  # лимит, а не формат: режим не меняем, ошибка уйдёт наверх
            except (BadRequestError, UnprocessableEntityError) as exc:
                # Сервер не принял response_format: эта модель схемы не умеет, дальше без них.
                log.warning("GigaChat отверг response_format (%s), дальше обычный JSON", exc)
                self.schema_mode = False
            except Exception as exc:  # noqa: BLE001 - разовый сбой (битый JSON): повтор без схемы
                log.info("GigaChat achat_parse: %s; этот запрос повторю обычным JSON", exc)

        async def chat_fn(m: list[dict[str, str]]) -> str:
            return await self.chat(model, m)

        return await structured_call(chat_fn, system, user, schema)

    def rank_fn(self, model: str) -> RankFn:
        async def rank(system: str, material: str) -> RankResult:
            return await self.structured(model, system, material, RankResult)  # type: ignore[return-value]

        return rank

    def write_fn(self, model: str) -> WriteFn:
        async def write(system: str, material: str) -> PostDraft:
            return await self.structured(model, system, material, PostDraft)  # type: ignore[return-value]

        return write

    async def list_models(self) -> list[str]:
        models = await self.client.aget_models()
        return [m.id_ for m in models.data]
