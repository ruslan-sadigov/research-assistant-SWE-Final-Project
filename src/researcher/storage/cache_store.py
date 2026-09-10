"""Persistence for cached source results.

Two stores satisfy `CacheStoreProtocol` from `researcher.interfaces`:

* `InMemoryCacheStore` keeps entries in a dictionary. Tests and short-lived
  runs use it; nothing survives process exit.
* `SqliteCacheStore` writes entries to a local SQLite database, so the cache
  survives restarts without requiring a separate database service.

Both stores are deliberately dumb: they store and return whatever they are
given. Query normalisation and expiry belong to
`researcher.services.cache.SourceCache`.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from pydantic import ValidationError

from researcher.models import CacheEntry, SourceName

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cache_entries (
    source TEXT NOT NULL,
    query_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (source, query_key)
)
"""


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


class SqliteCacheStore:
    """Store backed by a local SQLite database.

    One row per `(source, query_key)` pair, primary-keyed so `upsert_entry`
    overwrites rather than duplicates. Each `CacheEntry` is stored whole as a
    JSON payload (via pydantic's own `model_dump_json` / `model_validate_json`)
    so the row shape never drifts from the model -- only `source` and
    `query_key` are pulled out as real columns, because those are what
    `get_entry` looks up by.

    `sqlite3` is synchronous, so every database call runs through
    `asyncio.to_thread`. A single connection is reused for the store's
    lifetime and all access is serialised by an `asyncio.Lock`, the same
    approach `InMemoryCacheStore` and the previous JSON-file store used --
    one `sqlite3.Connection` is not safe to use concurrently from multiple
    tasks.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    async def get_entry(self, source: SourceName, query_key: str) -> CacheEntry | None:
        async with self._lock:
            conn = await self._ensure_connection()
            return await asyncio.to_thread(self._select, conn, source, query_key)

    async def upsert_entry(self, entry: CacheEntry) -> None:
        async with self._lock:
            conn = await self._ensure_connection()
            await asyncio.to_thread(self._upsert, conn, entry)

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
                self._conn = None

    async def _ensure_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = await asyncio.to_thread(self._connect)
        return self._conn

    def _connect(self) -> sqlite3.Connection:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.execute(_CREATE_TABLE)
            # Mirrors the old JSON document's "version" field: a place to
            # detect a future schema change instead of misreading it.
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            conn.commit()
            return conn
        except sqlite3.Error as exc:
            raise CacheStoreError(f"could not open cache database {self._path}") from exc

    def _select(
        self,
        conn: sqlite3.Connection,
        source: SourceName,
        query_key: str,
    ) -> CacheEntry | None:
        try:
            row = conn.execute(
                "SELECT payload FROM cache_entries WHERE source = ? AND query_key = ?",
                (source, query_key),
            ).fetchone()
        except sqlite3.Error as exc:
            raise CacheStoreError(f"could not read cache database {self._path}") from exc

        if row is None:
            return None

        try:
            return CacheEntry.model_validate_json(row[0])
        except ValidationError as exc:
            raise CacheStoreError(f"cache database {self._path} is not readable") from exc

    def _upsert(self, conn: sqlite3.Connection, entry: CacheEntry) -> None:
        try:
            conn.execute(
                """
                INSERT INTO cache_entries (source, query_key, payload)
                VALUES (?, ?, ?)
                ON CONFLICT (source, query_key) DO UPDATE SET payload = excluded.payload
                """,
                (entry.source, entry.query_key, entry.model_dump_json()),
            )
            conn.commit()
        except sqlite3.Error as exc:
            raise CacheStoreError(f"could not write cache database {self._path}") from exc
