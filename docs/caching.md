# Caching and persistence

Component owner: Member 4. Code: `src/researcher/services/cache.py`,
`src/researcher/storage/cache_store.py`. Satisfies `SourceCacheProtocol` and
`CacheStoreProtocol` from `src/researcher/interfaces.py`, and uses the shared
`CacheEntry` model from `src/researcher/models.py`.

## What is cached

One entry per `(source, normalised question)` pair, holding the list of
`ai.Source` objects that source returned. The three sources are cached
independently, so a repeated question re-uses whatever is still fresh and
re-fetches only the rest.

Only successful fetches are cached. An empty result is a success -- the source
was reached and had nothing to offer -- and is cached as such, so an empty
source is not re-queried on every run. Failures and timeouts are never written;
the orchestrator only records outcomes it received cleanly.

Synthesised answers are **not** cached. The cache sits below synthesis, so a
repeated question still produces a fresh answer from cached evidence.

## Query normalisation

`normalize_query()` decides which questions count as the same question:

1. Unicode NFKC normalisation.
2. Whitespace runs collapsed to single spaces; leading/trailing space removed.
3. Trailing `?`, `!` and `.` stripped.
4. `casefold()` -- a more aggressive lowercase that also handles non-English text.

So `"What is Photosynthesis?"`, `"WHAT IS PHOTOSYNTHESIS"` and
`"  what   is photosynthesis "` share one entry. Only *trailing* sentence
punctuation is removed, so symbols that carry meaning inside a query survive:
`"What is C++?"` becomes `"what is c++"`, not `"what is c"`.

A question that normalises to nothing (`"???"`) raises `ValueError` rather than
being stored under an empty key.

## Expiry

`SourceCache` takes `ttl_seconds` (from `Settings.cache_ttl_seconds`) and a
clock. On write it stamps `created_at` and `expires_at = created_at + ttl`, both
timezone-aware UTC; on read it asks `CacheEntry.is_expired(now)`.

| `get_sources` returns | Meaning |
|---|---|
| `None` | nothing cached, or the entry expired -- fetch from the source |
| `[]` | the source was already asked and genuinely returned nothing |
| `[Source, ...]` | cached evidence, still fresh |

`None` and `[]` are not interchangeable. Conflating them would re-fetch an
empty source on every run.

The clock is injectable (`clock=`), so tests move time forward instead of
sleeping.

## Storage back ends

Both satisfy `CacheStoreProtocol` (`get_entry`, `upsert_entry`, `close`)
structurally -- no inheritance required, which is what makes test fakes easy.

- **`InMemoryCacheStore`** -- a dictionary keyed by `(source, query_key)`, used
  by the test suite and by runs that do not need persistence.
- **`JsonFileCacheStore`** -- the same map written to one JSON document, so the
  cache survives restarts with no database to install or run.

PostgreSQL is optional under the assignment and was deliberately not used: it
would add a service to run, a driver to configure and a schema to migrate, in
exchange for a cache holding at most three entries per distinct question.

### JSON document format

```json
{
  "version": 1,
  "entries": [
    {
      "source": "wiki",
      "query_key": "what is photosynthesis",
      "sources": [
        {
          "title": "Photosynthesis",
          "url": "https://en.wikipedia.org/wiki/Photosynthesis",
          "snippet": "...",
          "origin": "wikipedia"
        }
      ],
      "created_at": "2026-01-01T12:00:00Z",
      "expires_at": "2026-01-02T12:00:00Z"
    }
  ]
}
```

`version` exists so a future format change is detected rather than misparsed.
A missing file is an empty cache, not an error. An unreadable file raises
`CacheStoreError`.

Writes are atomic: the document is written to a temporary file in the same
directory and moved into place with `os.replace`, which is atomic on both POSIX
and Windows, so an interrupted write cannot leave a corrupt cache. File I/O
runs through `asyncio.to_thread` so it does not block the event loop.

## Usage

```python
from researcher.services.cache import SourceCache
from researcher.storage.cache_store import InMemoryCacheStore, JsonFileCacheStore

# Ephemeral (tests, one-off runs)
cache = SourceCache(InMemoryCacheStore(), ttl_seconds=settings.cache_ttl_seconds)

# Persistent across runs
store = JsonFileCacheStore(".cache/sources.json")
cache = SourceCache(store, ttl_seconds=settings.cache_ttl_seconds)

cached = await cache.get_sources("wiki", question)
if cached is None:
    sources = await ai_service.fetch_sources("wiki", question, client=client)
    await cache.set_sources("wiki", question, sources)
else:
    sources = cached

await store.close()
```

`--no-cache` is handled above this layer: the orchestrator skips both the read
and the write rather than asking the cache to disable itself.

## Failure behaviour

Storage problems raise `CacheStoreError`, deliberately distinct from "nothing
cached". The orchestrator converts it into a warning and keeps fetching, so a
broken cache costs speed rather than the run.

## Invalidation

Entries expire by TTL only; there is no eviction command. If the provider or
the per-source result limit changes, cached entries no longer describe what the
application would fetch today -- delete the cache file, or point
`JsonFileCacheStore` at a different path. The `version` field and the composite
key give a natural place to add per-setting namespacing later.

Choose a cache path outside version control; `.cache/` is already covered by
`.gitignore`.

## Tests

`tests/test_cache.py` and `tests/test_cache_store.py` -- 38 offline tests, no
network, no filesystem dependency beyond `tmp_path`. They cover the
normalisation table (including `C++`, `C#` and `A*`), cached-empty versus miss,
TTL boundaries with an injected clock, per-source isolation, defensive copying,
JSON persistence across store instances, atomic writes, and `CacheStoreError`
on unreadable files.

## Known limitations

- Every write rewrites the whole JSON document. Fine for a small, short-lived
  cache; SQLite would be the next step for anything larger.
- The `asyncio.Lock` serialises tasks within one process. Two processes sharing
  a cache file cannot corrupt it -- writes are atomic -- but one can lose the
  other's entry.
- One TTL for all three sources, although arXiv results age far more slowly
  than web search results. Per-source TTLs are the obvious refinement.
- No size bound or eviction policy; the file grows until deleted.
