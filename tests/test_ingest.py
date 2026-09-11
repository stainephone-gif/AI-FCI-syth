from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import Database, Item, Post, PostStatus, Source
from app.db.models import SourceKind
from app.ingest import collector, feeds
from app.ingest.normalize import normalize_title, normalize_url, title_hash, url_hash

FIXTURE = Path(__file__).parent / "fixtures" / "feed.xml"


def test_normalize_url_strips_tracking_and_www() -> None:
    a = normalize_url("https://www.example.com/post/?utm_source=x&id=2&fbclid=abc")
    b = normalize_url("https://example.com/post?id=2")
    assert a == b


def test_normalize_url_collapses_arxiv_versions() -> None:
    assert url_hash("https://arxiv.org/abs/2409.01234v2") == url_hash(
        "http://arxiv.org/abs/2409.01234"
    )


def test_title_hash_ignores_case_punctuation_and_stopwords() -> None:
    assert normalize_title("The New Model: GPT-Next!") == normalize_title("new model gpt next")
    assert title_hash("OpenAI releases a new model") == title_hash("OpenAI releases new model.")


def test_parse_feed_fixture(fresh_feed_bytes: bytes) -> None:
    entries = feeds.parse_feed(fresh_feed_bytes)
    assert [e.title for e in entries] == [
        "Anthropic releases Claude Foo",
        "Anthropic Releases Claude Foo!",
        "Old news",
    ]
    assert entries[0].published_at is not None
    assert entries[0].published_at.tzinfo is not None


@pytest.fixture
def fresh_feed_bytes() -> bytes:
    """Фикстура с датами относительно сегодня, чтобы окно свежести работало."""
    now = datetime.now(UTC)
    fresh = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
    old = (now - timedelta(days=10)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    xml = FIXTURE.read_text(encoding="utf-8")
    return xml.replace("FRESH_DATE", fresh).replace("OLD_DATE", old).encode()


async def test_collect_dedupes_and_creates_candidates(
    db: Database, fresh_feed_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with db.session() as s:
        s.add(Source(name="Test", kind=SourceKind.rss, url="https://t/rss"))
        await s.commit()

    async def fake_fetch_feed(client, url):
        return feeds.parse_feed(fresh_feed_bytes)

    async def fake_full_text(client, url):
        return "Полный текст статьи " * 20

    monkeypatch.setattr(feeds, "fetch_feed", fake_fetch_feed)
    monkeypatch.setattr(feeds, "fetch_full_text", fake_full_text)

    report = await collector.collect(db, freshness_hours=72, fetch_full_text=True)
    assert report.sources_polled == 1
    assert report.new_items == 1  # второй заголовок — дубль, третий устарел
    assert report.duplicates == 1
    assert report.stale == 1

    async with db.session() as s:
        items = (await s.scalars(select(Item).order_by(Item.id))).all()
        posts = (await s.scalars(select(Post))).all()
    assert len(items) == 2
    assert items[1].duplicate_of_id == items[0].id
    assert items[0].text.startswith("Полный текст")
    assert len(posts) == 1 and posts[0].status == PostStatus.candidate

    # Повторный сбор ничего не добавляет
    report2 = await collector.collect(db, freshness_hours=72, fetch_full_text=True)
    assert report2.new_items == 0
    async with db.session() as s:
        assert await s.scalar(select(func.count(Item.id))) == 2


async def test_broken_source_does_not_stop_collection(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with db.session() as s:
        s.add(Source(name="Broken", kind=SourceKind.rss, url="https://b/rss"))
        await s.commit()

    async def boom(client, url):
        raise RuntimeError("503")

    monkeypatch.setattr(feeds, "fetch_feed", boom)
    report = await collector.collect(db, freshness_hours=72, fetch_full_text=False)
    assert report.sources_polled == 0
    assert report.errors and "Broken" in report.errors[0]
