"""Структурированный ответ от моделей без встроенной поддержки схем: JSON в тексте + проверка."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, get_args, get_origin

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# messages -> текст ответа. messages в формате [{"role": ..., "content": ...}].
ChatFn = Callable[[list[dict[str, str]]], Awaitable[str]]

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _placeholder(annotation: Any) -> Any:
    """Пример значения для поля: модели копируют пример надёжнее, чем читают схему."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is None and isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return template_from_model(annotation)
    if origin is list:
        return [_placeholder(args[0])] if args else []
    if origin is not None and str(origin) == "typing.Literal":
        return args[0]
    if args and type(None) in args:  # Optional[X]
        return _placeholder([a for a in args if a is not type(None)][0])
    if annotation is bool:
        return False
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    return "..."


def template_from_model(model_cls: type[BaseModel]) -> dict[str, Any]:
    return {name: _placeholder(f.annotation) for name, f in model_cls.model_fields.items()}


def field_notes(model_cls: type[BaseModel]) -> str:
    lines = []
    for name, f in model_cls.model_fields.items():
        ann = f.annotation
        origin = get_origin(ann)
        if origin is not None and str(origin) == "typing.Literal":
            kind = "одно из: " + ", ".join(repr(a) for a in get_args(ann))
        elif origin is list:
            kind = "список"
        else:
            kind = getattr(ann, "__name__", str(ann))
        desc = f" — {f.description}" if f.description else ""
        lines.append(f"- {name} ({kind}){desc}")
    return "\n".join(lines)


def schema_instructions(model_cls: type[BaseModel]) -> str:
    template = json.dumps(template_from_model(model_cls), ensure_ascii=False)
    return (
        "\n\nФормат ответа: один JSON-объект с точно такими ключами, как в шаблоне ниже, "
        "где вместо примерных значений стоят твои. Без пояснений, без markdown, без текста "
        "до или после объекта. Не возвращай описание схемы, только заполненный объект.\n"
        f"Шаблон: {template}\n"
        f"Поля:\n{field_notes(model_cls)}"
    )


def extract_json(text: str) -> str:
    text = _FENCE.sub("", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("в ответе нет JSON-объекта")
    return text[start : end + 1]


def parse_as(model_cls: type[T], text: str) -> T:
    return model_cls.model_validate_json(extract_json(text))


def _feedback(model_cls: type[BaseModel], reply: str, exc: Exception) -> str:
    template = json.dumps(template_from_model(model_cls), ensure_ascii=False)
    try:
        data = json.loads(extract_json(reply))
    except Exception:  # noqa: BLE001
        data = None
    if isinstance(data, dict) and "properties" in data and "relevance" not in data:
        return (
            "Ты вернул описание схемы, а не заполненный объект. Верни только объект вида "
            f"{template} со своими значениями."
        )
    return (
        f"Ответ не соответствует формату: {str(exc)[:600]}\n"
        f"Верни только JSON-объект вида {template} со своими значениями."
    )


async def structured_call(
    chat: ChatFn, system: str, user: str, model_cls: type[T], *, retries: int = 2
) -> T:
    """Просим JSON по шаблону, проверяем, при ошибке объясняем модели, что не так, и повторяем."""
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
            log.info("Ответ не по формату (попытка %d): %s", attempt + 1, str(exc)[:160])
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": _feedback(model_cls, reply, exc)},
            ]
    raise RuntimeError(f"модель не вернула ответ по формату за {retries + 1} попытки: {last_error}")
