from __future__ import annotations

from pydantic import BaseModel, Field


class Claim(BaseModel):
    text: str = Field(description="Утверждение из поста, одним предложением")
    quote: str = Field(
        description="Дословная цитата из текста источника, на которую опирается утверждение"
    )


class CardSpec(BaseModel):
    title: str = Field(description="Заголовок карточки, до 60 знаков")
    subtitle: str = Field(description="Подзаголовок, до 120 знаков")


class PostDraft(BaseModel):
    headline: str = Field(description="Первая строка поста без разметки, до 60 знаков")
    body: str = Field(
        description="Полный текст поста в HTML Telegram: заголовок, абзацы, блок для медийщика, "
        "призыв, источники. Только теги b, i, u, s, a, code, pre, blockquote."
    )
    media_takeaway: str = Field(description="Текст блока «Что это значит для медийщика»")
    source_url: str = Field(description="Ссылка на первоисточник")
    claims: list[Claim] = Field(description="Факты из поста с цитатами из источника, 2–6 штук")
    card: CardSpec
    confidence_notes: list[str] = Field(
        description="Что не удалось подтвердить текстом источника; пусто, если всё подтверждено"
    )
    dates: list[str] = Field(
        default_factory=list,
        description="Все даты и время, упомянутые в посте, «ДД.ММ.ГГГГ, день недели, ЧЧ:ММ»; "
        "пусто, если дат нет",
    )
