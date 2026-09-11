"""Приводим HTML от модели к подмножеству, которое принимает Telegram."""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

ALLOWED = {"b", "strong", "i", "em", "u", "s", "del", "code", "pre", "blockquote", "tg-spoiler"}
ALLOWED_WITH_HREF = {"a"}


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("br", "p", "div"):
            self.out.append("\n")
            return
        if tag in ALLOWED:
            self.out.append(f"<{tag}>")
            self.stack.append(tag)
        elif tag in ALLOWED_WITH_HREF:
            href = dict(attrs).get("href") or ""
            if href.startswith(("http://", "https://", "tg://")):
                self.out.append(f'<a href="{escape(href, quote=True)}">')
                self.stack.append("a")
        # остальные теги выбрасываем, текст внутри остаётся

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("p", "div"):
            self.out.append("\n")
            return
        if tag in self.stack:
            # закрываем всё, что открыто после него, чтобы вложенность осталась правильной
            while self.stack:
                top = self.stack.pop()
                self.out.append(f"</{top}>")
                if top == tag:
                    break

    def handle_data(self, data):
        self.out.append(escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        text = "".join(self.out)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def sanitize(html: str) -> str:
    p = _Sanitizer()
    p.feed(html)
    p.close()
    return p.result()


def strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)
