import pytest
from pydantic import BaseModel

from app.config import Settings
from app.llm.structured import extract_json, schema_instructions, structured_call


class Tiny(BaseModel):
    score: int
    note: str


def test_extract_json_handles_fences_and_prose() -> None:
    assert extract_json('Вот ответ:\n```json\n{"a": 1}\n```\nготово') == '{"a": 1}'
    assert extract_json('{"a": {"b": 2}} хвост') == '{"a": {"b": 2}}'
    with pytest.raises(ValueError):
        extract_json("никакого json")


def test_schema_instructions_mention_fields() -> None:
    text = schema_instructions(Tiny)
    assert '"score"' in text and '"note"' in text and "JSON" in text


async def test_structured_call_retries_with_error_feedback() -> None:
    replies = iter(['{"score": "много"}', 'ответ: {"score": 7, "note": "ок"}'])
    seen: list[list[dict]] = []

    async def chat(messages):
        seen.append(list(messages))
        return next(replies)

    result = await structured_call(chat, "system", "user", Tiny, retries=2)
    assert result == Tiny(score=7, note="ок")
    assert len(seen) == 2
    assert seen[0][0]["role"] == "system" and "Шаблон:" in seen[0][0]["content"]
    assert seen[1][-1]["role"] == "user" and "не соответствует формату" in seen[1][-1]["content"]


async def test_structured_call_gives_up() -> None:
    async def chat(messages):
        return "не json"

    with pytest.raises(RuntimeError):
        await structured_call(chat, "s", "u", Tiny, retries=1)


def test_default_models_per_provider(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    s = Settings(_env_file=None, model_provider="gigachat")
    assert (s.rank_model_name, s.draft_model_name) == ("GigaChat-2", "GigaChat-2-Max")
    s = Settings(_env_file=None, model_provider="anthropic", draft_model="claude-sonnet-5")
    assert (s.rank_model_name, s.draft_model_name) == ("claude-haiku-4-5", "claude-sonnet-5")
    s = Settings(_env_file=None, model_provider="openai")
    with pytest.raises(SystemExit):
        _ = s.rank_model_name


def test_gigachat_provider_requires_credentials(monkeypatch) -> None:
    from app.llm import build_model_functions

    monkeypatch.setenv("BOT_TOKEN", "1:x")
    s = Settings(_env_file=None, model_provider="gigachat")
    with pytest.raises(SystemExit):
        build_model_functions(s)
    s = Settings(_env_file=None, model_provider="gigachat", gigachat_credentials="abc")
    rank_fn, write_fn = build_model_functions(s)
    assert callable(rank_fn) and callable(write_fn)


def test_template_and_notes_for_rank_schema() -> None:
    from app.llm.structured import field_notes, template_from_model
    from app.rank.schemas import RankResult

    t = template_from_model(RankResult)
    assert t == {
        "relevance": 0,
        "audience_angle": "...",
        "topic": "models",
        "needs_fact_check": False,
        "reason": "...",
    }
    notes = field_notes(RankResult)
    assert "topic (одно из: 'models'" in notes and "needs_fact_check (bool)" in notes


def test_template_for_nested_post_schema() -> None:
    from app.draft.schemas import PostDraft
    from app.llm.structured import template_from_model

    t = template_from_model(PostDraft)
    assert t["claims"] == [{"text": "...", "quote": "..."}]
    assert t["card"] == {"title": "...", "subtitle": "..."}
    assert t["dates"] == ["..."] and t["confidence_notes"] == ["..."]


async def test_schema_echo_gets_specific_feedback() -> None:
    from app.rank.schemas import RankResult

    replies = iter(
        [
            '{"properties": {"relevance": {"type": "integer"}}}',
            '{"relevance": 80, "audience_angle": "a", "topic": "media", '
            '"needs_fact_check": false, "reason": "r"}',
        ]
    )
    seen = []

    async def chat(messages):
        seen.append(list(messages))
        return next(replies)

    r = await structured_call(chat, "s", "u", RankResult)
    assert r.relevance == 80
    assert "вернул описание схемы" in seen[1][-1]["content"]
    assert (
        "Шаблон:" in seen[0][0]["content"]
        and "Не возвращай описание схемы" in seen[0][0]["content"]
    )
