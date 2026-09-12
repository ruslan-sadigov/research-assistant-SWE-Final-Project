"""Concurrent source collection with per-source deadlines."""

import asyncio
import logging
from time import perf_counter

import httpx

from ai import Source
from researcher.config import Settings
from researcher.interfaces import AIServiceProtocol, SourceCacheProtocol
from researcher.models import CollectionResult, SourceName, SourceOutcome, SourceStatus

logger = logging.getLogger(__name__)


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
            elapsed = perf_counter() - started
            logger.info(
                "collection completed: requested_sources=0 unique_results=0 elapsed_seconds=%.3f",
                elapsed,
            )
            return CollectionResult(elapsed_seconds=elapsed)

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

        elapsed = perf_counter() - started
        logger.info(
            "collection completed: requested_sources=%d unique_results=%d elapsed_seconds=%.3f",
            len(selected),
            len(combined),
            elapsed,
        )
        return CollectionResult(
            outcomes=outcomes,
            sources=combined,
            elapsed_seconds=elapsed,
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
        status: SourceStatus = "failed"
        stage = "cache_read" if use_cache else "fetch"

        try:
            async with deadline:
                if use_cache:
                    try:
                        sources = await self._cache.get_sources(source, question)
                        cache_hit = sources is not None
                        if cache_hit:
                            logger.debug("cache hit: source=%s", source)
                        else:
                            logger.debug("cache miss: source=%s", source)
                    except Exception:
                        logger.warning(
                            "cache read failed: source=%s; attempting fresh fetch", source
                        )
                        warnings.append(
                            f"{source} cache could not be read; fetching fresh evidence."
                        )

                if sources is None:
                    stage = "fetch"
                    sources = await self._ai_service.fetch_sources(source, question, client=client)
                    if use_cache:
                        try:
                            stage = "cache_write"
                            await self._cache.set_sources(source, question, sources)
                        except Exception:
                            logger.warning(
                                "cache write failed: source=%s; fetched evidence retained", source
                            )
                            warnings.append(f"{source} results could not be saved to cache.")
        except TimeoutError:
            logger.warning(
                "source timeout: source=%s stage=%s deadline_expired=%s",
                source,
                stage,
                deadline.expired(),
            )
            status = "timeout" if deadline.expired() else "failed"
            if sources is not None:
                warnings.append(f"{source} cache save exceeded its deadline; evidence retained.")
            else:
                warnings.append(
                    f"{source} exceeded its source deadline."
                    if deadline.expired()
                    else f"{source} reported an upstream timeout."
                )
        except Exception as exc:
            logger.warning(
                "source operation failed: source=%s stage=%s error_type=%s",
                source,
                stage,
                type(exc).__name__,
            )
            warnings.append(f"{source} could not retrieve evidence.")

        # A failed or cancelled cache write must not discard fetched evidence.
        # External cancellation still propagates because it is not an Exception.
        if sources is not None:
            status = "ok" if sources else "empty"

        elapsed = perf_counter() - started
        logger.info(
            "source completed: source=%s status=%s cache_hit=%s results=%d elapsed_seconds=%.3f",
            source,
            status,
            cache_hit,
            len(sources) if sources is not None else 0,
            elapsed,
        )
        return SourceOutcome(
            source=source,
            status=status,
            sources=sources if sources is not None else [],
            elapsed_seconds=elapsed,
            cache_hit=cache_hit,
            warning=" ".join(warnings) or None,
        )
