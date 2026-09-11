"""Tests for the TTL source cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ai import Source
from researcher.services.cache import SourceCache, normalize_query
from researcher.storage.cache_store import InMemoryCacheStore

START = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
TTL = 600


class FakeClock:
    """Controllable clock so expiry is tested without sleeping."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_source(title: str = "Photosynthesis", origin: str = "wikipedia") -> Source:
    return Source(
        title=title,
        url=f"https://example.com/{title.lower().replace(' ', '-')}",
        snippet=f"A short excerpt about {title}.",
        origin=origin,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START)


@pytest.fixture
def cache(clock: FakeClock) -> SourceCache:
    return SourceCache(InMemoryCacheStore(), ttl_seconds=TTL, clock=clock)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("What is photosynthesis?", "what is photosynthesis"),
        ("WHAT IS PHOTOSYNTHESIS", "what is photosynthesis"),
        ("  what   is \n photosynthesis  ", "what is photosynthesis"),
        ("What is photosynthesis!!", "what is photosynthesis"),
        ("What is photosynthesis...", "what is photosynthesis"),
        # Symbols inside the query carry meaning and must survive.
        ("What is C++?", "what is c++"),
        ("Why use C#?", "why use c#"),
        ("Explain A* search.", "explain a* search"),
    ],
)
def test_normalize_query_canonicalises_equivalent_phrasings(raw: str, expected: str) -> None:
    assert normalize_query(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "???", " . "])
def test_normalize_query_rejects_empty_queries(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_query(raw)


def test_rejects_non_positive_ttl() -> None:
    for ttl in (0, -1):
        with pytest.raises(ValueError):
            SourceCache(InMemoryCacheStore(), ttl_seconds=ttl)


@pytest.mark.asyncio
async def test_returns_none_when_nothing_cached(cache: SourceCache) -> None:
    assert await cache.get_sources("wiki", "what is photosynthesis") is None


@pytest.mark.asyncio
async def test_round_trip(cache: SourceCache) -> None:
    sources = [make_source(), make_source("Chloroplast")]
    await cache.set_sources("wiki", "What is photosynthesis?", sources)

    assert await cache.get_sources("wiki", "What is photosynthesis?") == sources


@pytest.mark.asyncio
async def test_equivalent_phrasings_share_one_entry(cache: SourceCache) -> None:
    await cache.set_sources("wiki", "What is Photosynthesis?", [make_source()])

    assert await cache.get_sources("wiki", "  what   is photosynthesis ") is not None


@pytest.mark.asyncio
async def test_empty_result_is_cached_and_differs_from_a_miss(cache: SourceCache) -> None:
    await cache.set_sources("arxiv", "obscure question", [])

    cached = await cache.get_sources("arxiv", "obscure question")
    assert cached == []
    assert cached is not None


@pytest.mark.asyncio
async def test_sources_do_not_collide(cache: SourceCache) -> None:
    await cache.set_sources("wiki", "same question", [make_source()])

    assert await cache.get_sources("arxiv", "same question") is None


@pytest.mark.asyncio
async def test_entry_survives_until_the_ttl_elapses(cache: SourceCache, clock: FakeClock) -> None:
    await cache.set_sources("web", "what is photosynthesis", [make_source(origin="web")])

    clock.advance(TTL - 1)
    assert await cache.get_sources("web", "what is photosynthesis") is not None


@pytest.mark.asyncio
async def test_entry_expires_once_the_ttl_elapses(cache: SourceCache, clock: FakeClock) -> None:
    await cache.set_sources("web", "what is photosynthesis", [make_source(origin="web")])

    clock.advance(TTL)
    assert await cache.get_sources("web", "what is photosynthesis") is None


@pytest.mark.asyncio
async def test_rewriting_an_entry_refreshes_its_ttl(cache: SourceCache, clock: FakeClock) -> None:
    await cache.set_sources("wiki", "question", [make_source()])
    clock.advance(TTL - 1)
    await cache.set_sources("wiki", "question", [make_source("Newer")])
    clock.advance(TTL - 1)

    cached = await cache.get_sources("wiki", "question")
    assert cached is not None
    assert [source.title for source in cached] == ["Newer"]


@pytest.mark.asyncio
async def test_caller_cannot_mutate_the_cached_list(cache: SourceCache) -> None:
    await cache.set_sources("wiki", "question", [make_source()])

    first = await cache.get_sources("wiki", "question")
    assert first is not None
    first.append(make_source("Injected"))

    second = await cache.get_sources("wiki", "question")
    assert second is not None
    assert len(second) == 1


@pytest.mark.asyncio
async def test_rejects_a_naive_clock() -> None:
    naive = SourceCache(InMemoryCacheStore(), ttl_seconds=TTL, clock=lambda: datetime(2026, 1, 1))

    with pytest.raises(ValueError):
        await naive.set_sources("wiki", "question", [make_source()])
