"""On-disk layout and error contract of the SQLite cache store.

docs/caching.md and docs/cache-verification.md describe this layout. If a test
here fails because the format changed on purpose, bump the schema version and
update those documents together with the assertion.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai import Source
from researcher.models import CacheEntry
from researcher.services.cache import SourceCache
from researcher.storage.cache_store import CacheStoreError, SqliteCacheStore

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
TTL = 600
ORIGINS = {"wiki": "wikipedia", "arxiv": "arxiv", "web": "web"}


def make_source(source: str) -> Source:
    return Source(
        title=f"{source} evidence",
        url=f"https://example.org/{source}",
        snippet=f"Simulated {source} result.",
        origin=ORIGINS[source],
    )


async def cache_every_source(path: Path, question: str) -> None:
    store = SqliteCacheStore(path)
    try:
        cache = SourceCache(store, TTL, clock=lambda: NOW)
        for source in ORIGINS:
            await cache.set_sources(source, question, [make_source(source)])
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_new_database_has_the_documented_schema(tmp_path: Path) -> None:
    path = tmp_path / "sources.sqlite3"
    await cache_every_source(path, "What is photosynthesis?")

    with closing(sqlite3.connect(path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()
        objects = conn.execute(
            "SELECT type, name, tbl_name FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        columns = [
            (name, declared_type, not_null, key_position)
            for _, name, declared_type, not_null, _, key_position in conn.execute(
                "PRAGMA table_info(cache_entries)"
            )
        ]
        indexes = [
            (name, unique, origin)
            for _, name, unique, origin, _ in conn.execute("PRAGMA index_list(cache_entries)")
        ]
        indexed_columns = [
            row[2] for row in conn.execute("PRAGMA index_info(sqlite_autoindex_cache_entries_1)")
        ]
        foreign_keys = conn.execute("PRAGMA foreign_key_list(cache_entries)").fetchall()

    assert version == (1,)
    assert objects == [
        ("index", "sqlite_autoindex_cache_entries_1", "cache_entries"),
        ("table", "cache_entries", "cache_entries"),
    ]
    assert columns == [
        ("source", "TEXT", 1, 1),
        ("query_key", "TEXT", 1, 2),
        ("payload", "TEXT", 1, 0),
    ]
    # The composite primary key is the only index, and one table has nothing to reference.
    assert indexes == [("sqlite_autoindex_cache_entries_1", 1, "pk")]
    assert indexed_columns == ["source", "query_key"]
    assert foreign_keys == []


@pytest.mark.asyncio
async def test_rows_hold_the_normalised_key_and_the_whole_entry_as_json(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sources.sqlite3"
    await cache_every_source(path, "  What is   Photosynthesis? ")
    await cache_every_source(path, "WHAT IS PHOTOSYNTHESIS")  # same keys: rows are replaced

    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(
            "SELECT source, query_key, payload FROM cache_entries ORDER BY source"
        ).fetchall()

    assert [(source, key) for source, key, _ in rows] == [
        ("arxiv", "what is photosynthesis"),
        ("web", "what is photosynthesis"),
        ("wiki", "what is photosynthesis"),
    ]
    for source, key, payload in rows:
        document = json.loads(payload)
        assert set(document) == {"source", "query_key", "sources", "created_at", "expires_at"}
        assert (document["source"], document["query_key"]) == (source, key)
        assert document["sources"] == [
            {
                "title": f"{source} evidence",
                "url": f"https://example.org/{source}",
                "snippet": f"Simulated {source} result.",
                "origin": ORIGINS[source],
            }
        ]
        created_at = datetime.fromisoformat(document["created_at"])
        expires_at = datetime.fromisoformat(document["expires_at"])
        assert created_at == NOW
        assert created_at.utcoffset() == timedelta(0)
        assert expires_at - created_at == timedelta(seconds=TTL)
        assert CacheEntry.model_validate_json(payload).source == source


@pytest.mark.asyncio
async def test_table_removed_while_the_store_is_open_is_a_read_error(tmp_path: Path) -> None:
    path = tmp_path / "sources.sqlite3"
    store = SqliteCacheStore(path)
    try:
        assert await store.get_entry("wiki", "question") is None  # opens and initialises
        with closing(sqlite3.connect(path)) as other:
            other.execute("DROP TABLE cache_entries")
            other.commit()

        with pytest.raises(CacheStoreError, match="could not read") as excinfo:
            await store.get_entry("wiki", "question")
    finally:
        await store.close()

    assert isinstance(excinfo.value.__cause__, sqlite3.OperationalError)


class CloseFails:
    """Delegate to a real connection whose close() reports an SQLite error."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)

    def close(self) -> None:
        raise sqlite3.OperationalError("simulated close failure")


@pytest.mark.asyncio
async def test_close_failure_is_reported_as_a_store_error(tmp_path: Path) -> None:
    store = SqliteCacheStore(tmp_path / "sources.sqlite3")
    await store.get_entry("wiki", "question")
    real_connection = store._conn
    assert real_connection is not None
    store._conn = CloseFails(real_connection)  # type: ignore[assignment]
    try:
        with pytest.raises(CacheStoreError, match="could not close") as excinfo:
            await store.close()
        assert isinstance(excinfo.value.__cause__, sqlite3.OperationalError)
    finally:
        store._conn = real_connection
        await store.close()


@pytest.mark.asyncio
async def test_file_that_is_not_a_database_is_rejected_and_left_untouched(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sources.sqlite3"
    content = b"not a SQLite database\n" * 8
    path.write_bytes(content)
    entry = CacheEntry(
        source="wiki",
        query_key="question",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=TTL),
    )
    store = SqliteCacheStore(path)
    try:
        with pytest.raises(CacheStoreError, match="could not open"):
            await store.get_entry("wiki", "question")
        with pytest.raises(CacheStoreError, match="could not open"):
            await store.upsert_entry(entry)
    finally:
        await store.close()

    assert path.read_bytes() == content
    assert [child.name for child in tmp_path.iterdir()] == ["sources.sqlite3"]
