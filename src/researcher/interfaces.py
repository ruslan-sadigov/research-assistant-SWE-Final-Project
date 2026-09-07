"""Shared contracts for research components.

Concrete implementations and test fakes can satisfy these protocols
without inheriting from them.
"""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

import httpx

from ai import AnswerWithCitations, Source
from researcher.models import CacheEntry, CollectionResult, SourceName


class AIServiceProtocol(Protocol):
    """Fetch evidence and synthesize answers through the supplied AI module."""

    def open_source_client(self) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        """Return an async context manager for a shared HTTP client."""
        ...

    async def fetch_sources(
        self,
        source: SourceName,
        query: str,
        *,
        client: httpx.AsyncClient,
    ) -> list[Source]:
        """Fetch one source using the service's retry policy."""
        ...

    async def synthesize_answer(
        self,
        question: str,
        sources: list[Source],
    ) -> AnswerWithCitations:
        """Synthesize an answer without blocking the event loop."""
        ...


class SourceCacheProtocol(Protocol):
    """Read and write normalized, TTL-aware source results."""

    async def get_sources(
        self,
        source: SourceName,
        query: str,
    ) -> list[Source] | None:
        """Return cached results, or None for a missing or expired entry.

        An empty list is a valid cached result.
        """
        ...

    async def set_sources(
        self,
        source: SourceName,
        query: str,
        sources: list[Source],
    ) -> None:
        """Cache a successful fetch, including an empty result."""
        ...


class CacheStoreProtocol(Protocol):
    """Persist cache entries; normalization and TTL checks belong to the cache."""

    async def get_entry(
        self,
        source: SourceName,
        query_key: str,
    ) -> CacheEntry | None:
        """Return a stored entry, or None if no entry exists."""
        ...

    async def upsert_entry(self, entry: CacheEntry) -> None:
        """Insert or replace the entry for its source and query key."""
        ...

    async def close(self) -> None:
        """Release storage resources."""
        ...


class SourceOrchestratorProtocol(Protocol):
    """Collect evidence from selected sources with graceful degradation."""

    async def collect_sources(
        self,
        question: str,
        selected_sources: list[SourceName],
        *,
        use_cache: bool = True,
    ) -> CollectionResult:
        """Return combined evidence, source outcomes, and timings.

        Preserve successful results when another source fails.
        When use_cache is False, bypass cache reads and writes.
        """
        ...
