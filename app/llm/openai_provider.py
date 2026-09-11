"""Любой OpenAI-совместимый API: Qwen (Alibaba Cloud), Kimi (Moonshot), OpenRouter и т. п."""

from __future__ import annotations

import logging

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import Settings
from app.draft.schemas import PostDraft
from app.draft.writer import WriteFn
from app.llm.structured import structured_call
from app.rank.ranker import RankFn
from app.rank.schemas import RankResult

log = logging.getLogger(__name__)


class OpenAICompatProvider:
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key or not settings.openai_base_url:
            raise SystemExit("Для MODEL_PROVIDER=openai нужны OPENAI_BASE_URL и OPENAI_API_KEY")
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key, base_url=settings.openai_base_url
        )
        self.json_mode = True  # выключается, если сервер не принимает response_format

    async def chat(self, model: str, msgs: list[dict[str, str]]) -> str:
        kwargs = {"model": model, "messages": msgs, "temperature": 0.3}
        if self.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            r = await self.client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            if not self.json_mode or "response_format" not in str(exc):
                raise
            log.warning("Сервер не принял response_format (%s), дальше без него", exc)
            self.json_mode = False
            kwargs.pop("response_format")
            r = await self.client.chat.completions.create(**kwargs)
        return r.choices[0].message.content or ""

    async def structured(
        self, model: str, system: str, user: str, schema: type[BaseModel]
    ) -> BaseModel:
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
        return [m.id async for m in self.client.models.list()]
