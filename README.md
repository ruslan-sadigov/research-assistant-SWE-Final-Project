# Async Research Assistant

[![CI](https://github.com/ruslan-sadigov/research-assistant-SWE-Final-Project/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ruslan-sadigov/research-assistant-SWE-Final-Project/actions/workflows/ci.yml)

A command-line research assistant that collects evidence from Wikipedia, arXiv,
and web search concurrently, then generates an answer with numbered references.
It includes per-source deadlines, retries, persistent SQLite caching, and partial
failure handling. Successful sources remain usable when another source fails.

## Setup

Install uv and use Python 3.12. Run the commands below from the project root;
examples use PowerShell. Docker Desktop with Linux containers is needed only for
Docker commands.

```powershell
uv sync --locked
```

This creates `.venv` and installs the project and development dependencies from
`uv.lock`. You do not need to activate the environment when using `uv run`.

If you do not already have a local `.env`, create it:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and fill in the keys for the tested Gemini/Tavily setup:

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash-lite
GOOGLE_API_KEY=your-google-api-key
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=your-tavily-api-key
```

Obtain credentials from [Google AI Studio](https://aistudio.google.com/) and
[Tavily](https://app.tavily.com/). The model above was verified on 2026-09-16;
choose a model available to your account and check its current quota. Wikipedia
and arXiv need no API keys. Gemini synthesis needs its key even when web search
is not selected. Other LLM provider adapters may need additional SDK dependencies.

Keep `.env` out of Git. It is also excluded from Docker builds. To use the same
file with Docker, keep values unquoted and place comments on separate lines.
Existing shell environment variables take precedence over `.env` for local runs.

### Application settings

Defaults are provided in [.env.example](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `MAX_QUESTION_LENGTH` | `2000` | Maximum question length |
| `PER_SOURCE_TIMEOUT_SECONDS` | `10` | Deadline for each source, including retries and limiter waits |
| `MAX_SOURCES_PER_QUERY` | `3` | Maximum results requested per source |
| `RETRY_MAX_ATTEMPTS` | `3` | Maximum attempts, including the initial call |
| `RETRY_INITIAL_DELAY_SECONDS` | `0.5` | Initial retry backoff |
| `RETRY_MAX_DELAY_SECONDS` | `4` | Backoff cap; server Retry-After may require a longer wait |
| `CACHE_TTL_SECONDS` | `86400` | Evidence cache lifetime in seconds |
| `DATABASE_URL` | blank | Use the default local SQLite file |

## Run the assistant

```powershell
uv run python -m researcher --help
uv run python -m researcher ask "Photovoltaic effect" --sources wiki,arxiv,web
uv run python -m researcher ask "How do solar panels convert sunlight into electricity?" --sources web --no-cache
```

`--sources` accepts `wiki`, `arxiv`, and `web`, separated by commas. Omitting it
selects all three. Output includes the question, answer, numbered references,
and any warnings. Questions must be nonempty and within the configured limit.

| Exit code | Meaning |
|---|---|
| `0` | An answer was produced, possibly with partial-source warnings |
| `1` | Research failed or no answer could be produced |
| `2` | Invalid arguments, input, or configuration |

### Cache behavior

SQLite is created automatically at
`~/.cache/research-assistant/sources.sqlite3`; no database server is required.
To use a project-local file, set `DATABASE_URL=sqlite:///.cache/sources.sqlite3`.
See [cache documentation](docs/caching.md) for details.

Run the same question twice without `--no-cache` to check `cache_hit=True` in the
logs. The cache stores source evidence, not generated answers: synthesis still
runs on each invocation. `--no-cache` bypasses evidence reads and writes, but
arXiv rate limiting remains active.

### Offline demo

The supplied demo requires no API keys or network access:

```powershell
uv run python demo_ai.py --offline --limit 5
```

This is the instructor-provided demo. To exercise the application's actual CLI
with offline provider responses, run the sample-question integration tests:

```powershell
uv run python -m pytest tests/test_sample_questions.py -v
```

## Web app

A Streamlit UI (`streamlit_app/`) is a thin client over a FastAPI HTTP API
(`src/webapi/api.py`), which in turn shares all research logic with the CLI
through `Researcher`/`AIService`/`SourceOrchestrator` — the web app adds no
new business logic of its own. Two things must be running at once, in two
separate terminals. Complete [Setup](#setup) first (`uv sync --locked` and a
filled-in `.env`); without real API keys, requests will reach the server but
research itself will fail.

### 1. Start the API

In its own terminal, from the project root:

```powershell
uv run uvicorn webapi.api:app --app-dir src --reload
```

Leave this running. Confirm it started with no errors — you should see
`Uvicorn running on http://127.0.0.1:8000` and `Application startup complete`.
You can sanity-check it independently of the UI by opening
`http://127.0.0.1:8000/docs` (FastAPI's interactive docs) in a browser.

### 2. Start the Streamlit UI, in a second terminal

```powershell
uv pip install -r streamlit_app/requirements.txt
uv run streamlit run streamlit_app/app.py
```

Opens at `http://localhost:8501`. Set the `RESEARCH_API_URL` environment
variable first if the API isn't at the default `http://127.0.0.1:8000/ask`.
History is stored locally at `~/.cache/research-assistant/streamlit_history.json`.

### Troubleshooting

**The page hangs on "Researching..." / loading forever, with no error.**
This means the UI can reach *something* on port 8000, but it isn't
responding — usually a previous API process left running in a bad state
(e.g. a terminal that was closed without stopping it). Check what's on the
port and stop it, then restart the API from step 1:

```powershell
Get-NetTCPConnection -LocalPort 8000 | Select-Object OwningProcess
Stop-Process -Id <OwningProcess value from above> -Force
```

**"Could not reach the research API."**
The API isn't running, or it's on a different port than the UI expects.
Confirm step 1 is still running in its terminal and re-check `RESEARCH_API_URL`.

**A request completes but returns no answer, only warnings.**
Not a bug — it means every source failed or timed out, or synthesis failed.
Check the API terminal's log output for the actual cause (e.g. an invalid or
quota-exhausted `GOOGLE_API_KEY`/`TAVILY_API_KEY` in `.env`).

## Tests and code quality

Tests use simulated providers or mock HTTP and require no API credentials.

```powershell
uv run python -m pytest -v --cov=researcher --cov-report=term-missing --cov-fail-under=60
uv run ruff check .
uv run black --check .
uv run isort --check-only .
uv run mypy src/researcher
```

Apply formatting and import sorting when needed:

```powershell
uv run isort .
uv run black .
```

CI runs lint, formatting, import sorting, type checking, tests with a 60% coverage
threshold, and Docker checks. Its runtime smoke test verifies cache persistence
across two containers with networking disabled. Live benchmarks are manual.
The four required PR checks are `lint`, `typecheck`, `test`, and `docker`; merge
only after they pass under the repository's branch protection rules.

## Docker

### Build and run

```powershell
docker build --target runtime -t research-assistant:runtime .
docker run --rm research-assistant:runtime
docker run --rm --env-file .env research-assistant:runtime ask "Photovoltaic effect" --sources wiki,arxiv,web --no-cache
```

Running the image without arguments prints help. The runtime starts the research
CLI as a non-root user and contains installed application packages and production
dependencies. It does not contain uv or the development tools.

### Persist the cache

```powershell
docker volume create research-assistant-cache
docker run --rm --env-file .env --env DATABASE_URL= --mount type=volume,source=research-assistant-cache,target=/home/appuser/.cache/research-assistant research-assistant:runtime ask "Photovoltaic effect" --sources wiki
```

Repeat the second command to check a cache hit. The empty `DATABASE_URL` override
uses the container's default path instead of a possible Windows path in `.env`.
The volume preserves SQLite evidence and arXiv pacing state between containers.
Host and container limiters are separate unless they share storage; avoid running
live arXiv checks simultaneously on the host and in Docker.

### Test image

Build the separate test target to run tests and development tools:

```powershell
docker build --target test -t research-assistant:test .
docker run --rm --network none research-assistant:test
docker run --rm --network none research-assistant:test uv run --locked --no-sync ruff check .
docker run --rm --network none research-assistant:test uv run --locked --no-sync black --check .
docker run --rm --network none research-assistant:test uv run --locked --no-sync isort --check-only .
docker run --rm --network none research-assistant:test uv run --locked --no-sync mypy src/researcher
```

Rebuild after code or dependency changes. The runtime image measured
**218.67 MB (218,667,271 bytes)** on 2026-09-16, below the 250 MB bonus target.
That integration build passed 255 Linux tests with 97.87% application coverage;
these are dated measurements, not a claim about the latest test count.
Base image tags are mutable, so future builds may differ. Measure your image:

```powershell
docker image inspect research-assistant:runtime --format '{{.Size}}'
```

See [Docker verification](docs/docker.md) for the recorded build and live results.

### Web app (Docker Compose)

The API and the Streamlit UI each have their own image, joined by a Docker
Compose network so they can reach each other by service name instead of
`localhost`. Requires a filled-in `.env` (see [Setup](#setup)).

```powershell
docker compose up --build
```

This builds `api` (the `api` target in the root `Dockerfile`, reusing the
same installed package as the CLI's `runtime` image) and `frontend` (a
separate, standalone image built from `streamlit_app/Dockerfile`, which
needs nothing from the rest of the project — only `streamlit` and
`requests`). The frontend's `RESEARCH_API_URL` is set to `http://api:8000/ask`,
using the `api` service's Compose-internal hostname; on the host, reach them
at `http://localhost:8000/docs` and `http://localhost:8501`. The API's SQLite
cache persists in a named volume (`research-cache`) across `docker compose
restart`, but is removed by `docker compose down --volumes`.

```powershell
docker compose down
```

## Sequential versus parallel collection

Run the repeatable offline benchmark over all five supplied questions:

```powershell
uv run python benchmarks/benchmark_sources.py --repeats 5
```

With simulated delays of 0.1, 0.2, and 0.3 seconds, recorded sequential medians
were 0.623–0.628 seconds and parallel medians were 0.312–0.314 seconds, a
**1.99–2.00x** speedup. This demonstrates overlapping waits, not live API speed.
See [offline methodology and results](docs/concurrency-benchmark.md).

The separate live benchmark uses actual sources and consumes web-search quota,
with caching disabled and no LLM synthesis:

```powershell
uv run python benchmarks/benchmark_live_sources.py --repeats 1
```

One repeat runs one sequential/parallel pair for each of the five questions.
Results overwrite `docs/live-benchmark-results.json`; use `--output` to preserve
a separate run. Recorded on 2026-09-16:

| Topic | Sequential (s) | Parallel (s) | Speedup |
|---|---:|---:|---:|
| Photosynthesis | 6.009 | 3.616 | 1.66x |
| Long context windows | 4.411 | 5.596 | 0.79x |
| Financial crisis | 6.521 | 3.624 | 1.80x |
| Fusion energy | 4.550 | 4.343 | 1.05x |
| CRISPR-Cas9 | 7.044 | 3.868 | 1.82x |

Parallel was faster in four of five pairs. Both modes returned the same URLs,
with three arXiv and three web results per question; Wikipedia returned no
matches. One pair per question is exploratory evidence, not a stable performance
guarantee. See [live methodology and raw results](docs/live-benchmark.md).

## Known limitations

- Full questions may produce no Wikipedia search matches. Short topic queries
  such as `Photovoltaic effect` worked in live checks. There is no query-rewriting
  fallback; other selected sources can still supply evidence.
- Provider outages, quotas, and rate limiting affect results and latency. Source
  collection may succeed while synthesis fails; retrieved evidence is still
  displayed. No automatic fallback LLM is configured.
- Source deadlines include arXiv pacing, redirects, and retries, so a waiting
  request can time out even when the provider is reachable.
- Citations identify retrieved evidence; they do not guarantee factual accuracy.

## Design and supporting documents

The CLI creates the Researcher service, which delegates parallel collection to
SourceOrchestrator and synthesis to AIService. AIService wraps the supplied
`ai.*` functions with retries and shared HTTP connections. SourceCache applies
TTL rules over SQLite storage. No separate HTTP server or frontend is required.

- [Live integration checks](docs/live-integration.md)
- [AI service and arXiv handling](docs/ai-service.md)
- [Caching](docs/caching.md)
- [Web-search setup](docs/web-search.md)
- [Team responsibilities](docs/Research-Assistant-Team-Plan.md)

## Contributing

Use a separate branch for each task and open a pull request into `main`.
Keep each change focused and ask a teammate to review it before merging.

### Start a branch

With a clean working tree, update `main` and create your branch:

```powershell
git switch main
git pull --ff-only origin main
git switch -c feat/question-validation
uv sync --locked
```

Use `<type>/<short-description>` for branch names, with lowercase words
separated by hyphens:

| Type | Purpose | Example branch |
|---|---|---|
| `feat` | New functionality | `feat/source-cache` |
| `fix` | Bug fix | `fix/source-timeout` |
| `docs` | Documentation | `docs/setup-guide` |
| `test` | Tests | `test/cache-expiration` |
| `refactor` | Code restructuring without behavior changes | `refactor/source-service` |
| `style` | Formatting only | `style/import-order` |
| `chore` | Dependencies and development tooling | `chore/update-ruff` |

### Make and check changes

Preserve the supplied `ai/` module and `tests/test_ai_smoke.py`.
Add tests for new behavior. Use `uv add` for dependencies and commit both
`pyproject.toml` and `uv.lock` when they change.

Before committing, run:

```powershell
uv run isort .
uv run black .
uv run ruff check .
uv run mypy src/researcher
uv run python -m pytest -v --cov=researcher --cov-report=term-missing --cov-fail-under=60
uv run python demo_ai.py --offline --limit 5
git diff
git status
```

Review formatting changes too, and include only files relevant to your task.
For Docker or dependency changes, repeat the Docker checks above.

### Commit and push

Use commit messages in the form `<type>: <short description>`.
Use the types listed above and describe the action, for example:

```text
feat: add question length validation
fix: handle source timeouts gracefully
docs: explain local setup
test: cover expired cache entries
chore: update development dependencies
```

Stage the files for your task, review the staged changes, then commit and push.
Replace the example paths, message, and branch name with your own:

```powershell
git add path/to/changed-file.py path/to/test-file.py
git diff --cached
git commit -m "feat: add question length validation"
git push -u origin feat/question-validation
```

### Open a pull request

On GitHub, open a pull request from your branch into `main`. Include:

- What changed and why.
- How you tested it and the results.
- Any related issue or remaining limitations.

Request a teammate's review. Push follow-up commits to the same branch to
update the pull request. Merge after review and passing checks, then delete
the merged branch on GitHub. Start your next task from an updated `main`.
