"""Tests for shared application models."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from researcher.models import CacheEntry, SourceOutcome


def test_successful_outcome_preserves_sources(sample_sources):
    outcome = SourceOutcome(source="wiki", status="ok", sources=sample_sources, elapsed_seconds=0.5)

    assert outcome.sources == sample_sources
    assert outcome.cache_hit is False


def test_empty_result_can_be_a_cache_hit():
    outcome = SourceOutcome(source="web", status="empty", elapsed_seconds=0, cache_hit=True)

    assert outcome.sources == []
    assert outcome.cache_hit is True


@pytest.mark.parametrize("status", ["failed", "timeout"])
def test_unsuccessful_outcome_preserves_warning(status):
    outcome = SourceOutcome(
        source="arxiv", status=status, elapsed_seconds=1, warning="Source unavailable."
    )

    assert outcome.warning == "Source unavailable."
    assert outcome.sources == []


def test_successful_fetch_can_have_a_cache_warning(sample_sources):
    outcome = SourceOutcome(
        source="wiki",
        status="ok",
        sources=sample_sources,
        elapsed_seconds=0.5,
        warning="Could not save results to cache.",
    )

    assert outcome.sources == sample_sources
    assert outcome.warning is not None


def test_ok_outcome_requires_evidence():
    with pytest.raises(ValidationError, match="must contain sources"):
        SourceOutcome(source="wiki", status="ok", elapsed_seconds=0)


@pytest.mark.parametrize("status", ["empty", "failed", "timeout"])
def test_non_ok_outcome_rejects_evidence(status, sample_sources):
    with pytest.raises(ValidationError, match="Only an ok outcome"):
        SourceOutcome(
            source="wiki",
            status=status,
            sources=sample_sources,
            elapsed_seconds=0,
            warning="Source unavailable.",
        )


@pytest.mark.parametrize("status", ["failed", "timeout"])
@pytest.mark.parametrize("warning", [None, "", "  "])
def test_unsuccessful_outcome_requires_meaningful_warning(status, warning):
    with pytest.raises(ValidationError, match="needs a warning"):
        SourceOutcome(source="web", status=status, elapsed_seconds=0, warning=warning)


@pytest.mark.parametrize("status", ["failed", "timeout"])
def test_unsuccessful_outcome_cannot_be_a_cache_hit(status):
    with pytest.raises(ValidationError, match="cannot be a cache hit"):
        SourceOutcome(
            source="web",
            status=status,
            elapsed_seconds=0,
            warning="Source unavailable.",
            cache_hit=True,
        )


@pytest.mark.parametrize("elapsed", [-1, float("inf"), float("nan")])
def test_outcome_rejects_invalid_duration(elapsed):
    with pytest.raises(ValidationError):
        SourceOutcome(source="web", status="empty", elapsed_seconds=elapsed)


@pytest.fixture
def cache_entry():
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    return CacheEntry(
        source="wiki",
        query_key="what is photosynthesis",
        sources=[],
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
    )


def test_cache_expiration_boundary(cache_entry):
    expiry = cache_entry.expires_at

    assert not cache_entry.is_expired(expiry - timedelta(microseconds=1))
    assert cache_entry.is_expired(expiry)
    assert cache_entry.is_expired(expiry + timedelta(seconds=1))


@pytest.mark.parametrize("offset", [0, -1])
def test_cache_rejects_unpositive_lifetime(offset):
    created_at = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValidationError, match="must be later"):
        CacheEntry(
            source="wiki",
            query_key="photosynthesis",
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=offset),
        )


@pytest.mark.parametrize("field", ["created_at", "expires_at"])
def test_cache_rejects_timestamps_without_timezone(cache_entry, field):
    data = cache_entry.model_dump()
    data[field] = data[field].replace(tzinfo=None)

    with pytest.raises(ValidationError):
        CacheEntry(**data)


def test_expiration_check_rejects_time_without_timezone(cache_entry):
    with pytest.raises(ValueError, match="timezone"):
        cache_entry.is_expired(datetime(2026, 1, 1))


def test_cache_entry_round_trip_preserves_evidence(cache_entry, sample_sources):
    data = cache_entry.model_dump()
    data["sources"] = sample_sources
    original = CacheEntry(**data)

    restored = CacheEntry.model_validate_json(original.model_dump_json())

    assert restored == original
