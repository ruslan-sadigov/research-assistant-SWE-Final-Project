"""Tests for the cache storage back ends."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai import Source
from researcher.models import CacheEntry
from researcher.storage.cache_store import (
    CacheStoreError,
    InMemoryCacheStore,
    SqliteCacheStore,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


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

        with sqlite3.connect(path) as conn:
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
    async def test_unexpected_row_shape_raises_a_store_error(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.db"
        with sqlite3.connect(path) as conn:
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
