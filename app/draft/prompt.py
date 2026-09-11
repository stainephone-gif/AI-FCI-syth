"""Сборка промптов для черновика и правки. Стабильная часть кешируется."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

PROMPT_VERSION = "draft-v1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def style_bundle(prompts_dir: str | Path) -> str:
    """Стайлгайд плюс эталонные посты. Общая часть для draft и edit."""
    base = Path(prompts_dir) / "style"
    parts = []
    guide = _read(base / "styleguide.md")
    if guide:
        parts.append("# Стайлгайд канала\n\n" + guide)
    examples = sorted(p for p in (base / "examples").glob("*.md") if p.name != "README.md")
    for i, p in enumerate(examples, 1):
        parts.append(f"# Эталонный пост {i}\n\n" + _read(p))
    return "\n\n".join(parts)


def draft_system_prompt(prompts_dir: str | Path) -> str:
    return _read(Path(prompts_dir) / "draft.md") + "\n\n" + style_bundle(prompts_dir)


def edit_system_prompt(prompts_dir: str | Path) -> str:
    return _read(Path(prompts_dir) / "edit.md") + "\n\n" + style_bundle(prompts_dir)


def draft_user_prompt(
    *,
    title: str,
    url: str,
    source_name: str,
    published_at: datetime | None,
    audience_angle: str,
    text: str,
) -> str:
    date = published_at.strftime("%d.%m.%Y") if published_at else "не указана"
    return (
        f"Материал для поста.\n"
        f"Источник: {source_name}\n"
        f"Ссылка: {url}\n"
        f"Дата публикации: {date}\n"
        f"Заголовок: {title}\n"
        f"Угол для аудитории (из отбора): {audience_angle or 'не указан'}\n\n"
        f"Текст источника:\n{text.strip()[:16000]}"
    )


def edit_user_prompt(*, previous_body: str, remark: str, source_text: str, url: str) -> str:
    return (
        f"Текущая версия поста:\n{previous_body}\n\n"
        f"Замечание редактора:\n{remark}\n\n"
        f"Ссылка на источник: {url}\n"
        f"Текст источника (для проверки фактов):\n{source_text.strip()[:12000]}"
    )
