from pathlib import Path

from sqlalchemy import select

from app.db import Database, Post, PostStatus, Source
from app.db.models import HUMAN_ONLY_STATUSES, POST_TRANSITIONS
from app.ingest.sources import sync_sources


async def test_sync_sources_is_idempotent(db: Database, tmp_path: Path) -> None:
    f = tmp_path / "sources.yaml"
    f.write_text(
        "sources:\n  - name: A\n    kind: rss\n    url: https://a/rss\n    note: n\n",
        encoding="utf-8",
    )
    assert await sync_sources(db, f) == 1
    f.write_text(
        "sources:\n  - name: A\n    kind: rss\n    url: https://a/rss2\n    enabled: false\n",
        encoding="utf-8",
    )
    assert await sync_sources(db, f) == 1
    async with db.session() as s:
        rows = (await s.scalars(select(Source))).all()
    assert len(rows) == 1
    assert rows[0].url == "https://a/rss2"
    assert rows[0].enabled is False


async def test_repo_sources_file_is_valid(db: Database) -> None:
    root = Path(__file__).resolve().parents[1]
    assert await sync_sources(db, root / "sources.yaml") > 0


def test_every_status_has_transitions_defined() -> None:
    assert set(POST_TRANSITIONS) == set(PostStatus)


def test_only_humans_reach_approved() -> None:
    # Ни одно автоматическое состояние не ведёт в approved напрямую, кроме in_review,
    # где переход делает callback от редактора.
    sources_of_approved = {
        s for s, targets in POST_TRANSITIONS.items() if PostStatus.approved in targets
    }
    assert sources_of_approved == {PostStatus.in_review}
    assert PostStatus.approved in HUMAN_ONLY_STATUSES


def test_post_transition_guard() -> None:
    p = Post(status=PostStatus.drafted)
    assert p.can_move_to(PostStatus.in_review)
    assert not p.can_move_to(PostStatus.published)
    assert not p.can_move_to(PostStatus.approved)
