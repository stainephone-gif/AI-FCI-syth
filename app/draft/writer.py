"""Вызов модели для черновика и правки. Подменяется в тестах."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from anthropic import AsyncAnthropic

from app.draft.schemas import PostDraft

log = logging.getLogger(__name__)

# (system, material) -> PostDraft
WriteFn = Callable[[str, str], Awaitable[PostDraft]]


def make_claude_writer(client: AsyncAnthropic, model: str, effort: str) -> WriteFn:
    async def write(system: str, material: str) -> PostDraft:
        response = await client.beta.messages.parse(
            model=model,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": material}],
            output_format=PostDraft,
            output_config={"effort": effort},
            # Если основная модель откажется, запрос уйдёт на запасную; ответ помечен в usage.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Модель отказалась писать черновик")
        if response.parsed_output is None:
            raise RuntimeError("Модель вернула ответ без структуры")
        log.info(
            "draft: model=%s in=%s cached=%s out=%s",
            response.model,
            response.usage.input_tokens,
            getattr(response.usage, "cache_read_input_tokens", None),
            response.usage.output_tokens,
        )
        return response.parsed_output

    return write
