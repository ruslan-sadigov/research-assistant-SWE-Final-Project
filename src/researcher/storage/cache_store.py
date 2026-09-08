"""Persistence for cached source results.

Two stores satisfy `CacheStoreProtocol` from `researcher.interfaces`:

* `InMemoryCacheStore` keeps entries in a dictionary. Tests and short-lived
  runs use it; nothing survives process exit.
* `JsonFileCacheStore` writes entries to one JSON document, so the cache
  survives restarts without requiring a database.

Both stores are deliberately dumb: they store and return whatever they are
given. Query normalisation and expiry belong to
`researcher.services.cache.SourceCache`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from researcher.models import CacheEntry, SourceName

logger = logging.getLogger(__name__)

_FILE_FORMAT_VERSION = 1


class CacheStoreError(RuntimeError):
    """Raised when the backing storage cannot be read or written.

    The orchestrator turns this into a warning and keeps fetching, so it must
    stay distinguishable from an ordinary "nothing cached" result.
    """


def _key(source: SourceName, query_key: str) -> tuple[str, str]:
    """The unique key an entry is stored under."""
    return (source, query_key)


class InMemoryCacheStore:
    """Dictionary-backed store used by offline tests and ephemeral runs."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get_entry(self, source: SourceName, query_key: str) -> CacheEntry | None:
        async with self._lock:
            return self._entries.get(_key(source, query_key))

    async def upsert_entry(self, entry: CacheEntry) -> None:
        async with self._lock:
            self._entries[_key(entry.source, entry.query_key)] = entry

    async def close(self) -> None:
        logger.debug("in-memory cache store closed with %d entries", len(self._entries))


class JsonFileCacheStore:
    """Store backed by a single JSON file.

    The document is held in memory and rewritten on every upsert. The cache
    holds one entry per (source, query) pair for a short TTL, so it stays
    small; the simplicity buys atomicity that is easy to verify. Writes go to
    a temporary file in the same directory and are moved into place with
    `os.replace`, which is atomic on POSIX and Windows, so a crash mid-write
    cannot leave a half-written cache behind.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._entries: dict[tuple[str, str], CacheEntry] | None = None
        self._lock = asyncio.Lock()

    async def get_entry(self, source: SourceName, query_key: str) -> CacheEntry | None:
        async with self._lock:
            entries = await self._ensure_loaded()
            return entries.get(_key(source, query_key))

    async def upsert_entry(self, entry: CacheEntry) -> None:
        async with self._lock:
            entries = await self._ensure_loaded()
            entries[_key(entry.source, entry.query_key)] = entry
            await asyncio.to_thread(self._write, list(entries.values()))

    async def close(self) -> None:
        async with self._lock:
            self._entries = None

    async def _ensure_loaded(self) -> dict[tuple[str, str], CacheEntry]:
        if self._entries is None:
            self._entries = await asyncio.to_thread(self._read)
        return self._entries

    def _read(self) -> dict[tuple[str, str], CacheEntry]:
        """Load the document. A missing file is an empty cache, not an error."""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise CacheStoreError(f"could not read cache file {self._path}") from exc

        try:
            document: Any = json.loads(raw)
            entries = [CacheEntry.model_validate(item) for item in document["entries"]]
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
            raise CacheStoreError(f"cache file {self._path} is not readable") from exc

        return {_key(entry.source, entry.query_key): entry for entry in entries}

    def _write(self, entries: list[CacheEntry]) -> None:
        document = {
            "version": _FILE_FORMAT_VERSION,
            "entries": [entry.model_dump(mode="json") for entry in entries],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            )
            try:
                with handle:
                    json.dump(document, handle, ensure_ascii=False, indent=2)
                os.replace(handle.name, self._path)
            except BaseException:
                Path(handle.name).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise CacheStoreError(f"could not write cache file {self._path}") from exc
