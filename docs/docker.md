# Docker CLI integration

## Build

Run from the project root with Docker Desktop using Linux containers:

```powershell
docker build --target test -t research-assistant:test .
docker build --target runtime -t research-assistant:runtime .
```

The builder installs locked production dependencies and non-editable application
packages. The test target adds development dependencies and tests. The runtime
copies only the installed environment, runs as `appuser`, and starts
`python -m researcher`. Its default argument is `--help`; it makes no network
requests when run without arguments. It does not include uv, pytest, demo files,
or the source checkout.

## Run the real CLI

```powershell
docker run --rm research-assistant:runtime
docker run --rm --env-file .env research-assistant:runtime ask "Photovoltaic effect" --sources wiki,arxiv,web --no-cache
```

Supply keys only at runtime. `.dockerignore` excludes `.env` from both image
builds. Docker env files use plain `NAME=value` entries: put comments on separate
lines and avoid surrounding quotes. Docker does not parse dotenv inline comments
like python-dotenv does. Keep populated env files out of Git.

## Persistent cache and arXiv limiter

The image creates `/home/appuser/.cache/research-assistant` owned by its non-root
user. Mount a named volume there so SQLite evidence and arXiv pacing/cooldown
state survive container removal:

```powershell
docker volume create research-assistant-cache
docker run --rm --env-file .env --env DATABASE_URL= --mount type=volume,source=research-assistant-cache,target=/home/appuser/.cache/research-assistant research-assistant:runtime ask "Photovoltaic effect" --sources wiki
```

Run the second command twice. The second run should report `cache_hit=True`.
Gemini still synthesizes each answer. The empty DATABASE_URL override uses the
container's default SQLite path instead of a host-specific path from `.env`.
Docker initializes a fresh named volume from the image directory, including its
ownership. Existing volumes with different ownership may need separate repair.

Containers using the same volume share the arXiv limiter; the Windows host's
limiter remains separate. Coordinate host/container live arXiv calls. `--no-cache`
bypasses evidence storage, not rate limiting. Removing the volume deletes its
cache and cooldown state; do not remove it while requests are active.

## Offline validation and CI

```powershell
docker run --rm --network none research-assistant:test
docker run --rm --network none research-assistant:test uv run --locked --no-sync ruff check .
docker run --rm --network none research-assistant:test uv run --locked --no-sync black --check .
docker run --rm --network none research-assistant:test uv run --locked --no-sync isort --check-only .
docker run --rm --network none --entrypoint python research-assistant:runtime -c "import ai, researcher; from google import genai"
```

CI runs CLI help and `tests/docker_smoke.py` in two separate runtime containers
with networking disabled and a shared temporary named volume. The smoke script
replaces only AI provider functions with deterministic fakes; it exercises the
installed CLI, orchestration, synthesis wrapper, rendering, and SQLite. The first
run must fetch; the second must reuse cached sources. The script is mounted
read-only for testing and is not shipped in the runtime image. CI removes its
temporary volume afterwards. No API keys are required.

The existing test target also verifies Linux file locking for arXiv. The old
standalone offline demo can still run on the host or in the test image.

## Verification record

Verified on 2026-09-16 (Asia/Baku), Docker Desktop linux/amd64, Python 3.12.14:

| Check | Result |
|---|---|
| Test and runtime builds | Passed |
| Linux test suite | 255 passed in 2.53 seconds |
| Linux application coverage | 97.87% |
| Linux quality checks | Ruff, Black, isort, and mypy passed |
| Offline installed CLI | Passed |
| Separate-container SQLite persistence | First run miss, second run hit |
| Runtime UID | 1000 (non-root) |
| Runtime imports | ai, researcher, and google.genai available |
| Runtime exclusions | uv, pytest, and /app/.env absent |
| Runtime image size | 218,667,271 bytes (218.67 MB), below 250 MB |
| Test image size (initial integration build) | 569,654,118 bytes (569.65 MB) |

A live container query for `Photovoltaic effect` retrieved three results each
from Wikipedia (2.388 s), arXiv (3.533 s), and Tavily (0.527 s). Collection took
3.636 seconds and produced eight unique sources. Gemini returned HTTP 200,
references were printed, and the container exited with code 0. This diagnostic
used a ten-second source deadline and one attempt per operation. Credentials
were loaded locally and passed by environment-variable names, not baked into
the image or printed. No evidence volume was used for that uncached live run.

CI remains offline. Its SQLite check uses a fresh temporary named volume and
removes that volume afterwards. The checked runtime image ID is
`sha256:88cad388ab0c1f29ada7460ab6ea5dc02b29d2f1a7ccc4fee77277454f7899ca`.

The Linux mypy check exposed an error in instructor-owned
`ai/providers/openai.py`. A module-specific mypy override suppresses diagnostics
inside `ai` while retaining imported type information and checking our own code.
No instructor files were modified.
The historical 2026-09-10 image was 193.23 MB with 102 tests and the old demo entry
point; those measurements do not describe this integrated image.

The runtime size target remains below 250,000,000 bytes. Measure it with:

```powershell
docker image inspect research-assistant:runtime --format '{{.Size}}'
```

The Python base tag and `uv:latest` remain mutable. Pinning their versions/digests
and publishing the final README are separate follow-up tasks. README is unchanged.
