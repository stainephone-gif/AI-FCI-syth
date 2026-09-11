"""Сборка промпта ранжирования. Стабильная часть (кешируется) отдельно от материала."""

from __future__ import annotations

from pathlib import Path

PROMPT_VERSION = "rank-v2"


def section(markdown: str, heading: str) -> str:
    """Текст раздела `## heading` до следующего заголовка того же уровня."""
    lines = markdown.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip().lower() == heading.lower()
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def system_prompt(prompts_dir: str | Path) -> str:
    base = Path(prompts_dir)
    parts = [(base / "rank.md").read_text(encoding="utf-8")]
    styleguide = base / "style" / "styleguide.md"
    if styleguide.exists():
        audience = section(styleguide.read_text(encoding="utf-8"), "Кто читает")
        if audience and "___" not in audience:
            parts.append("Кто читает канал (из стайлгайда редакции):\n" + audience)
    examples = base / "style" / "relevance-examples.md"
    if examples.exists():
        text = examples.read_text(encoding="utf-8")
        if "___" not in text:  # шаблон ещё не заполнен редакцией
            parts.append(text)
    return "\n\n".join(parts)


def user_prompt(*, title: str, source_name: str, source_note: str, text: str) -> str:
    body = text.strip()[:6000]
    return (
        f"Источник: {source_name}\n"
        f"Зачем источник каналу: {source_note or 'не указано'}\n"
        f"Заголовок: {title}\n\n"
        f"Текст:\n{body}"
    )
