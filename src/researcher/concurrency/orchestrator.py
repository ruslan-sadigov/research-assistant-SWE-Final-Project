"""Concurrent source collection with per-source deadlines."""

import asyncio
from time import perf_counter

import httpx

from ai import Source
from researcher.config import Settings
from researcher.interfaces import AIServiceProtocol, SourceCacheProtocol
from researcher.models import CollectionResult, SourceName, SourceOutcome


class SourceOrchestrator:
    """Coordinate source fetching, caching, and partial failures."""

    def __init__(
        self, settings: Settings, ai_service: AIServiceProtocol, cache: SourceCacheProtocol
    ) -> None:
        self._settings = settings
        self._ai_service = ai_service
        self._cache = cache

    async def collect_sources(
        self, question: str, selected_sources: list[SourceName], *, use_cache: bool = True
    ) -> CollectionResult:
        """Collect evidence while preserving successful source results."""
        if use_cache:
            raise NotImplementedError("Cache integration is not implemented yet.")

        started = perf_counter()

        # Preserve requested order while avoiding duplicate fetches.
        selected = list(dict.fromkeys(selected_sources))

        if not selected:
            return CollectionResult(elapsed_seconds=perf_counter() - started)

        async with self._ai_service.open_source_client() as client:
            results = await asyncio.gather(
                *(self._fetch_one(source, question, client=client) for source in selected),
                return_exceptions=True,
            )

        outcomes: list[SourceOutcome] = []

        for result in results:
            # Do not convert cancellation into an ordinary source failure.
            if isinstance(result, BaseException):
                raise result
            outcomes.append(result)

        combined: list[Source] = []
        seen_urls: set[str] = set()

        for outcome in outcomes:
            for source in outcome.sources:
                if source.url not in seen_urls:
                    seen_urls.add(source.url)
                    combined.append(source)

        return CollectionResult(
            outcomes=outcomes,
            sources=combined,
            elapsed_seconds=perf_counter() - started,
        )

    async def _fetch_one(
        self,
        source: SourceName,
        question: str,
        *,
        client: httpx.AsyncClient,
    ) -> SourceOutcome:
        """Fetch one source within its deadline."""
        started = perf_counter()
        deadline = asyncio.timeout(self._settings.per_source_timeout_seconds)

        try:
            async with deadline:
                sources = await self._ai_service.fetch_sources(
                    source,
                    question,
                    client=client,
                )

                outcome = SourceOutcome(
                    source=source,
                    status="ok" if sources else "empty",
                    sources=sources,
                    elapsed_seconds=perf_counter() - started,
                )
        except TimeoutError:
            return SourceOutcome(
                source=source,
                status="timeout" if deadline.expired() else "failed",
                elapsed_seconds=perf_counter() - started,
                warning=(
                    f"{source} exceeded its source deadline."
                    if deadline.expired()
                    else f"{source} reported an upstream timeout."
                ),
            )
        except Exception:
            return SourceOutcome(
                source=source,
                status="failed",
                elapsed_seconds=perf_counter() - started,
                warning=f"{source} could not retrieve evidence.",
            )

        return outcome
