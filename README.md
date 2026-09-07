# Async Research Assistant

## Setup

Requires uv and Python 3.12. Run commands from the project root.

```powershell
uv sync --locked
```

## Run the offline demo

No API keys required.

```powershell
uv run python demo_ai.py --offline --limit 5
```

## Run tests

```powershell
uv run python -m pytest -v
```

## Check formatting and linting

These commands do not modify code:

```powershell
uv run isort --check-only .
uv run black --check .
uv run ruff check .
```

## Apply formatting

```powershell
uv run isort .
uv run black .
```

## Run with Docker

Start Docker Desktop, then build:

```powershell
docker build -t research-assistant .
```

Run the offline demo:

```powershell
docker run --rm research-assistant
```

Run tests:

```powershell
docker run --rm research-assistant uv run --locked python -m pytest -v
```

Run formatting and lint checks:

```powershell
docker run --rm research-assistant uv run --locked isort --check-only .
docker run --rm research-assistant uv run --locked black --check .
docker run --rm research-assistant uv run --locked ruff check .
```

Rebuild the image after changing code or dependencies.

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
uv run python -m pytest -v
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
