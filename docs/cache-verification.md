# Cache verification

This page records how the SQLite source cache behaves, measured with a
reproducible offline script. The design is described in [caching](caching.md)
and the component layout in [architecture](architecture.md).

## Method

- **Workload:** the five supplied questions (`data/research_questions.json`,
  q1–q5) with `--sources wiki,arxiv,web`, one CLI invocation per question.
- **Providers:** simulated. [`tests/cache_verification.py`](../tests/cache_verification.py)
  replaces only `ai.fetch_wikipedia`, `ai.fetch_arxiv`, `ai.fetch_web` and
  `ai.synthesize`, the boundary that `tests/docker_smoke.py` also uses. The
  real CLI, settings loader, `Researcher`, `SourceOrchestrator`, `AIService`,
  `SourceCache` and `SqliteCacheStore` run unchanged, and HTTP requests are
  rejected.
- **Isolation:** every run creates new SQLite files in a temporary directory
  and works from that directory, so no personal cache or `.env` file is read
  or changed. All application settings are pinned to the `.env.example`
  defaults, including `CACHE_TTL_SECONDS=86400`.
- **Hit rate = hits ÷ eligible lookups.** A cache-enabled invocation makes one
  lookup per selected source. Each lookup ends in one
  `source completed: … cache_hit=True|False` INFO record, which the script
  counts. A `--no-cache` invocation makes no lookups, so it is reported
  separately rather than as a 0% run.
- **Cross-check:** every lookup that is not a hit must cause exactly one
  provider fetch, counted inside the simulated fetchers.

## Reproduce

```powershell
uv run python tests/cache_verification.py
uv run python tests/cache_verification.py --json
uv run python tests/cache_verification.py --show-logs
uv run python -m pytest tests/test_cache_verification.py tests/test_cache_integration.py tests/test_cache_store_contract.py -v
```

`--show-logs` echoes the application logs to stderr. The script exits with 1
if any check fails, and `tests/test_cache_verification.py` runs it as part of
the normal test suite, so CI keeps these numbers reproducible.

## Recorded runs

| Date (UTC) | Environment | Code | Result |
|---|---|---|---|
| 2026-09-16 | Linux x86_64 container; Python 3.12.3; SQLite 3.45.1; research-assistant 0.1.0; pydantic 2.13.5; httpx 0.28.1 | `ccc2eb7` with this page's test files | 9 of 9 checks passed |

The same working tree passed the full suite (283 tests, 98.44% application
coverage, `cache_store.py` and `cache.py` at 100%), the 16 supplied smoke
tests, and the `ruff`, `black`, `isort` and `mypy` checks. Add a row for each
further environment where the script is run; the counts are deterministic, so
only the environment column should change.

## Results

### Hit rate

| Run | Hits / lookups | Hit rate |
|---|---:|---:|
| First run (empty cache) | 0 / 15 | 0% |
| Second run (same questions) | 15 / 15 | 100% |
| **Both runs combined** | **15 / 30** | **50%** |

The second run made no provider fetches. Both runs synthesized all five
answers, because answers are never cached.

### Additional checks

| Run | Invocations | Lookups | Hits | Fetches | Answers | Warning lines |
|---|---:|---:|---:|---:|---:|---:|
| Same words, different case, spacing and trailing `?!` | 5 | 15 | 15 | 0 | 5 | 0 |
| `--no-cache` | 5 | none | none | 15 | 5 | 0 |
| Cache file is not a database | 1 | 3 | 0 | 3 | 1 | 3 |
| Wikipedia returns nothing, first run | 1 | 3 | 0 | 3 | 1 | 0 |
| Wikipedia returns nothing, second run | 1 | 3 | 3 | 0 | 1 | 0 |

- The database held 15 rows (five questions × three sources) after the first
  run and still 15 after all passes: repeated writes replace rows.
- The SQLite file was byte-for-byte unchanged by the `--no-cache` pass.
- With a corrupt cache file, every source logged a failed read and a failed
  write, then used fresh evidence; each invocation still printed an answer,
  with one warning line per source. The corrupt file was left unchanged.
- The empty Wikipedia result was stored: the second run reported
  `status=empty cache_hit=True` and did not ask Wikipedia again.
- Every lookup that was not a hit caused exactly one fetch.

### Log excerpts

First run, first question:

```text
INFO researcher.concurrency.orchestrator: source completed: source=arxiv status=ok cache_hit=False results=1 elapsed_seconds=0.005
INFO researcher.concurrency.orchestrator: source completed: source=wiki status=ok cache_hit=False results=1 elapsed_seconds=0.007
INFO researcher.concurrency.orchestrator: source completed: source=web status=ok cache_hit=False results=1 elapsed_seconds=0.005
```

Second run, first question:

```text
INFO researcher.concurrency.orchestrator: source completed: source=wiki status=ok cache_hit=True results=1 elapsed_seconds=0.001
INFO researcher.concurrency.orchestrator: source completed: source=arxiv status=ok cache_hit=True results=1 elapsed_seconds=0.001
INFO researcher.concurrency.orchestrator: source completed: source=web status=ok cache_hit=True results=1 elapsed_seconds=0.000
INFO researcher.concurrency.orchestrator: collection completed: requested_sources=3 unique_results=3 elapsed_seconds=0.023
```

Corrupt cache file:

```text
WARNING researcher.concurrency.orchestrator: cache read failed: source=wiki; attempting fresh fetch
WARNING researcher.concurrency.orchestrator: cache write failed: source=wiki; fetched evidence retained
INFO researcher.concurrency.orchestrator: source completed: source=wiki status=ok cache_hit=False results=1 elapsed_seconds=0.002
```

Empty Wikipedia result, first and second run:

```text
INFO researcher.concurrency.orchestrator: source completed: source=wiki status=empty cache_hit=False results=0 elapsed_seconds=0.004
INFO researcher.concurrency.orchestrator: source completed: source=wiki status=empty cache_hit=True results=0 elapsed_seconds=0.001
```

Timestamps are omitted. Elapsed times come from simulated providers and are
not performance measurements.

### CLI output (first run, q1)

```text
Q: What is photosynthesis and what are its main stages?
A: Simulated answer [1] [2] [3]

References:
  [1] (wikipedia) Simulated wiki evidence for q1
    https://example.org/simulated/q1/wiki
  [2] (arxiv) Simulated arxiv evidence for q1
    https://example.org/simulated/q1/arxiv
  [3] (web) Simulated web evidence for q1
    https://example.org/simulated/q1/web
```

## SQLite schema

Read back from the database the script created:

```sql
-- PRAGMA user_version = 2; journal_mode = delete
CREATE TABLE cache_entries (
    source TEXT NOT NULL,
    query_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (source, query_key)
)
-- sqlite_autoindex_cache_entries_1: unique, created for the primary key on (source, query_key)
```

| Column | Type | Constraint | Content |
|---|---|---|---|
| `source` | `TEXT` | not null, primary key part 1 | `wiki`, `arxiv` or `web` |
| `query_key` | `TEXT` | not null, primary key part 2 | Question after `normalize_query()` |
| `payload` | `TEXT` | not null | The whole `CacheEntry` as JSON |

- **Indexes:** only the automatic unique index for the composite primary key.
  It serves the single lookup the store runs
  (`WHERE source = ? AND query_key = ?`) and turns a repeated write into an
  in-place update.
- **Foreign keys:** none. There is one table, so nothing can be referenced.
- **Serialization:** Pydantic's `model_dump_json()` writes the payload and
  `model_validate_json()` validates it on every read. Times are UTC ISO 8601
  strings.
- **Derived versus authoritative:** every column is derived. Rows are copies
  of what Wikipedia, arXiv and the web-search provider returned at fetch time;
  those services stay authoritative. Deleting the file loses speed and API
  quota, not information. `query_key` is derived from the question, and
  `fetch_settings`, `created_at` and `expires_at` are cache metadata. Answers are not stored.

Sample payload (`wiki`, q1):

```json
{
  "source": "wiki",
  "query_key": "what is photosynthesis and what are its main stages",
  "sources": [
    {
      "title": "Simulated wiki evidence for q1",
      "url": "https://example.org/simulated/q1/wiki",
      "snippet": "Offline wiki result for q1.",
      "origin": "wikipedia"
    }
  ],
  "fetch_settings": {
    "max_results": 3
  },
  "created_at": "2026-09-16T20:14:16.202181Z",
  "expires_at": "2026-09-17T20:14:16.202181Z"
}
```

`tests/test_cache_store_contract.py` fails if this layout changes.

## Findings for team discussion

These are observations only; no application code was changed.

1. **Empty results are cached for the full TTL.** Live runs of the five full
   questions found no Wikipedia matches. Repeating such a question reports
   Wikipedia as empty from the cache for 24 hours without asking again. Short
   topic queries use their own keys. Possible remedy: a shorter TTL for empty
   entries.
2. **The key ignored configuration.** Entries fetched with a different
   `WEB_SEARCH_PROVIDER` or `MAX_SOURCES_PER_QUERY` were served until they
   expired. Resolved after this run: entries now record their fetch settings,
   and a mismatch is a miss (schema version 2; see [caching](caching.md#invalidation)).
3. **Expired rows are never deleted.** They are replaced only when the same
   question is fetched again, so the file grows with every distinct question.
   Expiry exists only inside the JSON payload, which rules out a simple
   indexed purge. Possible remedy: an indexed `expires_at` column or a purge
   command.
4. **A corrupt file is never repaired.** Every invocation warns for every
   source until the file is removed or `DATABASE_URL` changes; answers are
   still produced.
5. **A failed connection close leaves the store open.** If SQLite's `close()`
   fails, `close()` raises `CacheStoreError` without marking the store closed.
   The CLI prints `Warning: cache cleanup failed.` and exits normally, so it
   needs no change.

## Limits of this evidence

- Simulated providers show the cache logic, persistence and failure handling,
  not provider behaviour or live latency; no timing claim is made here.
- Live observations are recorded separately: a repeated arXiv query showed
  `cache_hit=True` ([live integration](live-integration.md)), and the CI Docker
  job reuses a cache volume across two containers ([Docker](docker.md)).
- Concurrent writers are covered by `tests/test_cache_store.py`, not by this
  script.

## Related tests

| Test file | What it adds |
|---|---|
| `tests/test_cache_verification.py` | Runs the script and checks the numbers on this page |
| `tests/test_cache_integration.py` | Hits, reformatted and different questions, expiry, empty and failed results, a corrupt file, and `--no-cache`, through the real pipeline |
| `tests/test_cache_store_contract.py` | Schema, index, foreign keys and payload format; a table removed while open; close failures; a non-database file left untouched |
| `tests/test_cache.py`, `tests/test_cache_store.py` | Normalisation, TTL boundaries, persistence, cancellation and SQLite error handling |
| `tests/test_cli.py`, `tests/test_sample_questions.py` | `DATABASE_URL` handling and cache reuse through the CLI |
