"""Cache behaviour across Researcher, SourceOrchestrator, SourceCache and SQLite.

The production components run unchanged; only the AI service is simulated, so
no provider or network is contacted. Each run opens a new store instance, as a
new CLI invocation does.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from ai import AnswerWithCitations, Citation, Source
from researcher import cli
from researcher.concurrency.orchestrator import SourceOrchestrator
from researcher.config import Settings
from researcher.core.researcher import Researcher
from researcher.models import CacheEntry, ResearchResult, SourceName
from researcher.services.cache import SourceCache
from researcher.storage.cache_store import SqliteCacheStore

QUESTION = "What is photosynthesis?"
SOURCES: list[SourceName] = ["wiki", "arxiv", "web"]
ORIGINS = {"wiki": "wikipedia", "arxiv": "arxiv", "web": "web"}
TTL = 600
START = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def evidence(source: str, version: str = "v1") -> Source:
    return Source(
        title=f"{source} evidence {version}",
        url=f"https://example.org/{source}/{version}",
        snippet=f"Simulated {source} result ({version}).",
        origin=ORIGINS[source],
    )


class Clock:
    """Controllable UTC clock, so expiry is tested without sleeping."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class SimulatedAIService:
    """Satisfy AIServiceProtocol with canned evidence and no network access."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.responses: dict[str, list[Source] | Exception] = {
            source: [evidence(source)] for source in SOURCES
        }
        self.fetches: list[str] = []
        self.syntheses: list[str] = []

    @asynccontextmanager
    async def open_source_client(self) -> AsyncIterator[httpx.AsyncClient]:
        def reject(request: httpx.Request) -> httpx.Response:
            raise AssertionError("Cache integration tests must stay offline.")

        async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as client:
            yield client

    async def fetch_sources(
        self, source: SourceName, query: str, *, client: httpx.AsyncClient
    ) -> list[Source]:
        self.fetches.append(source)
        response = self.responses[source]
        if isinstance(response, Exception):
            raise response
        return list(response)

    async def synthesize_answer(
        self, question: str, sources: Sequence[Source]
    ) -> AnswerWithCitations:
        self.syntheses.append(question)
        return AnswerWithCitations(
            question=question,
            answer="Simulated answer [1].",
            citations=[Citation(index=1, source=sources[0])],
        )


class CachedResearch:
    """Run the production pipeline against one SQLite file, one store per run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.clock = Clock()
        self.service = SimulatedAIService()
        self.settings = Settings(per_source_timeout_seconds=5)

    async def run(self, question: str = QUESTION) -> ResearchResult:
        store = SqliteCacheStore(self.path)
        try:
            cache = SourceCache(store, TTL, clock=self.clock)
            orchestrator = SourceOrchestrator(self.settings, self.service, cache)
            researcher = Researcher(orchestrator, self.service)
            return await researcher.research(question, list(SOURCES))
        finally:
            await store.close()

    def rows(self) -> list[tuple[str, str, str]]:
        with closing(sqlite3.connect(self.path)) as conn:
            return conn.execute(
                "SELECT source, query_key, payload FROM cache_entries ORDER BY source, query_key"
            ).fetchall()


def hits(result: ResearchResult) -> dict[str, bool]:
    return {outcome.source: outcome.cache_hit for outcome in result.collection.outcomes}


def statuses(result: ResearchResult) -> dict[str, str]:
    return {outcome.source: outcome.status for outcome in result.collection.outcomes}


@pytest.fixture
def research(tmp_path: Path) -> CachedResearch:
    return CachedResearch(tmp_path / "sources.sqlite3")


@pytest.mark.asyncio
async def test_second_run_is_served_from_sqlite_and_still_synthesizes(
    research: CachedResearch,
) -> None:
    cold = await research.run()
    warm = await research.run()

    assert hits(cold) == {"wiki": False, "arxiv": False, "web": False}
    assert hits(warm) == {"wiki": True, "arxiv": True, "web": True}
    assert sorted(research.service.fetches) == ["arxiv", "web", "wiki"]  # none while warm
    assert research.service.syntheses == [QUESTION, QUESTION]  # answers are never cached
    assert warm.answer is not None
    assert warm.collection.sources == cold.collection.sources
    assert len(research.rows()) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant",
    ["WHAT IS PHOTOSYNTHESIS", "  what   is\tphotosynthesis ?! ", "What is Photosynthesis..."],
)
async def test_reformatted_question_reuses_the_persisted_entries(
    research: CachedResearch, variant: str
) -> None:
    await research.run(QUESTION)
    result = await research.run(variant)

    assert hits(result) == {"wiki": True, "arxiv": True, "web": True}
    assert len(research.service.fetches) == 3
    assert {key for _, key, _ in research.rows()} == {"what is photosynthesis"}


@pytest.mark.asyncio
async def test_different_wording_gets_its_own_entries(research: CachedResearch) -> None:
    await research.run(QUESTION)
    result = await research.run("What is photosynthesis in plants?")

    assert hits(result) == {"wiki": False, "arxiv": False, "web": False}
    assert len(research.service.fetches) == 6
    assert {key for _, key, _ in research.rows()} == {
        "what is photosynthesis",
        "what is photosynthesis in plants",
    }


@pytest.mark.asyncio
async def test_expired_entries_are_refetched_and_replaced_in_place(
    research: CachedResearch,
) -> None:
    await research.run()
    research.clock.advance(TTL - 1)
    assert all(hits(await research.run()).values())

    research.clock.advance(1)  # exactly TTL seconds after the first write
    research.service.responses = {source: [evidence(source, "v2")] for source in SOURCES}
    expired = await research.run()

    assert not any(hits(expired).values())
    assert [source.url for source in expired.collection.sources] == [
        f"https://example.org/{source}/v2" for source in SOURCES
    ]
    entries = [CacheEntry.model_validate_json(payload) for _, _, payload in research.rows()]
    assert len(entries) == 3  # replaced, not appended
    for entry in entries:
        assert entry.created_at == START + timedelta(seconds=TTL)
        assert entry.expires_at == START + timedelta(seconds=2 * TTL)
        assert entry.sources == [evidence(entry.source, "v2")]

    assert all(hits(await research.run()).values())
    assert len(research.service.fetches) == 6


@pytest.mark.asyncio
async def test_empty_result_is_persisted_and_reused_until_it_expires(
    research: CachedResearch,
) -> None:
    # Live full-sentence questions returned no Wikipedia matches.
    research.service.responses["wiki"] = []
    first = await research.run()
    second = await research.run()

    assert statuses(first)["wiki"] == statuses(second)["wiki"] == "empty"
    assert (hits(first)["wiki"], hits(second)["wiki"]) == (False, True)
    assert research.service.fetches.count("wiki") == 1
    assert second.answer is not None  # the other sources still supplied evidence
    wiki_payload = next(payload for source, _, payload in research.rows() if source == "wiki")
    assert CacheEntry.model_validate_json(wiki_payload).sources == []

    research.clock.advance(TTL)
    research.service.responses["wiki"] = [evidence("wiki", "v2")]
    third = await research.run()

    assert (statuses(third)["wiki"], hits(third)["wiki"]) == ("ok", False)
    assert research.service.fetches.count("wiki") == 2


@pytest.mark.asyncio
async def test_failed_source_is_not_persisted_and_is_fetched_again(
    research: CachedResearch,
) -> None:
    research.service.responses["arxiv"] = httpx.ConnectError("simulated outage")
    first = await research.run()

    assert statuses(first) == {"wiki": "ok", "arxiv": "failed", "web": "ok"}
    assert {source for source, _, _ in research.rows()} == {"wiki", "web"}

    research.service.responses["arxiv"] = [evidence("arxiv")]
    second = await research.run()

    assert hits(second) == {"wiki": True, "arxiv": False, "web": True}
    assert statuses(second)["arxiv"] == "ok"
    assert {source for source, _, _ in research.rows()} == {"wiki", "arxiv", "web"}


@pytest.fixture
def cli_services(monkeypatch: pytest.MonkeyPatch) -> list[SimulatedAIService]:
    """Let run_ask build its real cache stack around simulated AI services."""
    created: list[SimulatedAIService] = []

    def build(settings: Settings) -> SimulatedAIService:
        service = SimulatedAIService(settings)
        created.append(service)
        return service

    monkeypatch.setattr(cli, "AIService", build)
    return created


def sqlite_url(path: Path) -> str:
    return "sqlite:///" + path.as_posix()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_corrupt_cache_file_still_produces_an_answer_with_warnings(
    tmp_path: Path,
    cli_services: list[SimulatedAIService],
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "sources.sqlite3"
    path.write_bytes(b"not a SQLite database\n" * 8)
    before = digest(path)
    settings = Settings(database_url=sqlite_url(path), per_source_timeout_seconds=5)

    assert await cli.run_ask(settings, QUESTION, SOURCES, True) == 0

    output = capsys.readouterr().out
    assert "A: Simulated answer [1]." in output
    for source in SOURCES:
        assert f"{source} cache could not be read; fetching fresh evidence." in output
        assert f"{source} results could not be saved to cache." in output
    assert sorted(cli_services[0].fetches) == ["arxiv", "web", "wiki"]
    assert digest(path) == before


@pytest.mark.asyncio
async def test_no_cache_run_leaves_an_existing_database_untouched(
    tmp_path: Path, cli_services: list[SimulatedAIService]
) -> None:
    path = tmp_path / "sources.sqlite3"
    settings = Settings(database_url=sqlite_url(path), per_source_timeout_seconds=5)
    assert await cli.run_ask(settings, QUESTION, SOURCES, True) == 0
    before = (digest(path), path.stat().st_mtime_ns)

    assert await cli.run_ask(settings, QUESTION, SOURCES, False) == 0

    assert (digest(path), path.stat().st_mtime_ns) == before
    assert [len(service.fetches) for service in cli_services] == [3, 3]
    assert [len(service.syntheses) for service in cli_services] == [1, 1]
