"""Провайдеры моделей. Снаружи нужны только две функции: оценить кандидата и написать пост."""

from __future__ import annotations

from app.config import Settings
from app.draft.writer import WriteFn
from app.rank.ranker import RankFn

PROVIDERS = ("anthropic", "gigachat", "openai")


def build_model_functions(settings: Settings) -> tuple[RankFn, WriteFn]:
    provider = settings.model_provider
    if provider == "anthropic":
        from anthropic import AsyncAnthropic

        from app.draft.writer import make_claude_writer
        from app.rank.ranker import make_claude_ranker

        if not settings.anthropic_api_key:
            raise SystemExit("ANTHROPIC_API_KEY пуст: заполните .env")
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        return (
            make_claude_ranker(client, settings.rank_model_name),
            make_claude_writer(client, settings.draft_model_name, settings.draft_effort),
        )
    if provider == "gigachat":
        from app.llm.gigachat_provider import GigaChatProvider

        p = GigaChatProvider(settings)
        return p.rank_fn(settings.rank_model_name), p.write_fn(settings.draft_model_name)
    if provider == "openai":
        from app.llm.openai_provider import OpenAICompatProvider

        p = OpenAICompatProvider(settings)
        return p.rank_fn(settings.rank_model_name), p.write_fn(settings.draft_model_name)
    raise SystemExit(f"MODEL_PROVIDER={provider!r}: допустимо {', '.join(PROVIDERS)}")


async def list_models(settings: Settings) -> list[str]:
    provider = settings.model_provider
    if provider == "anthropic":
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        return [m.id async for m in client.models.list()]
    if provider == "gigachat":
        from app.llm.gigachat_provider import GigaChatProvider

        return await GigaChatProvider(settings).list_models()
    if provider == "openai":
        from app.llm.openai_provider import OpenAICompatProvider

        return await OpenAICompatProvider(settings).list_models()
    raise SystemExit(f"MODEL_PROVIDER={provider!r}: допустимо {', '.join(PROVIDERS)}")
