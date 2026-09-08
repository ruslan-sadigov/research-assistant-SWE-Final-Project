"""Tests for the cache storage back ends."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai import Source
from researcher.models import CacheEntry
from researcher.storage.cache_store import (
    CacheStoreError,
    InMemoryCacheStore,
    JsonFileCacheStore,
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


class TestJsonFileCacheStore:
    @pytest.mark.asyncio
    async def test_missing_file_is_an_empty_cache(self, tmp_path: Path) -> None:
        store = JsonFileCacheStore(tmp_path / "cache.json")

        assert await store.get_entry("wiki", "anything") is None

    @pytest.mark.asyncio
    async def test_entries_survive_a_new_store_instance(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        entry = make_entry()

        writer = JsonFileCacheStore(path)
        await writer.upsert_entry(entry)
        await writer.close()

        reader = JsonFileCacheStore(path)
        restored = await reader.get_entry("wiki", entry.query_key)

        assert restored == entry
        assert restored is not None
        assert restored.expires_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / "cache.json"
        store = JsonFileCacheStore(path)

        await store.upsert_entry(make_entry())

        assert path.exists()

    @pytest.mark.asyncio
    async def test_rewriting_a_key_leaves_one_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        store = JsonFileCacheStore(path)

        await store.upsert_entry(make_entry(titles=("First",)))
        await store.upsert_entry(make_entry(titles=("Second",)))
        await store.close()

        reopened = JsonFileCacheStore(path)
        stored = await reopened.get_entry("wiki", "what is photosynthesis")
        assert stored is not None
        assert [source.title for source in stored.sources] == ["Second"]

    @pytest.mark.asyncio
    async def test_writes_leave_no_temporary_files_behind(self, tmp_path: Path) -> None:
        store = JsonFileCacheStore(tmp_path / "cache.json")

        await store.upsert_entry(make_entry())

        assert [path.name for path in tmp_path.iterdir()] == ["cache.json"]

    @pytest.mark.asyncio
    async def test_unreadable_file_raises_a_store_error(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        path.write_text("{ this is not json", encoding="utf-8")
        store = JsonFileCacheStore(path)

        with pytest.raises(CacheStoreError):
            await store.get_entry("wiki", "anything")

    @pytest.mark.asyncio
    async def test_unexpected_document_shape_raises_a_store_error(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        path.write_text('{"version": 1, "entries": [{"source": "wiki"}]}', encoding="utf-8")
        store = JsonFileCacheStore(path)

        with pytest.raises(CacheStoreError):
            await store.get_entry("wiki", "anything")
