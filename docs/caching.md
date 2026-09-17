# Caching and persistence

Component owner: Member 4 (Samur Eyyubov). Code:
`src/researcher/services/cache.py`, `src/researcher/storage/cache_store.py`.
Satisfies `SourceCacheProtocol` and `CacheStoreProtocol` from
`src/researcher/interfaces.py`, and uses the shared `CacheEntry` model from
`src/researcher/models.py`. The cache's place in the request flow is shown in
[architecture](architecture.md); measured behaviour and the schema read back
from a real database are in [cache verification](cache-verification.md).

Measured with simulated providers over the five supplied questions and all
three sources: **0/15 hits on the first run, 15/15 on the second, 15/30 (50%)
combined**, with an answer synthesized on every run.

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
3. Any trailing mix of `?`, `!`, `.` and whitespace stripped.
4. `casefold()` -- a more aggressive lowercase that also handles non-English text.

So `"What is Photosynthesis?"`, `"WHAT IS PHOTOSYNTHESIS"` and
`"  what   is photosynthesis "` share one entry. Only *trailing* sentence
punctuation is removed, so symbols that carry meaning inside a query survive:
`"What is C++?"` becomes `"what is c++"`, not `"what is c"`.

A question that normalises to nothing (`"???"`) raises `ValueError` rather than
being stored under an empty key. The CLI rejects such questions before they
reach the cache: a question needs at least one letter or digit. Normalisation
is purely textual: `"What is photosynthesis in plants?"` is a different key
from `"What is photosynthesis?"`.

## Expiry

`SourceCache` takes `ttl_seconds` (from `Settings.cache_ttl_seconds`) and a
clock. On write it stamps `created_at` and `expires_at = created_at + ttl`, both
timezone-aware UTC; on read it asks `CacheEntry.is_expired(now)`.

| `get_sources` returns | Meaning |
|---|---|
| `None` | nothing cached, the entry expired, or it was fetched with other settings -- fetch from the source |
| `[]` | the source was already asked and genuinely returned nothing |
| `[Source, ...]` | cached evidence, still fresh |

`None` and `[]` are not interchangeable. Conflating them would re-fetch an
empty source on every run.

The clock is injectable (`clock=`), so tests move time forward instead of
sleeping.

Expired entries are not deleted. The next successful fetch of the same key
replaces the row with fresh evidence and new timestamps.

## Where the cache lives

`researcher.cli.create_cache_store()` chooses the file:

| Setting | SQLite file |
|---|---|
| `DATABASE_URL` blank (default) | `~/.cache/research-assistant/sources.sqlite3` |
| `DATABASE_URL=sqlite:///.cache/sources.sqlite3` | Relative to the working directory |
| `DATABASE_URL=sqlite:///C:/path/to/sources.sqlite3` | Windows absolute path |
| `DATABASE_URL=sqlite:////path/to/sources.sqlite3` | Linux absolute path |
| Docker runtime with `--env DATABASE_URL=` | `/home/appuser/.cache/research-assistant/sources.sqlite3`; mount a volume there to keep it |
| `--no-cache` | None; see below |

Missing parent directories are created and `~` is expanded. A value that is not
`sqlite:///` followed by a file path (including `:memory:` or a path with `?`
or `#`) stops the CLI with `Invalid cache configuration` and exit code 2,
without printing the value. `.cache/` is already covered by `.gitignore`.

## Storage back ends

Both satisfy `CacheStoreProtocol` (`get_entry`, `upsert_entry`, `close`)
structurally -- no inheritance required, which is what makes test fakes easy.

- **`InMemoryCacheStore`** -- a dictionary keyed by `(source, query_key)`, used
  by the test suite and by runs that do not need persistence.
- **`SqliteCacheStore`** -- the same entries in a local SQLite database, so the
  cache survives restarts with no external database service to install or run.

PostgreSQL is optional under the assignment and was deliberately not used: it
would add a service to run, a driver to configure and a schema to migrate, in
exchange for a cache holding at most three entries per distinct question.

### SQLite schema

One table, primary-keyed on `(source, query_key)` so a write to an existing key
overwrites in place instead of accumulating rows:

```sql
CREATE TABLE IF NOT EXISTS cache_entries (
    source TEXT NOT NULL,
    query_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (source, query_key)
)
```

`source` and `query_key` are real columns because `get_entry` looks up by them;
`payload` holds the whole `CacheEntry` (including `fetch_settings`,
`created_at` / `expires_at`) as JSON via pydantic's own `model_dump_json` /
`model_validate_json`, so the payload is validated against the model on read.
Initialization reads `PRAGMA user_version` and rejects unsupported versions
without overwriting them. Version 0 databases are initialized (or adopted if
their existing cache table matches). Version 2 added `fetch_settings` to the
payload; version 1 files have the same table and are upgraded in place, their
entries read as having no recorded settings and are refetched once. Versions 1
and 2 must already have the expected table.
Table columns and primary-key structure are checked before use.

The table has no index other than the automatic unique one for the primary
key (`sqlite_autoindex_cache_entries_1`), which serves the only lookup the
store runs. There are no foreign keys: a single table has nothing to
reference. SQLite runs in its default rollback-journal mode, and no journal
files remain after a write. Every column is derived data: a copy of what the
external sources returned. Those sources stay authoritative, so deleting the
file loses speed and API quota, not information. A sample payload and the
schema as read from a real database are in
[cache verification](cache-verification.md#sqlite-schema).

A missing database file is an empty cache, not an error -- `sqlite3.connect`
creates it lazily. A file that exists but is not a valid SQLite database, or a
row whose `payload` no longer parses as a `CacheEntry`, raises `CacheStoreError`.
Such a file is never overwritten.

Database operations run in `asyncio.to_thread`. A `threading.Lock` inside the
worker serializes initialization, reads, full write transactions, and closure
on a single reused connection. Cancelling the awaiting task cannot release
that lock while its worker still uses the connection. Transaction context
managers commit successful writes and roll back failed writes or commits.

Cancellation stops waiting; it does not forcibly stop SQLite. A cancelled
write may still commit. `close()` waits for the active database operation and
permanently closes the store; subsequent operations raise `CacheStoreError`.
Calls queued concurrently with close may finish first or be rejected, but
cannot reopen a closed store. A new store instance is needed to reopen it.

## Usage

`run_ask()` builds the store and cache; `SourceOrchestrator` performs the
read, fetch and write for each source inside that source's deadline. In
outline:

```python
from pathlib import Path

from researcher.services.cache import SourceCache
from researcher.storage.cache_store import InMemoryCacheStore, SqliteCacheStore

# Persistent across runs (the CLI's default location)
store = SqliteCacheStore(Path.home() / ".cache" / "research-assistant" / "sources.sqlite3")
cache = SourceCache(store, ttl_seconds=settings.cache_ttl_seconds)
try:
    cached = await cache.get_sources("wiki", question)
    if cached is None:
        sources = await ai_service.fetch_sources("wiki", question, client=client)
        await cache.set_sources("wiki", question, sources)
    else:
        sources = cached
finally:
    await store.close()

# Ephemeral (tests, one-off experiments)
cache = SourceCache(InMemoryCacheStore(), ttl_seconds=600)
```

### `--no-cache`

`--no-cache` is handled above this layer. The CLI builds an
`InMemoryCacheStore` instead of opening SQLite, and the orchestrator skips both
the read and the write rather than asking the cache to disable itself. The run
therefore makes no cache lookups, fetches every selected source, and leaves an
existing SQLite file byte-for-byte unchanged. arXiv request pacing still
applies.

## Failure behaviour

Storage problems raise `CacheStoreError`, deliberately distinct from "nothing
cached". The orchestrator converts it into a warning and keeps fetching, so a
broken cache costs speed rather than the run. With an unreadable file, every
source reports a failed read and a failed write; the answer is still produced,
with one warning line per source, until the file is removed.

## Invalidation

Entries expire by TTL; there is no eviction command. Each entry also records
the fetch settings it was fetched with: `MAX_SOURCES_PER_QUERY` for every
source, plus the normalised `WEB_SEARCH_PROVIDER` for `web`
(`source_fetch_settings` in `researcher.services.ai_service`). An entry whose
recorded settings differ from the current ones is a miss; the next fetch
overwrites it. The key itself stays `(source, normalised question)`, so
switching a setting back and forth refetches each time rather than keeping
one row per configuration. A provider's own behaviour changing over time is
covered only by the TTL.

Choose a cache path outside version control; `.cache/` is already covered by
`.gitignore`.

## Tests

`tests/test_cache.py` and `tests/test_cache_store.py` -- offline tests, no
network, no filesystem dependency beyond `tmp_path`. They cover the
normalisation table (including `C++`, `C#` and `A*`), cached-empty versus miss,
TTL boundaries with an injected clock, misses after a fetch-settings change,
per-source isolation, defensive copying,
persistence across store instances, upsert overwriting a row rather than
duplicating it, and `CacheStoreError` on an unreadable database or a row that
no longer validates as a `CacheEntry`.

Regression tests cover concurrent store instances, cancellation during writes
and initialization, subsequent reads/writes/closure, rollback after a deferred
constraint fails at commit, unsupported schema versions, the version 1 upgrade,
initialization
cleanup, and filesystem errors. Test-created connections are explicitly closed.

- `tests/test_cache_store_contract.py` pins the documented schema, index,
  foreign keys and JSON payload, and covers a table removed while the store is
  open, a failing connection close, and a non-database file that must be left
  untouched.
- `tests/test_cache_integration.py` runs the real `Researcher`,
  `SourceOrchestrator`, `SourceCache` and SQLite store with a simulated AI
  service: cold and warm hits, reformatted and different questions, expiry
  followed by in-place replacement, empty and failed results, a corrupt cache
  file through `run_ask()`, and `--no-cache` leaving the database unchanged.
- `tests/test_cache_verification.py` runs `tests/cache_verification.py`, the
  script behind the measured hit rate.

## Known limitations

- The worker lock serializes access within one store instance. SQLite's file
  locking coordinates separate instances/processes. Distinct-key upserts do
  not overwrite each other's rows; competing same-key updates are last-writer-wins.
- Cancelled operations can continue in a worker. Shutdown may wait for them;
  SQLite's default lock wait is five seconds. The async source deadline does
  not forcibly terminate an in-progress database call.
- One TTL for all three sources, although arXiv results age far more slowly
  than web search results. Per-source TTLs are the obvious refinement.
- No size bound or eviction policy; the database grows until deleted.
- An empty result is kept for the full TTL. Live full-sentence questions found
  no Wikipedia matches, so repeating one reports Wikipedia as empty from the
  cache for 24 hours. A shorter TTL for empty entries would be a refinement.
- A corrupt cache file is not repaired or replaced automatically.

These points are discussed with evidence in
[cache verification](cache-verification.md#findings-for-team-discussion).
