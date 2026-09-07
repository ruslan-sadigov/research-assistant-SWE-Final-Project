# Research Assistant — Four-Person Team Plan

## Goal and current baseline

Build the application layer described in TOPIC.md around the supplied AI module. The local and Docker offline demos and 16 supplied smoke tests already pass. We use Python 3.12, uv, Black, isort, and Ruff.

Do not edit ai/ or delete or weaken tests/test_ai_smoke.py. All research calls must go through the public ai.fetch_wikipedia, ai.fetch_arxiv, ai.fetch_web, and ai.synthesize functions.

The names and interfaces below are the proposed shared contract. Agree on them before implementation; communicate any later changes before merging.

## Ownership

| Person | Responsibility | Owned paths |
|---|---|---|
| Member 1 | Orchestration, per-source deadlines, graceful degradation, timing, benchmarks; repository, packaging, and development tooling | `src/researcher/concurrency/orchestrator.py`, `tests/test_orchestrator.py`, `benchmarks/benchmark_sources.py`; shared project files listed below |
| Member 2 | CLI, typed configuration, shared models, validation, output rendering, application integration | `src/researcher/{__main__.py,cli.py,config.py,models.py}`, `src/researcher/core/researcher.py`, `tests/test_cli.py`, `tests/test_researcher.py` |
| Member 3 | AI wrappers, retry policy, HTTP retries, async synthesis adapter | `src/researcher/services/ai_service.py`, `tests/test_ai_service.py` |
| Member 4 | TTL caching, normalization, persistence and database setup | `src/researcher/services/cache.py`, `src/researcher/storage/cache_store.py`, `tests/test_cache.py`, `tests/test_cache_store.py`, `compose.yaml`, `migrations/` if needed |

Member 1 owns repository administration, branch protection and merge coordination, package management, and shared project tooling: pyproject.toml, uv.lock, .python-version, pytest.ini, README.md, Dockerfile, .dockerignore, .gitignore, and any CI configuration. Request dependency additions from Member 1 instead of independently changing the lockfile. Member 1 creates package __init__.py files in the initial scaffold. Member 2 owns config.py, models.py, and .env.example; coordinate interface and settings changes with Member 2. Member 4 owns compose.yaml and coordinates Docker-related changes with Member 1. Repository ownership does not replace teammate code review.

Each person writes their component tests and supplies documentation notes with their PR. Member 1 owns benchmark results and incorporates setup/command updates into README.md; Member 4 supplies persistence setup instructions. Member 2 assembles the project report from all four members' contributions and coordinates application integration. There is no separate team-lead role in this allocation.

## Package layout

```text
src/researcher/
    __init__.py
    __main__.py
    cli.py
    config.py
    models.py
    core/
        __init__.py
        researcher.py
    concurrency/
        __init__.py
        orchestrator.py
    services/
        __init__.py
        ai_service.py
        cache.py
    storage/
        __init__.py
        cache_store.py
```

Member 1 configures project packaging so uv run python -m researcher works with this src layout and the supplied ai package remains importable. Update Docker dependency/project installation steps when packaging is introduced.

## Shared models

Define these once in models.py using Pydantic models and reuse ai.Source and ai.AnswerWithCitations.

- SourceName = Literal["wiki", "arxiv", "web"]
- SourceStatus = Literal["ok", "empty", "failed", "timeout"]
- SourceOutcome:
  - source: SourceName
  - status: SourceStatus
  - sources: list[Source]
  - elapsed_seconds: float
  - cache_hit: bool
  - warning: str | None
- CollectionResult:
  - outcomes: list[SourceOutcome]
  - sources: list[Source]
  - elapsed_seconds: float
- ResearchResult:
  - question: str
  - answer: AnswerWithCitations | None
  - collection: CollectionResult
  - warnings: list[str]

The CLI name "wiki" maps to ai.fetch_wikipedia; returned Source.origin remains "wikipedia". Never change the supplied Source model.

An empty source response is different from a failed source. No evidence means answer=None with a clear warning; do not call synthesis with an empty list. Preserve source ordering so numeric citations remain stable.

## Function contracts

The signatures below describe interfaces, not complete implementations. Fetching, synthesis, orchestration, and cache operations are async. Constructors, validation, rendering, and the factory returning an async context manager are synchronous. Dependencies are injected, allowing each member to test using fakes.

### Member 1: SourceOrchestrator and project infrastructure

Constructor: `SourceOrchestrator(settings: Settings, ai_service: AIService, cache: SourceCache)`

```python
async def collect_sources(
    self,
    question: str,
    selected_sources: list[SourceName],
    *,
    use_cache: bool = True,
) -> CollectionResult
```

Responsibilities:

- Open one shared source client using AIService.open_source_client().
- Run selected sources using asyncio.gather with return_exceptions=True.
- Apply asyncio.timeout per source over cache lookup, fetch/retries, and cache write. The source deadline bounds retries too.
- If a cache write fails or times out after retrieval succeeds, retain the retrieved evidence and attach a warning.
- With use_cache=False, bypass both cache reads and writes.
- Return one outcome per selected source, preserving requested order.
- Deduplicate combined sources by URL, retaining the first occurrence.
- Preserve successful sources when another source fails or times out.
- Record source durations and total wall-clock time using a monotonic timer.
- Do not implement another retry loop; AIService owns retries.

### Member 2: Researcher and CLI

Constructor: `Researcher(orchestrator: SourceOrchestrator, ai_service: AIService)`

```python
async def research(
    self,
    question: str,
    selected_sources: list[SourceName],
    *,
    use_cache: bool = True,
) -> ResearchResult
```

Synchronous helpers:

```python
def validate_question(question: str, max_length: int) -> str
```

```python
def render_result(result: ResearchResult) -> str
```

Responsibilities:

- CLI: python -m researcher ask "question" --sources wiki,arxiv --no-cache
- Default to all three sources; reject unknown names and deduplicate repeated selections.
- Validate input and construct dependencies once at the application boundary.
- Call collection, then synthesize only if evidence exists.
- Print source-failure notices beside the references without inventing citation sources.
- Preserve synthesis citation numbering and sanitize invalid citation markers/output in the application layer. The supplied synthesizer filters invalid reference indices but does not remove those markers from answer text.
- Handle synthesis failure as a clear user-facing result and nonzero exit status.
- Define exit codes and document them: 0 for an answer (including partial-source success), 1 for no answer/runtime failure, 2 for invalid CLI input.
- Configure logging centrally and close resources reliably.

### Member 3: AIService

Constructor: `AIService(settings: Settings)`

```python
async def fetch_sources(
    self, source: SourceName, query: str, *, client: httpx.AsyncClient
) -> list[Source]
```

```python
async def synthesize_answer(
    self, question: str, sources: list[Source]
) -> AnswerWithCitations
```

Provide an HTTP client factory/context manager:

```python
def open_source_client(self) -> AsyncContextManager[httpx.AsyncClient]
```

Responsibilities:

- Route to the supplied public ai functions.
- Apply bounded exponential backoff to retryable failures; do not retry invalid input or missing credentials.
- Own HTTP retry behavior through the injected client/transport. A wrapper around ai.fetch_* alone is insufficient: Wikipedia catches some individual HTTP failures internally.
- Respect cancellation and never swallow CancelledError.
- Offload synchronous ai.synthesize through asyncio.to_thread.
- Do not claim that cancelling a thread stops an in-progress SDK call. Document synthesis timeout limits and use supported provider settings where available without editing ai/.
- Log attempts without API keys or credential-bearing request bodies.

DuckDuckGo uses its own library and ignores the injected client. Document this limitation and retry its public ai.fetch_web call; do not bypass the assignment API contract to reach into that library.

### Member 4: SourceCache

Constructor: `SourceCache(store: CacheStore, ttl_seconds: int)`

```python
async def get_sources(
    self, source: SourceName, query: str
) -> list[Source] | None
```

```python
async def set_sources(
    self, source: SourceName, query: str, sources: list[Source]
) -> None
```

CacheStore persistence contract:

```python
async def get_entry(self, source: SourceName, query_key: str) -> CacheEntry | None
```
```python
async def upsert_entry(self, entry: CacheEntry) -> None
```
```python
async def close(self) -> None
```

CacheEntry contains source, query_key, sources, created_at, and expires_at. Agree on this shared model with Member 2 before implementing it.

Responsibilities:

- Own query normalization and UTC expiration checks.
- Normalize case, surrounding/repeated whitespace, and trailing question punctuation. Preserve meaningful internal symbols such as C++ and C#.
- Return None for missing/expired entries; [] is a valid cached empty result.
- Cache successful fetch results only, including empty responses; never cache failures.
- Use a unique (source, query_key) key and atomic upserts.
- Keep provider/max-result settings fixed for the initial cache contract; invalidate or namespace the cache if those settings change.
- Expose storage errors predictably; the orchestrator converts cache errors into warnings and continues fetching.

Persistence decision: PostgreSQL is optional under the assignment. Confirm it at kickoff. If selected, use a cache table with JSONB results and a Docker volume. Research history is an optional later feature, not part of the initial workload. Provide a fake/in-memory store for offline tests; ordinary pytest runs must not require a live database.

## Settings to agree at kickoff

Member 2 owns Settings; members request additions before depending on them. Member 1 owns dependency and packaging changes needed to support those settings.

Initial fields:

- log_level
- max_question_length
- per_source_timeout_seconds
- max_sources_per_query
- retry_max_attempts (includes the first attempt)
- retry_initial_delay_seconds
- retry_max_delay_seconds
- cache_ttl_seconds
- database_url (only if PostgreSQL is selected)

Validate positive bounds. Keep provider environment variables compatible with .env.example. Explicitly implement/document environment loading; the starter does not load .env automatically.

## Parallel workflow and merge order

1. Member 1 opens the package/tooling foundation PR, including the src layout and package initialization files. Member 2 follows with shared models, settings, agreed interfaces, and the importable entry point. Coordinate these small PRs before parallel implementation. Do not create placeholder tests that pretend unimplemented functionality works.
2. Merge the foundation. Everyone starts their own branch from updated main.
3. Members 1, 3, and 4 work against fakes of dependencies rather than waiting for each other. Member 2 builds CLI and researcher tests against a fake orchestrator/AI service.
4. Merge working AI service and cache increments, then orchestration and full CLI integration. Small independently tested PRs are preferred over one large final PR per person.
5. Member 2 coordinates application integration; Member 1 coordinates repository merges and Docker/tooling verification. Everyone runs the combined offline checks and fixes issues in their own component. Member 1 supplies benchmark evidence, and Member 2 assembles the report.

## Git conventions

Branch names: `<type>/<short-description>`, lowercase with hyphens.

Examples:

- feat/concurrent-sources
- feat/cli-workflow
- feat/ai-service-retries
- feat/persistent-cache

Commit messages: `<type>: <short action description>`

Types: feat, fix, docs, test, refactor, style, chore.

Examples:

- feat: add per-source timeouts
- fix: preserve results when cache writes fail
- test: cover expired cache entries

Work on one task per branch. Open PRs into main and request a teammate review. Avoid editing or reformatting another member's files in unrelated PRs. Announce shared-interface changes before implementing them. Never force-push main.

## Definition of done

Each component PR includes:

- Implementation and meaningful offline tests for success and failure paths.
- No edits to protected starter files.
- Passing isort, Black, and Ruff checks.
- Passing existing and new tests.
- A short explanation of changes, validation, and known limitations.

Run from the project root:

    uv run isort --check-only .
    uv run black --check .
    uv run ruff check .
    uv run python -m pytest -v
    uv run python demo_ai.py --offline --limit 5

The final integrated project must meet the assignment's at-least-60% coverage requirement. Passing the starter smoke tests alone does not demonstrate coverage of the new application.

No live API keys or network calls in the ordinary test suite. Test retries with mocked sleep, cache expiration with an injectable clock, and HTTP paths using mocked async HTTP. Keep any optional PostgreSQL integration tests separate and document how to run them.

