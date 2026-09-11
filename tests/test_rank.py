from pathlib import Path

from sqlalchemy import select

from app.db import Database, Item, Post, PostStatus, Ranking, Source
from app.db.models import SourceKind
from app.ingest.normalize import title_hash, url_hash
from app.rank.prompt import section, system_prompt, user_prompt
from app.rank.ranker import rank_candidates, top_ranked
from app.rank.schemas import RankResult

ROOT = Path(__file__).resolve().parents[1]


def test_system_prompt_includes_editorial_files() -> None:
    text = system_prompt(ROOT / "prompts")
    assert "Оцени один материал" in text
    assert "___" not in text
    assert "Кто читает канал" in text
    assert "Взяли / не взяли" in text


def test_section_extracts_one_heading() -> None:
    md = "# T\n\n## Кто читает\n\nСтудент.\n\n## Тон\n\nСпокойный.\n"
    assert section(md, "Кто читает") == "Студент."
    assert section(md, "Нет такого") == ""


def test_user_prompt_truncates_text() -> None:
    p = user_prompt(title="T", source_name="S", source_note="", text="x" * 10_000)
    assert len(p) < 6500


async def _seed(db: Database, n: int) -> None:
    async with db.session() as s:
        src = Source(name="S", kind=SourceKind.rss, url="https://s/rss", note="зачем")
        s.add(src)
        await s.flush()
        for i in range(n):
            item = Item(
                source_id=src.id,
                url=f"https://s/{i}",
                url_hash=url_hash(f"https://s/{i}"),
                title=f"Новость {i}",
                title_hash=title_hash(f"Новость {i}"),
                text="текст " * 50,
            )
            s.add(item)
            await s.flush()
            s.add(Post(item_id=item.id, status=PostStatus.candidate))
        await s.commit()


async def test_rank_moves_candidates_and_orders_top(db: Database) -> None:
    await _seed(db, 3)
    seen: list[str] = []

    async def fake_rank(system: str, material: str) -> RankResult:
        seen.append(material)
        n = int(material.split("Новость ")[1].split("\n")[0])
        if n == 1:
            raise RuntimeError("модель упала")
        return RankResult(
            relevance=90 if n == 2 else 30,
            audience_angle="угол",
            topic="media",
            needs_fact_check=(n == 2),
            reason="потому",
        )

    report = await rank_candidates(
        db,
        fake_rank,
        prompts_dir=str(ROOT / "prompts"),
        model_name="m",
        concurrency=2,
        min_relevance=60,
    )
    assert report.ranked == 2 and report.failed == 1 and report.above_threshold == 1
    assert all("Зачем источник каналу: зачем" in m for m in seen)

    async with db.session() as s:
        statuses = dict(
            (
                await s.execute(select(Item.title, Post.status).join(Post, Post.item_id == Item.id))
            ).all()
        )
        rankings = (await s.scalars(select(Ranking))).all()
    assert statuses["Новость 0"] == PostStatus.ranked
    assert statuses["Новость 1"] == PostStatus.candidate  # ошибка: остаётся на следующий прогон
    assert statuses["Новость 2"] == PostStatus.ranked
    assert {r.model for r in rankings} == {"m/rank-v2"}

    top = await top_ranked(db, limit=10)
    assert [i.title for i, _ in top] == ["Новость 2", "Новость 0"]
    assert top[0][1].needs_fact_check is True
