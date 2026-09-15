"""Check the live benchmark's collection paths without making real requests."""

from contextlib import asynccontextmanager

import pytest

from ai import Source
from benchmarks.benchmark_live_sources import collect
from researcher.concurrency.orchestrator import SourceOrchestrator
from researcher.config import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sequential", "parallel"])
async def test_live_benchmark_preserves_partial_results_and_shared_client(mode):
    shared_client = object()
    calls = []

    class Service:
        @asynccontextmanager
        async def open_source_client(self):
            yield shared_client

        async def fetch_sources(self, source, query, *, client):
            assert client is shared_client
            assert query == "Question"
            calls.append(source)
            if source == "arxiv":
                raise ValueError("Simulated failure")
            return [
                Source(
                    title=source,
                    url="https://example.com/shared",
                    snippet="Evidence",
                    origin="wikipedia" if source == "wiki" else source,
                )
            ]

    class ForbiddenCache:
        async def get_sources(self, *args):
            pytest.fail("Benchmark must bypass cache reads")

        async def set_sources(self, *args):
            pytest.fail("Benchmark must bypass cache writes")

    orchestrator = SourceOrchestrator(Settings(), Service(), ForbiddenCache())
    result = await collect(orchestrator, "Question", mode)
    assert sorted(calls) == ["arxiv", "web", "wiki"]
    assert [outcome.status for outcome in result.outcomes] == ["ok", "failed", "ok"]
    assert len(result.sources) == 1
    assert result.sources[0].origin == "wikipedia"
    assert result.elapsed_seconds >= 0
