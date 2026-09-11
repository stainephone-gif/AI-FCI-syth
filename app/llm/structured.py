"""Структурированный ответ от моделей без встроенной поддержки схем: JSON в тексте + проверка."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# messages -> текст ответа. messages в формате [{"role": ..., "content": ...}].
ChatFn = Callable[[list[dict[str, str]]], Awaitable[str]]

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def schema_instructions(model_cls: type[BaseModel]) -> str:
    schema = json.dumps(model_cls.model_json_schema(), ensure_ascii=False)
    return (
        "\n\nФормат ответа: один JSON-объект строго по этой JSON-схеме, без пояснений, "
        "без markdown и без текста до или после объекта. Все обязательные поля должны "
        f"присутствовать.\nСхема:\n{schema}"
    )


def extract_json(text: str) -> str:
    text = _FENCE.sub("", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("в ответе нет JSON-объекта")
    return text[start : end + 1]


def parse_as(model_cls: type[T], text: str) -> T:
    return model_cls.model_validate_json(extract_json(text))


async def structured_call(
    chat: ChatFn, system: str, user: str, model_cls: type[T], *, retries: int = 2
) -> T:
    """Просим JSON, проверяем схемой, при ошибке объясняем модели, что не так, и повторяем."""
    messages = [
        {"role": "system", "content": system + schema_instructions(model_cls)},
        {"role": "user", "content": user},
    ]
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        reply = await chat(messages)
        try:
            return parse_as(model_cls, reply)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            log.info("Ответ не по схеме (попытка %d): %s", attempt + 1, str(exc)[:200])
            messages += [
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": "Ответ не соответствует схеме: "
                    f"{str(exc)[:800]}\nВерни только исправленный JSON-объект.",
                },
            ]
    raise RuntimeError(f"модель не вернула ответ по схеме за {retries + 1} попытки: {last_error}")
