"""Offline behavioral tests for uncached source orchestration."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

import httpx
import pytest

from ai import Source
from researcher.concurrency.orchestrator import SourceOrchestrator
from researcher.config import Settings
from researcher.models import SourceName

QUESTION = "What is photosynthesis?"
NAMES: list[SourceName] = ["wiki", "arxiv", "web"]


def evidence(name: SourceName, url: str | None = None) -> Source:
    return Source(
        title=f"Evidence from {name}",
        url=url or f"https://example.com/{name}",
        snippet="An offline excerpt.",
        origin="wikipedia" if name == "wiki" else name,
    )


class ForbiddenCache:
    """Record accesses as well as raising, since fetch errors can be caught."""

    def __init__(self):
        self.calls = []

    async def get_sources(self, source, query):
        self.calls.append(("read", source, query))
        raise AssertionError("The uncached path must not read the cache.")

    async def set_sources(self, source, query, sources):
        self.calls.append(("write", source, query))
        raise AssertionError("The uncached path must not write the cache.")


class FakeAIService:
    def __init__(self, handler: Callable[[SourceName], Awaitable[list[Source]]]):
        self.handler = handler
        self.calls = []
        self.client = None
        self.closed = False
        self.started = {name: asyncio.Event() for name in NAMES}
        self.finished = {name: asyncio.Event() for name in NAMES}
        self.completion_order = []

    @asynccontextmanager
    async def open_source_client(self) -> AsyncIterator[httpx.AsyncClient]:
        def reject_network(request):
            raise AssertionError("Network calls are forbidden in these tests.")

        async with httpx.AsyncClient(transport=httpx.MockTransport(reject_network)) as client:
            self.client = client
            try:
                yield client
            finally:
                self.closed = True

    async def fetch_sources(self, source, query, *, client):
        self.calls.append((source, query, client))
        self.started[source].set()
        try:
            return await self.handler(source)
        finally:
            self.completion_order.append(source)
            self.finished[source].set()

    async def synthesize_answer(self, question, sources):
        raise AssertionError("The orchestrator must not synthesize answers.")


def build(handler, timeout=5):
    service = FakeAIService(handler)
    cache = ForbiddenCache()
    settings = Settings(per_source_timeout_seconds=timeout)
    return SourceOrchestrator(settings, service, cache), service, cache


@pytest.mark.asyncio
async def test_fetches_overlap_share_client_and_preserve_requested_order():
    release = {name: asyncio.Event() for name in NAMES}

    async def handler(name):
        await release[name].wait()
        return [evidence(name)]

    orchestrator, service, cache = build(handler)
    task = asyncio.create_task(orchestrator.collect_sources(QUESTION, NAMES, use_cache=False))
    try:
        # Every fetch must start before any is allowed to finish.
        async with asyncio.timeout(2):
            await asyncio.gather(*(event.wait() for event in service.started.values()))
            for name in reversed(NAMES):
                release[name].set()
                await service.finished[name].wait()
            result = await task
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert service.completion_order == list(reversed(NAMES))
    assert [outcome.source for outcome in result.outcomes] == NAMES
    assert result.sources == [evidence(name) for name in NAMES]
    assert all(query == QUESTION and client is service.client for _, query, client in service.calls)
    assert service.closed and service.client.is_closed
    assert cache.calls == []
    assert result.elapsed_seconds >= 0
    assert all(outcome.elapsed_seconds >= 0 for outcome in result.outcomes)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, TimeoutError])
async def test_source_failure_preserves_other_evidence(failure):
    async def handler(name):
        if name == "arxiv":
            raise failure("Upstream failed")
        return [evidence(name)]

    orchestrator, service, cache = build(handler)
    result = await orchestrator.collect_sources(QUESTION, NAMES, use_cache=False)

    assert [outcome.status for outcome in result.outcomes] == ["ok", "failed", "ok"]
    assert result.sources == [evidence("wiki"), evidence("web")]
    assert result.outcomes[1].warning
    assert result.outcomes[1].sources == []
    assert service.client.is_closed
    assert cache.calls == []


@pytest.mark.asyncio
async def test_deadline_cancels_slow_fetch_and_preserves_success():
    cancelled = asyncio.Event()

    async def handler(name):
        if name == "arxiv":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return [evidence(name)]

    orchestrator, service, _ = build(handler, timeout=0.05)
    async with asyncio.timeout(2):
        result = await orchestrator.collect_sources(QUESTION, NAMES, use_cache=False)

    assert [outcome.status for outcome in result.outcomes] == ["ok", "timeout", "ok"]
    assert result.sources == [evidence("wiki"), evidence("web")]
    assert cancelled.is_set()
    assert result.outcomes[1].warning
    assert service.client.is_closed


@pytest.mark.asyncio
async def test_duplicate_selections_and_urls_are_deduplicated():
    shared_url = "https://example.com/shared"

    async def handler(name):
        return [evidence(name, shared_url), evidence(name)]

    orchestrator, service, _ = build(handler)
    result = await orchestrator.collect_sources(QUESTION, ["web", "wiki", "web"], use_cache=False)

    assert [name for name, _, _ in service.calls] == ["web", "wiki"]
    assert [outcome.source for outcome in result.outcomes] == ["web", "wiki"]
    assert result.sources == [evidence("web", shared_url), evidence("web"), evidence("wiki")]
    assert [len(outcome.sources) for outcome in result.outcomes] == [2, 2]


@pytest.mark.asyncio
async def test_empty_results_are_successful_empty_outcomes():
    async def handler(name):
        return []

    orchestrator, _, cache = build(handler)
    result = await orchestrator.collect_sources(QUESTION, NAMES, use_cache=False)

    assert result.sources == []
    assert [outcome.status for outcome in result.outcomes] == ["empty"] * 3
    assert all(outcome.warning is None and not outcome.cache_hit for outcome in result.outcomes)
    assert cache.calls == []


@pytest.mark.asyncio
async def test_empty_selection_does_not_open_client_or_access_cache():
    async def handler(name):
        raise AssertionError("No fetch should start.")

    orchestrator, service, cache = build(handler)
    result = await orchestrator.collect_sources(QUESTION, [], use_cache=False)

    assert result.outcomes == []
    assert result.sources == []
    assert service.client is None
    assert service.calls == cache.calls == []


@pytest.mark.asyncio
async def test_cancelling_collection_cancels_fetches_and_closes_client():
    cancelled = set()

    async def handler(name):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.add(name)

    orchestrator, service, _ = build(handler)
    task = asyncio.create_task(orchestrator.collect_sources(QUESTION, NAMES, use_cache=False))
    try:
        async with asyncio.timeout(2):
            await asyncio.gather(*(event.wait() for event in service.started.values()))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert cancelled == set(NAMES)
    assert service.closed and service.client.is_closed


@pytest.mark.asyncio
async def test_source_cancellation_is_not_reported_as_a_failure():
    async def handler(name):
        raise asyncio.CancelledError

    orchestrator, service, _ = build(handler)
    with pytest.raises(asyncio.CancelledError):
        await orchestrator.collect_sources(QUESTION, ["wiki"], use_cache=False)

    assert service.closed and service.client.is_closed
