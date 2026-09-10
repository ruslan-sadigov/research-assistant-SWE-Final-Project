# Multi-stage Docker setup

## Status

The Dockerfile defines builder, test, and runtime stages. This document records
the current design and will be updated after application integration.

The current multi-stage build was verified on 2026-09-10. Both targets build,
all 102 tests pass in the test image, and the runtime image is 193.23 MB.
The runtime still starts the supplied offline demo. Repeat these checks after
the real CLI, provider dependencies, and persistent storage are integrated.

## Stages

| Stage | Purpose | Contents |
|---|---|---|
| `builder` | Install runtime dependencies and application packages | Python 3.12, uv, source files, and `/app/.venv` without development dependencies |
| `test` | Run tests and development checks | Builder contents, repository files, and development dependencies |
| `runtime` | Run the application with fewer installed tools | Fresh Python 3.12 image, the builder's installed environment, demo script, and sample data |

The test stage inherits from the builder. The runtime stage starts fresh and
copies only the installed virtual environment and required demo assets; it
does not inherit test tools or uv's build cache. Runtime is the final stage,
so a build without `--target` selects it.

## Installation decisions

- Copy `pyproject.toml` and `uv.lock` before source files to cache dependency
  installation separately from application changes.
- Use `uv sync --locked` to require a consistent lockfile during builds.
- Use `--no-dev` in the builder to omit development dependencies from runtime.
- Use `--no-install-project` until application source files are available.
- Use `--no-editable` when installing the project so runtime imports do not
  depend on the original `src/` directory. Both `researcher` and `ai` are
  installed according to the project build configuration.
- The test target runs uv with `--no-sync`, using the environment prepared
  during the build without modifying it at container startup.
- `UV_LINK_MODE=copy` avoids relying on links to the installation cache.
- `UV_PYTHON_DOWNLOADS=never` requires the Python interpreter supplied by the
  base image.

## Runtime user and filesystem

`useradd --create-home appuser` creates a regular Linux user inside the image.
`USER appuser` makes the application run as that user instead of root. This is
independent of the developer's Windows or GitHub account.

The copied application files are root-owned and readable by `appuser`. The
current demo needs no writable application directory. SQLite integration must
provide a writable data directory or volume with appropriate ownership; do
not assume `appuser` can create a database under `/app`.

The runtime prepends `/app/.venv/bin` to `PATH`, disables Python bytecode writes,
and enables unbuffered output. The virtual environment is copied to the same
absolute path where it was built, and both stages use Python 3.12 slim images.

## Build and verify

Run from the project root with Docker Desktop running Linux containers.

Build the test and runtime targets:

```powershell
docker build --target test -t research-assistant:test .
docker build --target runtime -t research-assistant:runtime .
```

Run the test suite and development checks:

```powershell
docker run --rm research-assistant:test
docker run --rm research-assistant:test uv run --locked --no-sync ruff check .
docker run --rm research-assistant:test uv run --locked --no-sync black --check .
docker run --rm research-assistant:test uv run --locked --no-sync isort --check-only .
```

Run the offline demo and verify installed package imports:

```powershell
docker run --rm research-assistant:runtime
docker run --rm research-assistant:runtime python -c "import ai, researcher; print('Installed packages import successfully')"
```

The runtime image has no uv or pytest. Use the test image for development
commands. Rebuild after changing application files or dependencies.

## Image size and verification record

```powershell
docker image ls research-assistant
docker image inspect research-assistant:runtime --format '{{.Size}}'
```

The inspect command reports bytes. Use 250,000,000 bytes as a conservative
threshold for the bonus's 250 MB runtime limit. Multi-stage construction does
not guarantee meeting it; actual installed dependencies determine the size.

| Check | Current result |
|---|---|
| Test target build | Passed |
| Runtime target build | Passed |
| Tests in test image | 102 passed in 0.74 s |
| Lint and formatting checks in test image | Ruff, Black, and isort passed |
| Offline runtime demo | All five sample questions completed |
| Installed package imports in runtime | `ai` and `researcher` imported successfully |
| Runtime image size | 193,229,678 bytes (193.23 MB), below 250,000,000 bytes |
| Test image size | 405,444,608 bytes (405.44 MB); development tools included |
| Runtime user | UID 1000, non-root |
| Runtime development-tool exclusion | uv executable and pytest module absent |

Measurements used Docker Desktop Linux containers on a Windows host,
`linux/amd64`, and Python 3.12.14 inside the images. Sizes are Docker image
inspect `.Size` values in decimal MB, not compressed download sizes. Build
cache was used; this was not a clean-cache build performance measurement.

Verified image IDs:

- Runtime: `sha256:0ca4f9363bf10a9fdb7bdc0066513ec931b572bb8c684d0326e383fa1e9ec225`
- Test: `sha256:33832ccfa95f9bfea1556886d43ff65b95f92e30aee5d251b490146b2f68aab5`

These results establish the current baseline only. Rebuild both targets and
update this record after integration; dependency changes can affect image size.

## Final integration work

- Replace the demo startup command with the agreed research CLI entry point.
- Add and verify writable SQLite storage and persistence across container runs.
- Supply provider credentials at runtime, keeping populated `.env` files out
  of the image and repository.
- Add type-checking and coverage commands to CI when those tools are configured.
- Pin the uv image version or digest; `uv:latest` currently changes over time.
- Rebuild and remeasure after provider and database dependencies are finalized.
- Verify the real integrated workflow in addition to the offline demo.
- Add the final image size to the README before submission, as required by the
  bonus. Keep interim documentation here until that final update.

The README is intentionally unchanged at this stage.
