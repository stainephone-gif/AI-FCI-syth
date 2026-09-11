"""Карточка к посту: HTML-шаблон → Chromium → PNG 1080×1350."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger(__name__)

TEMPLATES = Path(__file__).parent / "templates"
ASSETS = Path(__file__).parent / "assets"
WIDTH, HEIGHT = 1080, 1350

# Цвет плашки темы. Ключи совпадают с Topic из ранжирования.
TOPIC_STYLE = {
    "models": ("Модели", "#6C4BF4"),
    "tools": ("Инструменты", "#0E9F8A"),
    "media": ("Медиа", "#F25C8A"),
    "education": ("Образование", "#E08A00"),
    "policy": ("Правила", "#D64545"),
    "research": ("Исследование", "#2F6FD6"),
    "lab": ("Лаборатория", "#6C4BF4"),
    "other": ("ИИ", "#6B6784"),
}


@dataclass(slots=True)
class CardData:
    title: str
    subtitle: str
    topic: str = "other"
    source_name: str = ""
    date: datetime | None = None
    channel: str = "ИИ • и • ФКИ"
    handle: str = "@ii_i_fki"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )


def _title_class(title: str) -> str:
    n = len(title)
    if n <= 40:
        return "xl"
    if n <= 70:
        return "lg"
    return "md"


def render_html(data: CardData, template: str = "news.html") -> str:
    label, color = TOPIC_STYLE.get(data.topic, TOPIC_STYLE["other"])
    fonts = sorted(p.name for p in (ASSETS / "fonts").glob("*.ttf")) + sorted(
        p.name for p in (ASSETS / "fonts").glob("*.woff2")
    )
    return (
        _env()
        .get_template(template)
        .render(
            data=data,
            topic_label=label,
            topic_color=color,
            title_class=_title_class(data.title),
            date=data.date.strftime("%d.%m.%Y") if data.date else "",
            assets=ASSETS.resolve().as_uri(),
            fonts=fonts,
            width=WIDTH,
            height=HEIGHT,
        )
    )


async def render_png(
    data: CardData, out_path: str | Path, *, chromium_path: str | None = None
) -> Path:
    from playwright.async_api import async_playwright

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(data)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=chromium_path or None, args=["--no-sandbox"]
        )
        try:
            page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
            await page.set_content(html, wait_until="load")
            await page.screenshot(path=str(out), type="png", full_page=False)
        finally:
            await browser.close()
    log.info("Карточка: %s", out)
    return out
