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
        started = perf_counter()

        # Preserve requested order while avoiding duplicate fetches.
        selected = list(dict.fromkeys(selected_sources))

        if not selected:
            return CollectionResult(elapsed_seconds=perf_counter() - started)

        async with self._ai_service.open_source_client() as client:
            results = await asyncio.gather(
                *(
                    self._fetch_one(source, question, client=client, use_cache=use_cache)
                    for source in selected
                ),
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
        use_cache: bool,
    ) -> SourceOutcome:
        """Read, fetch, and save one source within a single deadline."""
        started = perf_counter()
        deadline = asyncio.timeout(self._settings.per_source_timeout_seconds)
        sources: list[Source] | None = None
        cache_hit = False
        warnings: list[str] = []
        status = "failed"

        try:
            async with deadline:
                if use_cache:
                    try:
                        sources = await self._cache.get_sources(source, question)
                        cache_hit = sources is not None
                    except Exception:
                        warnings.append(
                            f"{source} cache could not be read; fetching fresh evidence."
                        )

                if sources is None:
                    sources = await self._ai_service.fetch_sources(source, question, client=client)
                    if use_cache:
                        try:
                            await self._cache.set_sources(source, question, sources)
                        except Exception:
                            warnings.append(f"{source} results could not be saved to cache.")
        except TimeoutError:
            status = "timeout" if deadline.expired() else "failed"
            if sources is not None:
                warnings.append(f"{source} cache save exceeded its deadline; evidence retained.")
            else:
                warnings.append(
                    f"{source} exceeded its source deadline."
                    if deadline.expired()
                    else f"{source} reported an upstream timeout."
                )
        except Exception:
            warnings.append(f"{source} could not retrieve evidence.")

        # A failed or cancelled cache write must not discard fetched evidence.
        # External cancellation still propagates because it is not an Exception.
        if sources is not None:
            status = "ok" if sources else "empty"

        return SourceOutcome(
            source=source,
            status=status,
            sources=sources if sources is not None else [],
            elapsed_seconds=perf_counter() - started,
            cache_hit=cache_hit,
            warning=" ".join(warnings) or None,
        )
