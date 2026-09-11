"""Провайдеры моделей. Снаружи нужны только две функции: оценить кандидата и написать пост."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.draft.quotes import SYSTEM as QUOTES_SYSTEM
from app.draft.quotes import FixQuotesFn, QuoteFixes, apply_fixes
from app.draft.quotes import user_prompt as quotes_prompt
from app.draft.schemas import Claim
from app.draft.writer import WriteFn
from app.rank.ranker import RankFn

PROVIDERS = ("anthropic", "gigachat", "openai")


@dataclass(slots=True)
class ModelFunctions:
    rank: RankFn
    write: WriteFn
    fix_quotes: FixQuotesFn

    def __iter__(self):  # позволяет писать rank_fn, write_fn = build_model_functions(...)
        yield self.rank
        yield self.write


def _fix_quotes_from_structured(structured, model: str) -> FixQuotesFn:
    async def fix(source_text: str, claims: list[Claim]) -> list[Claim]:
        fixes = await structured(
            model, QUOTES_SYSTEM, quotes_prompt(source_text, claims), QuoteFixes
        )
        return apply_fixes(claims, fixes)

    return fix


def build_model_functions(settings: Settings) -> ModelFunctions:
    provider = settings.model_provider
    if provider == "anthropic":
        from anthropic import AsyncAnthropic

        from app.draft.writer import make_claude_writer
        from app.rank.ranker import make_claude_ranker

        if not settings.anthropic_api_key:
            raise SystemExit(
                "MODEL_PROVIDER=anthropic, но ANTHROPIC_API_KEY пуст. Для GigaChat добавьте в .env "
                "строку MODEL_PROVIDER=gigachat и GIGACHAT_CREDENTIALS=..."
            )
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)

        async def structured(model, system, user, schema):
            r = await client.messages.parse(
                model=model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
            return r.parsed_output

        return ModelFunctions(
            rank=make_claude_ranker(client, settings.rank_model_name),
            write=make_claude_writer(client, settings.draft_model_name, settings.draft_effort),
            fix_quotes=_fix_quotes_from_structured(structured, settings.rank_model_name),
        )
    if provider == "gigachat":
        from app.llm.gigachat_provider import GigaChatProvider

        p = GigaChatProvider(settings)
    elif provider == "openai":
        from app.llm.openai_provider import OpenAICompatProvider

        p = OpenAICompatProvider(settings)
    else:
        raise SystemExit(f"MODEL_PROVIDER={provider!r}: допустимо {', '.join(PROVIDERS)}")
    return ModelFunctions(
        rank=p.rank_fn(settings.rank_model_name),
        write=p.write_fn(settings.draft_model_name),
        fix_quotes=_fix_quotes_from_structured(p.structured, settings.rank_model_name),
    )


async def list_models(settings: Settings) -> list[str]:
    provider = settings.model_provider
    if provider == "anthropic":
        from anthropic import AsyncAnthropic

        if not settings.anthropic_api_key:
            raise SystemExit(
                "MODEL_PROVIDER=anthropic, но ANTHROPIC_API_KEY пуст. Для GigaChat добавьте в .env "
                "строку MODEL_PROVIDER=gigachat и GIGACHAT_CREDENTIALS=..."
            )
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        return [m.id async for m in client.models.list()]
    if provider == "gigachat":
        from app.llm.gigachat_provider import GigaChatProvider

        return await GigaChatProvider(settings).list_models()
    if provider == "openai":
        from app.llm.openai_provider import OpenAICompatProvider

        return await OpenAICompatProvider(settings).list_models()
    raise SystemExit(f"MODEL_PROVIDER={provider!r}: допустимо {', '.join(PROVIDERS)}")
