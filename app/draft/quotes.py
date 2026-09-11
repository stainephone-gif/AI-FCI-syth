"""Второй проход по цитатам: просим модель найти дословные фрагменты для неподтверждённых фактов."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from app.draft.schemas import Claim


class QuoteFix(BaseModel):
    text: str = Field(description="Утверждение, как оно было дано")
    quote: str = Field(
        description="Дословный фрагмент текста источника, 5–30 слов, скопированный символ в "
        "символ на языке источника; пустая строка, если подтверждения в тексте нет"
    )


class QuoteFixes(BaseModel):
    quotes: list[QuoteFix]


# (source_text, claims) -> claims с исправленными цитатами
FixQuotesFn = Callable[[str, list[Claim]], Awaitable[list[Claim]]]

SYSTEM = (
    "Ты проверяешь факты. Тебе дан текст источника и список утверждений из поста. "
    "Для каждого утверждения найди в тексте источника дословный фрагмент, который его "
    "подтверждает: скопируй его символ в символ, на языке источника, без перевода и "
    "пересказа, 5–30 слов. Если подтверждения в тексте нет, оставь quote пустой. "
    "Не выдумывай и не переформулируй."
)


def user_prompt(source_text: str, claims: list[Claim]) -> str:
    items = "\n".join(f"{i + 1}. {c.text}" for i, c in enumerate(claims))
    return f"Утверждения:\n{items}\n\nТекст источника:\n{source_text.strip()[:16000]}"


def apply_fixes(claims: list[Claim], fixes: QuoteFixes) -> list[Claim]:
    by_text = {f.text.strip(): f.quote for f in fixes.quotes if f.quote}
    out = []
    for i, c in enumerate(claims):
        quote = by_text.get(c.text.strip())
        if quote is None and i < len(fixes.quotes) and fixes.quotes[i].quote:
            quote = fixes.quotes[i].quote  # модель могла чуть переписать text, берём по порядку
        out.append(Claim(text=c.text, quote=quote or c.quote))
    return out
