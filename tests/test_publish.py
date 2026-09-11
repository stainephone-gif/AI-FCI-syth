import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import Settings
from app.db import Database, Draft, Event, Item, Post, PostStatus, Source
from app.db.models import SourceKind
from app.ingest.normalize import title_hash, url_hash
from app.publish.publisher import Publisher, render_channel_text
from app.publish.scheduler import Scheduler


class FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    async def send_text(self, channel_id: str, html: str) -> int:
        if self.fail:
            raise RuntimeError("network down")
        self.sent.append((channel_id, html))
        return 1000 + len(self.sent)

    async def send_photo(self, channel_id: str, photo_path: str, caption_html: str) -> int:
        self.sent.append((channel_id, f"[photo {photo_path}] {caption_html}"))
        return 2000 + len(self.sent)


async def _seed(db: Database, status: PostStatus, when=None, card=None, body=None) -> int:
    key = uuid.uuid4().hex[:8]
    async with db.session() as s:
        src = Source(name=f"S{key}", kind=SourceKind.rss, url="https://s/rss")
        s.add(src)
        await s.flush()
        url = f"https://s/{key}"
        item = Item(
            source_id=src.id,
            url=url,
            url_hash=url_hash(url),
            title=f"T{key}",
            title_hash=title_hash(f"T{key}"),
            text="txt",
        )
        s.add(item)
        await s.flush()
        draft = Draft(
            item_id=item.id,
            version=1,
            card_path=card,
            post_json={"body": body or "<b>Пост</b>\n\nТекст <script>x</script>."},
        )
        s.add(draft)
        await s.flush()
        post = Post(
            item_id=item.id, draft_id=draft.id, status=status, scheduled_at=when, approved_by=111
        )
        s.add(post)
        await s.commit()
        return post.id


def _publisher(db, settings, sender, notes=None):
    async def notify(text):
        (notes if notes is not None else []).append(text)

    return Publisher(db, Scheduler(settings.tz), sender, settings, notify=notify)


async def test_publish_is_idempotent_and_sanitized(db: Database, settings: Settings) -> None:
    settings.channel_id = "@chan"
    settings.post_footer = "<i>Черновик написал ИИ</i>"
    pid = await _seed(db, PostStatus.scheduled)
    sender, notes = FakeSender(), []
    pub = _publisher(db, settings, sender, notes)

    r1 = await pub.publish(pid)
    r2 = await pub.publish(pid)
    assert r1.outcome == "published" and r2.outcome == "skipped"
    assert len(sender.sent) == 1
    channel, html = sender.sent[0]
    assert channel == "@chan"
    assert "<script>" not in html and "<b>Пост</b>" in html
    assert html.endswith("<i>Черновик написал ИИ</i>")
    assert notes == [f"#{pid} опубликован: https://t.me/chan/1001."]

    async with db.session() as s:
        post = await s.get(Post, pid)
        actions = (await s.scalars(select(Event.action).where(Event.post_id == pid))).all()
    assert post.status == PostStatus.published and post.channel_message_id == 1001
    assert post.published_at is not None and post.publish_key
    assert actions == ["published"]


async def test_unapproved_post_is_never_sent(db: Database, settings: Settings) -> None:
    sender = FakeSender()
    pub = _publisher(db, settings, sender)
    for status in (
        PostStatus.in_review,
        PostStatus.drafted,
        PostStatus.rejected,
        PostStatus.published,
    ):
        pid = await _seed(db, status)
        assert (await pub.publish(pid)).outcome == "skipped"
    assert sender.sent == []


async def test_failure_releases_lock_and_notifies(db: Database, settings: Settings) -> None:
    pid = await _seed(db, PostStatus.scheduled)
    notes = []
    pub = _publisher(db, settings, FakeSender(fail=True), notes)
    assert (await pub.publish(pid)).outcome == "failed"
    assert notes and "не удалась" in notes[0] and f"/publish {pid}" in notes[0]
    async with db.session() as s:
        post = await s.get(Post, pid)
    assert post.status == PostStatus.scheduled and post.publish_key is None

    # после починки повторная попытка проходит
    pub.sender = FakeSender()
    assert (await pub.publish(pid)).outcome == "published"


async def test_card_goes_as_photo_when_caption_fits(db: Database, settings: Settings) -> None:
    pid = await _seed(db, PostStatus.approved, card="/tmp/card.png")
    sender = FakeSender()
    await _publisher(db, settings, sender).publish(pid)
    assert len(sender.sent) == 1
    assert sender.sent[0][1].startswith("[photo /tmp/card.png] <b>Пост</b>")


async def test_long_text_with_card_sends_photo_then_text(db: Database, settings: Settings) -> None:
    pid = await _seed(db, PostStatus.approved, card="/tmp/card.png", body="x" * 1500)
    sender = FakeSender()
    r = await _publisher(db, settings, sender).publish(pid)
    assert [m[1][:18] for m in sender.sent] == ["[photo /tmp/card.p", "xxxxxxxxxxxxxxxxxx"]
    assert r.message_id == 1002  # id текстового сообщения, оно и есть пост


async def test_schedule_cancel_and_rearm(db: Database, settings: Settings) -> None:
    when = datetime.now(UTC) + timedelta(hours=3)
    pid = await _seed(db, PostStatus.approved)
    pub = _publisher(db, settings, FakeSender())

    await pub.schedule(pid, when)
    assert pub.scheduler.has(Publisher.job_id(pid))
    async with db.session() as s:
        assert (await s.get(Post, pid)).status == PostStatus.scheduled

    assert await pub.cancel(pid, actor=222) is True
    assert not pub.scheduler.has(Publisher.job_id(pid))
    async with db.session() as s:
        post = await s.get(Post, pid)
    assert post.status == PostStatus.in_review and post.approved_by is None
    assert await pub.cancel(pid, actor=222) is False

    # перевооружение при старте: свежий approved ставится, давно просроченный нет
    fresh = await _seed(db, PostStatus.approved, when=when)
    stale = await _seed(db, PostStatus.approved, when=datetime.now(UTC) - timedelta(days=1))
    armed, missed = await pub.rearm_from_db()
    assert armed == [fresh] and missed == [stale]
    assert pub.scheduler.has(Publisher.job_id(fresh))


def test_render_channel_text_truncates() -> None:
    post = Post(draft=Draft(post_json={"body": "x" * 5000}))
    assert len(render_channel_text(post, "")) == 4096
