"""TTL cache for per-source research results.

`SourceCache` satisfies `SourceCacheProtocol` from `researcher.interfaces`.
It sits between the orchestrator and a `CacheStoreProtocol` implementation and
owns two decisions the store knows nothing about:

* which questions count as the same question (normalisation), and
* when a stored result has gone stale (time to live).

Only successful fetches are cached. An empty result is a success -- the source
was reached and had nothing to offer -- and is cached as such. Failures and
timeouts never reach `set_sources`; the orchestrator only records outcomes it
received cleanly.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone

from ai import Source
from researcher.interfaces import CacheStoreProtocol
from researcher.models import CacheEntry, SourceName

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")
_TRAILING_PUNCTUATION = "?!. \t\n"


def utc_now() -> datetime:
    """Default clock. Injectable so tests control expiry without sleeping."""
    return datetime.now(timezone.utc)


def normalize_query(query: str) -> str:
    """Reduce a question to the key its cache entry is stored under.

    "What is Photosynthesis?" and "  what   is photosynthesis " both become
    "what is photosynthesis". Only trailing sentence punctuation is stripped,
    so symbols that carry meaning inside a query survive: "What is C++?"
    becomes "what is c++", not "what is c".

    Raises:
        ValueError: if nothing meaningful is left after normalisation.
    """
    collapsed = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", query)).strip()
    key = collapsed.rstrip(_TRAILING_PUNCTUATION).casefold()
    if not key:
        raise ValueError("query must contain more than whitespace and punctuation")
    return key


def no_fetch_settings(source: SourceName) -> dict[str, str | int]:
    """Default for callers whose fetches do not depend on configuration."""
    return {}


class SourceCache:
    """Cache keyed by (source, normalised query) with a fixed time to live.

    Each entry also records the fetch settings (such as the result limit or web
    search provider) it was fetched with. An entry recorded under different
    settings is a miss, so a configuration change refetches instead of serving
    results the current configuration would not produce.
    """

    def __init__(
        self,
        store: CacheStoreProtocol,
        ttl_seconds: int,
        *,
        clock: Callable[[], datetime] = utc_now,
        fetch_settings: Callable[[SourceName], dict[str, str | int]] = no_fetch_settings,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        self._store = store
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._fetch_settings = fetch_settings

    async def get_sources(self, source: SourceName, query: str) -> list[Source] | None:
        """Return cached sources, or None when there is no usable entry.

        None means "nothing cached, go and fetch". An empty list means "this
        source was already asked and genuinely had nothing" -- the two are not
        interchangeable.
        """
        query_key = normalize_query(query)
        entry = await self._store.get_entry(source, query_key)

        if entry is None:
            logger.debug("cache miss: source=%s key=%r", source, query_key)
            return None

        if entry.is_expired(self._now()):
            logger.debug("cache expired: source=%s key=%r", source, query_key)
            return None

        if entry.fetch_settings != self._fetch_settings(source):
            logger.debug("cache settings changed: source=%s key=%r", source, query_key)
            return None

        logger.debug("cache hit: source=%s key=%r n=%d", source, query_key, len(entry.sources))
        # Copy so a caller cannot mutate the list held by the store.
        return list(entry.sources)

    async def set_sources(self, source: SourceName, query: str, sources: Sequence[Source]) -> None:
        """Store a successful fetch result, replacing any existing entry."""
        query_key = normalize_query(query)
        created_at = self._now()
        entry = CacheEntry(
            source=source,
            query_key=query_key,
            sources=list(sources),
            fetch_settings=self._fetch_settings(source),
            created_at=created_at,
            expires_at=created_at + self._ttl,
        )
        await self._store.upsert_entry(entry)
        logger.debug(
            "cache write: source=%s key=%r n=%d expires_at=%s",
            source,
            query_key,
            len(entry.sources),
            entry.expires_at.isoformat(),
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock must return timezone-aware datetimes")
        return now.astimezone(timezone.utc)
