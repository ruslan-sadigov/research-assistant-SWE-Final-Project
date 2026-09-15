## What this PR changes

<!-- Summarize the resulting behavior in 1–3 sentences. -->

## Why

<!-- Explain the problem, design decision, or assignment requirement.
Link a relevant issue if one exists; use Closes #123 only when this PR resolves it. -->

## How I tested it

<!-- Check only what you actually verified. Mark checks that do not apply as N/A
with a short reason. For documentation-only changes, describe link/command review
instead of claiming application tests were run. Include relevant results below. -->

- [ ] `uv run python -m pytest -v --cov=researcher --cov-report=term-missing --cov-fail-under=60`
- [ ] Supplied smoke tests pass (included above, or run `uv run python -m pytest tests/test_ai_smoke.py -v`).
- [ ] `uv run ruff check .`
- [ ] `uv run black --check .`
- [ ] `uv run isort --check-only .`
- [ ] `uv run mypy src/researcher`
- [ ] For Docker or dependency changes, both image targets build and offline checks pass:

```powershell
docker build --target test -t research-assistant:test .
docker run --rm --network none research-assistant:test
docker build --target runtime -t research-assistant:runtime .
docker run --rm --network none research-assistant:runtime
```

<!-- Add test counts, coverage, manual steps, and expected output as appropriate.
Distinguish offline tests from live API checks. Never paste credentials. -->

## Scope and remaining work

<!-- State relevant exclusions, limitations, or follow-up work. Link issues when available. -->

## Checklist

- [ ] No `.env`, credentials, private files, or local database files are included.
- [ ] No unresolved `TODO` / `FIXME` comments remain in changed code.
- [ ] New public functions and methods have type hints.
- [ ] No bare `except:` or silently discarded exceptions (`except Exception: pass`).
- [ ] Runtime diagnostics use `logging`; CLI output may use `print()`.
- [ ] The supplied `ai/` files and `tests/test_ai_smoke.py` are unchanged.
- [ ] Dependency changes include the corresponding `pyproject.toml` and `uv.lock` updates.
- [ ] Documentation reflects relevant changes to setup, configuration, or behavior.
- [ ] I can explain the changed code, including any AI-assisted contributions.

## AI assistant disclosure

<!-- State “None” or name the assistant, affected modules/files, and how its output
was reviewed, adapted, and verified. Be specific and accurate. -->

## Supporting evidence

<!-- Optional: sanitized CLI output, screenshots, benchmark tables, or links to
verification documents. Remove this section if it is not applicable. -->
