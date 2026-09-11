"""Проверка стиля кодом: то, что стайлгайд запрещает, а модель всё равно делает."""

from __future__ import annotations

import re

from app.bot.telegram_html import strip_tags

MARKERS = {"💜", "🩷", "❗", "📌", "🌟", "🌨️"}
_EMOJI_START = re.compile(r"^\s*([\U0001F300-\U0001FAFF☀-➿⭐✅❌]️?)")
_OURS = re.compile(
    r"\bнаш(?:ем|его|а|е|ей|у|и|их|им)?\s+(?:исследован|разбор|стать|работ|эксперимент|метод)",
    re.IGNORECASE,
)
_OPENERS = ("сегодня поговорим", "в этом посте", "итак")
_CARDS = re.compile(r"в карточках|смотрите карточки|листайте", re.IGNORECASE)


def lint(body_html: str, *, min_len: int = 800, max_len: int = 2500) -> list[str]:
    text = strip_tags(body_html)
    notes: list[str] = []

    n = len(text)
    if n < min_len:
        notes.append(f"короткий пост: {n} знаков, стайлгайд просит от {min_len}")
    elif n > max_len:
        notes.append(f"длинный пост: {n} знаков, стайлгайд просит до {max_len}")

    first = text.strip().splitlines()[0].lower() if text.strip() else ""
    if any(first.startswith(o) for o in _OPENERS):
        notes.append(f"запрещённое начало: «{first[:40]}»")

    if re.search(r"^\s*[-•–]\s+\S", text, re.MULTILINE):
        notes.append("список через дефис или точку: стайлгайд просит эмодзи-маркеры вместо списков")

    if _OURS.search(text):
        notes.append("«наш» про чужую работу: «мы» в посте только про редакцию")

    if _CARDS.search(text):
        notes.append("обещаны карточки, а у поста одна обложка")

    if text.count("!") > 2:
        notes.append(f"восклицательных знаков: {text.count('!')}, стайлгайд допускает два")

    foreign = set()
    for line in text.splitlines():
        m = _EMOJI_START.match(line)
        if m and m.group(1) not in MARKERS:
            foreign.add(m.group(1))
    if foreign:
        notes.append("маркеры не из набора стайлгайда: " + " ".join(sorted(foreign)))

    takeaway = [ln for ln in text.splitlines() if "Что это значит для медийщика" in ln]
    if not takeaway:
        notes.append("нет блока «Что это значит для медийщика»")
    elif not _EMOJI_START.match(takeaway[0]):
        notes.append("блок «Что это значит для медийщика» без эмодзи-маркера в начале")

    return notes
