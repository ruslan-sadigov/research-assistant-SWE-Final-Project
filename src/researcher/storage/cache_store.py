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
import threading
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
    lifetime. A thread lock covers complete database operations, including
    initialization and closure. Cancelling an await does not stop its worker,
    so serialization must live inside the worker rather than the coroutine.
    A cancelled write may still commit. close() permanently closes the store.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._closed = False

    async def get_entry(self, source: SourceName, query_key: str) -> CacheEntry | None:
        return await asyncio.to_thread(self._get_entry, source, query_key)

    def _get_entry(self, source: SourceName, query_key: str) -> CacheEntry | None:
        with self._lock:
            return self._select(self._ensure_connection(), source, query_key)

    async def upsert_entry(self, entry: CacheEntry) -> None:
        snapshot = entry.model_copy(deep=True)
        await asyncio.to_thread(self._upsert_entry, snapshot)

    def _upsert_entry(self, entry: CacheEntry) -> None:
        with self._lock:
            self._upsert(self._ensure_connection(), entry)

    async def close(self) -> None:
        await asyncio.to_thread(self._close)

    def _close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error as exc:
                    raise CacheStoreError("could not close cache database") from exc
                self._conn = None
            self._closed = True

    def _ensure_connection(self) -> sqlite3.Connection:
        if self._closed:
            raise CacheStoreError("cache store is closed")
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def _connect(self) -> sqlite3.Connection:
        conn = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, check_same_thread=False)
            with conn:
                # Serialize initialization against other store instances.
                conn.execute("BEGIN IMMEDIATE")
                (version,) = conn.execute("PRAGMA user_version").fetchone()
                if version not in (0, _SCHEMA_VERSION):
                    raise CacheStoreError(f"unsupported cache schema version: {version}")
                if version == 0:
                    conn.execute(_CREATE_TABLE)
                columns = conn.execute("PRAGMA table_info(cache_entries)").fetchall()
                shape = [(row[1], row[2].upper(), row[3], row[5]) for row in columns]
                if shape != [
                    ("source", "TEXT", 1, 1),
                    ("query_key", "TEXT", 1, 2),
                    ("payload", "TEXT", 1, 0),
                ]:
                    raise CacheStoreError("incompatible cache table schema")
                if version == 0:
                    conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return conn
        except (OSError, sqlite3.Error, CacheStoreError) as exc:
            if conn is not None:
                conn.close()
            if isinstance(exc, CacheStoreError):
                raise
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
            with conn:
                conn.execute(
                    """
                    INSERT INTO cache_entries (source, query_key, payload)
                    VALUES (?, ?, ?)
                    ON CONFLICT (source, query_key) DO UPDATE SET payload = excluded.payload
                    """,
                    (entry.source, entry.query_key, entry.model_dump_json()),
                )
        except sqlite3.Error as exc:
            raise CacheStoreError(f"could not write cache database {self._path}") from exc
