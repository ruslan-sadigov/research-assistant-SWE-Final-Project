"""Offline behavioral tests for cached and uncached source orchestration."""

import asyncio
import logging
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
LOGGER_NAME = "researcher.concurrency.orchestrator"


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


def build(handler, timeout=5, cache=None):
    service = FakeAIService(handler)
    cache = cache if cache is not None else ForbiddenCache()
    settings = Settings(per_source_timeout_seconds=timeout)
    return SourceOrchestrator(settings, service, cache), service, cache


class FakeCache:
    def __init__(self, entries=None, read_error=False, write_error=False, stall=None):
        self.entries = entries or {}
        self.read_error = read_error
        self.write_error = write_error
        self.stall = stall
        self.reads = []
        self.writes = []
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def pause(self, operation):
        if self.stall == operation:
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()

    async def get_sources(self, source, query):
        self.reads.append((source, query))
        await self.pause("read")
        if self.read_error:
            raise RuntimeError("Cache unavailable")
        return self.entries.get(source)

    async def set_sources(self, source, query, sources):
        self.writes.append((source, query, sources))
        await self.pause("write")
        if self.write_error:
            raise RuntimeError("Cache unavailable")
        self.entries[source] = sources


@pytest.mark.asyncio
async def test_cache_hits_empty_hits_and_misses_in_one_collection():
    cache = FakeCache({"wiki": [evidence("wiki")], "arxiv": []})

    async def handler(name):
        return [evidence(name)]

    orchestrator, service, _ = build(handler, cache=cache)
    result = await orchestrator.collect_sources(QUESTION, NAMES)

    assert [outcome.cache_hit for outcome in result.outcomes] == [True, True, False]
    assert [outcome.status for outcome in result.outcomes] == ["ok", "empty", "ok"]
    assert result.sources == [evidence("wiki"), evidence("web")]
    assert [name for name, _, _ in service.calls] == ["web"]
    assert cache.writes == [("web", QUESTION, [evidence("web")])]


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("write_error", [False, True])
async def test_cache_read_failure_fetches_and_preserves_results(empty, write_error):
    cache = FakeCache(read_error=True, write_error=write_error)
    sources = [] if empty else [evidence("wiki")]

    async def handler(name):
        return sources

    orchestrator, _, _ = build(handler, cache=cache)
    result = await orchestrator.collect_sources(QUESTION, ["wiki"])

    assert result.sources == sources
    outcome = result.outcomes[0]
    assert outcome.status == ("empty" if empty else "ok")
    assert not outcome.cache_hit
    assert "could not be read" in outcome.warning
    assert ("could not be saved" in outcome.warning) == write_error
    assert cache.writes == [("wiki", QUESTION, sources)]


@pytest.mark.asyncio
async def test_failed_fetch_is_not_cached():
    cache = FakeCache()

    async def handler(name):
        raise RuntimeError("Fetch failed")

    orchestrator, _, _ = build(handler, cache=cache)
    result = await orchestrator.collect_sources(QUESTION, ["wiki"])

    assert result.outcomes[0].status == "failed"
    assert cache.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["read", "write"])
@pytest.mark.parametrize("empty", [False, True])
async def test_cache_deadline_retains_only_completed_fetches(operation, empty):
    cache = FakeCache(stall=operation)
    sources = [] if empty else [evidence("wiki")]

    async def handler(name):
        return sources

    orchestrator, service, _ = build(handler, timeout=0.05, cache=cache)
    async with asyncio.timeout(2):
        result = await orchestrator.collect_sources(QUESTION, ["wiki"])

    outcome = result.outcomes[0]
    assert cache.cancelled.is_set()
    assert outcome.warning
    assert service.client.is_closed
    if operation == "read":
        assert outcome.status == "timeout"
        assert service.calls == cache.writes == []
    else:
        assert outcome.status == ("empty" if empty else "ok")
        assert result.sources == sources


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["read", "write"])
async def test_external_cancellation_during_cache_access_propagates(operation):
    cache = FakeCache(stall=operation)

    async def handler(name):
        return [evidence(name)]

    orchestrator, service, _ = build(handler, cache=cache)
    task = asyncio.create_task(orchestrator.collect_sources(QUESTION, ["wiki"]))
    try:
        async with asyncio.timeout(2):
            await cache.entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert cache.cancelled.is_set()
    assert service.client.is_closed


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


@pytest.mark.asyncio
async def test_logs_cache_hit_and_collection_summary(caplog):
    cache = FakeCache({"wiki": [evidence("wiki")]})

    async def handler(name):
        raise AssertionError("A cache hit must not fetch.")

    orchestrator, service, _ = build(handler, cache=cache)

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        await orchestrator.collect_sources(QUESTION, ["wiki"])

    assert service.calls == []
    assert "cache hit: source=wiki" in caplog.text
    assert "source=wiki status=ok cache_hit=True results=1" in caplog.text
    assert "requested_sources=1 unique_results=1" in caplog.text


@pytest.mark.asyncio
async def test_logs_failure_without_exposing_exception_text(caplog):
    sensitive_text = "api_key=fake-sensitive-value"

    async def handler(name):
        raise RuntimeError(sensitive_text)

    orchestrator, _, _ = build(handler)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = await orchestrator.collect_sources(QUESTION, ["wiki"], use_cache=False)

    assert result.outcomes[0].status == "failed"
    assert "source=wiki stage=fetch error_type=RuntimeError" in caplog.text
    assert sensitive_text not in caplog.text

    records = [record for record in caplog.records if record.name == LOGGER_NAME]
    assert records
    assert all(record.exc_info is None for record in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["read", "write"])
async def test_logs_cache_timeout_stage(caplog, operation):
    cache = FakeCache(stall=operation)

    async def handler(name):
        return [evidence(name)]

    orchestrator, _, _ = build(handler, timeout=0.05, cache=cache)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        async with asyncio.timeout(2):
            await orchestrator.collect_sources(QUESTION, ["wiki"])

    assert f"source=wiki stage=cache_{operation} deadline_expired=True" in caplog.text


@pytest.mark.asyncio
async def test_logs_cache_failures_and_preserves_evidence(caplog):
    cache = FakeCache(read_error=True, write_error=True)

    async def handler(name):
        return [evidence(name)]

    orchestrator, _, _ = build(handler, cache=cache)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = await orchestrator.collect_sources(QUESTION, ["wiki"])

    assert "cache read failed: source=wiki" in caplog.text
    assert "cache write failed: source=wiki" in caplog.text
    assert result.sources == [evidence("wiki")]
