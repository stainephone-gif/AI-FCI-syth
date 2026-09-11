from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Topic = Literal["models", "tools", "media", "education", "policy", "research", "other"]


class RankResult(BaseModel):
    relevance: int = Field(
        ge=0, le=100, description="0–100: насколько это важно студенту-медийщику"
    )
    audience_angle: str = Field(description="Одна фраза: чем это касается медиа или образования")
    topic: Topic
    needs_fact_check: bool = Field(
        description="True, если в материале версии моделей, цифры или заявления о превосходстве"
    )
    reason: str = Field(description="Одно предложение: почему такая оценка")
