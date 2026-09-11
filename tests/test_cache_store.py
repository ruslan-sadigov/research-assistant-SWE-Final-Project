"""Tests for the cache storage back ends."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from ai import Source
from researcher.models import CacheEntry
from researcher.storage.cache_store import (
    CacheStoreError,
    InMemoryCacheStore,
    SqliteCacheStore,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture(autouse=True)
async def close_sqlite_stores(monkeypatch):
    """Close every test-created store even when an assertion fails."""
    stores = []
    original_init = SqliteCacheStore.__init__

    def tracked_init(self, path):
        original_init(self, path)
        stores.append(self)

    monkeypatch.setattr(SqliteCacheStore, "__init__", tracked_init)
    yield
    for store in stores:
        await store.close()


def make_entry(
    source: str = "wiki",
    query_key: str = "what is photosynthesis",
    titles: tuple[str, ...] = ("Photosynthesis",),
) -> CacheEntry:
    return CacheEntry(
        source=source,
        query_key=query_key,
        sources=[
            Source(
                title=title,
                url=f"https://example.com/{title.lower()}",
                snippet=f"An excerpt about {title}.",
                origin="wikipedia",
            )
            for title in titles
        ],
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=600),
    )


class TestInMemoryCacheStore:
    @pytest.mark.asyncio
    async def test_unknown_key_returns_none(self) -> None:
        store = InMemoryCacheStore()

        assert await store.get_entry("wiki", "missing") is None

    @pytest.mark.asyncio
    async def test_stores_and_returns_an_entry(self) -> None:
        store = InMemoryCacheStore()
        entry = make_entry()

        await store.upsert_entry(entry)

        assert await store.get_entry("wiki", entry.query_key) == entry

    @pytest.mark.asyncio
    async def test_upsert_replaces_rather_than_duplicates(self) -> None:
        store = InMemoryCacheStore()
        await store.upsert_entry(make_entry(titles=("First",)))
        await store.upsert_entry(make_entry(titles=("Second",)))

        stored = await store.get_entry("wiki", "what is photosynthesis")
        assert stored is not None
        assert [source.title for source in stored.sources] == ["Second"]

    @pytest.mark.asyncio
    async def test_entries_are_keyed_by_source_and_query(self) -> None:
        store = InMemoryCacheStore()
        await store.upsert_entry(make_entry(source="wiki"))
        await store.upsert_entry(make_entry(source="arxiv"))

        assert await store.get_entry("wiki", "what is photosynthesis") is not None
        assert await store.get_entry("arxiv", "what is photosynthesis") is not None
        assert await store.get_entry("web", "what is photosynthesis") is None

    @pytest.mark.asyncio
    async def test_close_is_repeatable(self) -> None:
        store = InMemoryCacheStore()

        await store.close()
        await store.close()


class TestSqliteCacheStore:
    @pytest.mark.asyncio
    async def test_missing_database_is_an_empty_cache(self, tmp_path: Path) -> None:
        store = SqliteCacheStore(tmp_path / "cache.db")

        assert await store.get_entry("wiki", "anything") is None

    @pytest.mark.asyncio
    async def test_entries_survive_a_new_store_instance(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.db"
        entry = make_entry()

        writer = SqliteCacheStore(path)
        await writer.upsert_entry(entry)
        await writer.close()

        reader = SqliteCacheStore(path)
        restored = await reader.get_entry("wiki", entry.query_key)

        assert restored == entry
        assert restored is not None
        assert restored.expires_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / "cache.db"
        store = SqliteCacheStore(path)

        await store.upsert_entry(make_entry())

        assert path.exists()

    @pytest.mark.asyncio
    async def test_rewriting_a_key_leaves_one_row(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.db"
        store = SqliteCacheStore(path)

        await store.upsert_entry(make_entry(titles=("First",)))
        await store.upsert_entry(make_entry(titles=("Second",)))
        await store.close()

        reopened = SqliteCacheStore(path)
        stored = await reopened.get_entry("wiki", "what is photosynthesis")
        assert stored is not None
        assert [source.title for source in stored.sources] == ["Second"]
        await reopened.close()

        with closing(sqlite3.connect(path)) as conn:
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM cache_entries WHERE source = ? AND query_key = ?",
                ("wiki", "what is photosynthesis"),
            ).fetchone()
        assert count == 1

    @pytest.mark.asyncio
    async def test_writes_leave_no_journal_files_behind(self, tmp_path: Path) -> None:
        store = SqliteCacheStore(tmp_path / "cache.db")

        await store.upsert_entry(make_entry())
        await store.close()

        assert [path.name for path in tmp_path.iterdir()] == ["cache.db"]

    @pytest.mark.asyncio
    async def test_unreadable_database_raises_a_store_error(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.db"
        path.write_text("this is not a sqlite database", encoding="utf-8")
        store = SqliteCacheStore(path)

        with pytest.raises(CacheStoreError):
            await store.get_entry("wiki", "anything")


@pytest.mark.asyncio
async def test_concurrent_instances_preserve_distinct_keys(tmp_path):
    path = tmp_path / "cache.db"
    stores = [SqliteCacheStore(path), SqliteCacheStore(path)]
    entries = [make_entry(query_key=f"question-{i}") for i in range(12)]
    await asyncio.gather(*(stores[i % 2].upsert_entry(entry) for i, entry in enumerate(entries)))
    reader = SqliteCacheStore(path)
    for entry in entries:
        assert await reader.get_entry(entry.source, entry.query_key) == entry


@pytest.mark.asyncio
@pytest.mark.parametrize("next_operation", ["read", "write", "close"])
async def test_cancelled_write_keeps_connection_serialized(tmp_path, monkeypatch, next_operation):
    store = SqliteCacheStore(tmp_path / "cache.db")
    started = threading.Event()
    release = threading.Event()
    original_upsert = store._upsert

    def blocked_upsert(conn, entry):
        started.set()
        if not release.wait(5):
            raise RuntimeError("Test failed to release worker")
        original_upsert(conn, entry)

    monkeypatch.setattr(store, "_upsert", blocked_upsert)
    writer = asyncio.create_task(store.upsert_entry(make_entry(titles=("First",))))
    following = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        writer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await writer
        # Cancellation must not unlock the still-running database operation.
        acquired = store._lock.acquire(blocking=False)
        if acquired:
            store._lock.release()
        assert not acquired

        if next_operation == "read":
            following = asyncio.create_task(store.get_entry("wiki", "what is photosynthesis"))
        elif next_operation == "write":
            following = asyncio.create_task(store.upsert_entry(make_entry(titles=("Second",))))
        else:
            following = asyncio.create_task(store.close())
        release.set()
        await asyncio.wait_for(following, 2)
    finally:
        release.set()
        await asyncio.gather(writer, *([following] if following else []), return_exceptions=True)
        await store.close()

    reader = SqliteCacheStore(tmp_path / "cache.db")
    restored = await reader.get_entry("wiki", "what is photosynthesis")
    expected = "Second" if next_operation == "write" else "First"
    assert restored.sources[0].title == expected


@pytest.mark.asyncio
async def test_cancelled_initialization_is_cleaned_up_by_close(tmp_path, monkeypatch):
    store = SqliteCacheStore(tmp_path / "cache.db")
    started = threading.Event()
    release = threading.Event()
    connections = []
    connect = store._connect

    def blocked_connect():
        conn = connect()
        connections.append(conn)
        started.set()
        if not release.wait(5):
            raise RuntimeError("Test failed to release initialization")
        return conn

    monkeypatch.setattr(store, "_connect", blocked_connect)
    task = asyncio.create_task(store.get_entry("wiki", "missing"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")


@pytest.mark.asyncio
async def test_failed_commit_rolls_back_and_connection_remains_usable(tmp_path):
    class ConstraintStore(SqliteCacheStore):
        def _connect(self):
            conn = super()._connect()
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
                conn.execute(
                    "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) "
                    "DEFERRABLE INITIALLY DEFERRED)"
                )
                conn.execute(
                    "CREATE TRIGGER fail_commit AFTER INSERT ON cache_entries "
                    "WHEN NEW.query_key = 'bad' BEGIN INSERT INTO child VALUES (999); END"
                )
            return conn

    store = ConstraintStore(tmp_path / "cache.db")
    with pytest.raises(CacheStoreError, match="could not write"):
        await store.upsert_entry(make_entry(query_key="bad"))
    assert not store._conn.in_transaction
    assert await store.get_entry("wiki", "bad") is None
    await store.upsert_entry(make_entry(query_key="good"))
    await store.close()
    reader = SqliteCacheStore(tmp_path / "cache.db")
    assert await reader.get_entry("wiki", "bad") is None
    assert await reader.get_entry("wiki", "good") is not None


@pytest.mark.asyncio
async def test_unsupported_version_is_preserved_and_connection_closed(tmp_path, monkeypatch):
    path = tmp_path / "cache.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
    original_connect = sqlite3.connect
    opened = []

    def capture_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", capture_connect)
    store = SqliteCacheStore(path)
    with pytest.raises(CacheStoreError, match="unsupported cache schema version"):
        await store.get_entry("wiki", "anything")
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")
    with closing(original_connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (99,)
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


@pytest.mark.asyncio
async def test_directory_creation_failure_is_wrapped(tmp_path):
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied", encoding="utf-8")
    store = SqliteCacheStore(parent / "cache.db")
    with pytest.raises(CacheStoreError, match="could not open"):
        await store.get_entry("wiki", "anything")


@pytest.mark.asyncio
async def test_incompatible_table_is_rejected_without_stamping_version(tmp_path):
    path = tmp_path / "cache.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE cache_entries (unrelated TEXT)")
        conn.commit()
    store = SqliteCacheStore(path)
    with pytest.raises(CacheStoreError, match="incompatible cache table schema"):
        await store.get_entry("wiki", "anything")
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (0,)


@pytest.mark.asyncio
async def test_close_is_repeatable_and_operations_cannot_reopen_store(tmp_path):
    store = SqliteCacheStore(tmp_path / "cache.db")
    await store.get_entry("wiki", "anything")
    await store.close()
    await store.close()
    with pytest.raises(CacheStoreError, match="closed"):
        await store.get_entry("wiki", "anything")
    with pytest.raises(CacheStoreError, match="closed"):
        await store.upsert_entry(make_entry())


@pytest.mark.asyncio
async def test_unexpected_row_shape_raises_a_store_error(tmp_path: Path) -> None:
    path = tmp_path / "cache.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "CREATE TABLE cache_entries "
            "(source TEXT NOT NULL, query_key TEXT NOT NULL, payload TEXT NOT NULL, "
            "PRIMARY KEY (source, query_key))"
        )
        conn.execute(
            "INSERT INTO cache_entries VALUES (?, ?, ?)",
            ("wiki", "anything", '{"source": "wiki"}'),
        )
        conn.commit()
    store = SqliteCacheStore(path)

    with pytest.raises(CacheStoreError):
        await store.get_entry("wiki", "anything")
