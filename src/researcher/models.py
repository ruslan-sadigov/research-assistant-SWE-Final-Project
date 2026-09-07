"""Shared data contracts for the research application"""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai import AnswerWithCitations, Source

SourceName = Literal["wiki", "arxiv", "web"]
SourceStatus = Literal["ok", "empty", "failed", "timeout"]


class AppModel(BaseModel):
    """Reject unexpected fields to catch mistakes between components."""

    model_config = ConfigDict(extra="forbid")


class SourceOutcome(AppModel):
    """Result of collecting evidence from one source."""

    source: SourceName
    status: SourceStatus
    sources: list[Source] = Field(default_factory=list)
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    cache_hit: bool = False
    warning: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "SourceOutcome":
        if self.status == "ok" and not self.sources:
            raise ValueError("An ok outcome must contain sources.")

        if self.status != "ok" and self.sources:
            raise ValueError("Only an ok outcome can contain sources.")

        if self.status in ("failed", "timeout"):
            if not self.warning or not self.warning.strip():
                raise ValueError("A failed or time-out outcome needs a warning.")
            if self.cache_hit:
                raise ValueError("A failed or timed-out outcome cannot be a cache hit.")

        return self


class CollectionResult(AppModel):
    """Combined evidence and individual source outcomes."""

    outcomes: list[SourceOutcome] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)


class ResearchResult(AppModel):
    """Application result, including cases where no answer was produced."""

    question: str = Field(min_length=1)
    answer: AnswerWithCitations | None = None
    collection: CollectionResult
    warnings: list[str] = Field(default_factory=list)


class CacheEntry(AppModel):
    """Stored evidence for a source and normalized query."""

    source: SourceName
    query_key: str = Field(min_length=1)
    sources: list[Source] = Field(default_factory=list)
    created_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_expiration(self) -> "CacheEntry":
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at.")
        return self

    def is_expired(self, now: datetime) -> bool:
        """Use a supplied time so expiration tests remain deterministic."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must include timezone information.")

        return now >= self.expires_at
