"""Проверка, что цитаты из черновика действительно есть в тексте источника."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.draft.schemas import Claim

_WS = re.compile(r"\s+")
_QUOTES = str.maketrans(
    {"«": '"', "»": '"', "“": '"', "”": '"', "’": "'", "‘": "'", "—": "-", "–": "-"}
)


def _norm(text: str) -> str:
    return _WS.sub(" ", text.translate(_QUOTES)).strip().lower()


@dataclass(slots=True)
class ClaimCheck:
    text: str
    quote: str
    score: float
    confirmed: bool


def match_score(quote: str, source: str) -> float:
    """1.0 при точном вхождении, иначе лучшее сходство с окном того же размера."""
    q, s = _norm(quote), _norm(source)
    if not q or not s:
        return 0.0
    if q in s:
        return 1.0
    n = len(q)
    if n > len(s):
        return SequenceMatcher(None, q, s).ratio()
    best = 0.0
    step = max(1, n // 4)
    for start in range(0, len(s) - n + 1, step):
        window = s[start : start + n + step]
        m = SequenceMatcher(None, q, window)
        if m.quick_ratio() < best:
            continue
        r = m.ratio()
        if r > best:
            best = r
            if best >= 0.99:
                break
    return best


def check_claims(claims: list[Claim], source_text: str, threshold: float) -> list[ClaimCheck]:
    out = []
    for c in claims:
        # Модели иногда меняют поля местами: цитата в text, пересказ в quote. Берём лучшее.
        score = max(match_score(c.quote, source_text), match_score(c.text, source_text))
        out.append(
            ClaimCheck(
                text=c.text, quote=c.quote, score=round(score, 3), confirmed=score >= threshold
            )
        )
    return out
