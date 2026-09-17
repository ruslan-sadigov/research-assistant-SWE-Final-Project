# Defense demo

A 90-second live demo of the Docker runtime image: one short question against
all three sources, run twice with a persistent cache. The second run is served
from SQLite and still produces an answer. Slide 8 shows the same command.

Use a short topic such as `Photovoltaic effect`: live runs found no Wikipedia
matches for full-sentence questions.

## Preparation (before the defense)

Run these from the project root in PowerShell.

1. Start Docker Desktop and confirm it answers:

   ```powershell
   docker version
   ```

2. Create `.env` from the template and fill in the keys. Keep one unquoted
   `NAME=value` per line and never commit this file.

   ```powershell
   Copy-Item .env.example .env
   ```

   Needed: `LLM_PROVIDER=gemini`, `LLM_MODEL=gemini-3.5-flash-lite`,
   `GOOGLE_API_KEY`, `WEB_SEARCH_PROVIDER=tavily`, `TAVILY_API_KEY`,
   `LOG_LEVEL=INFO` (so the `cache_hit` lines are visible).

3. Build both images:

   ```powershell
   docker build --target runtime -t research-assistant:runtime .
   docker build --target test -t research-assistant:test .
   ```

4. Start from an empty cache volume:

   ```powershell
   docker volume rm research-assistant-cache
   docker volume create research-assistant-cache
   ```

   The first command reports an error if the volume does not exist yet; that is fine.

5. Rehearse the two demo runs below once, then empty the volume again (step 4)
   so the first run on stage is a real cache miss.

6. Record a screencast of the rehearsal as a backup. Keep it outside Git and put
   its link in the submission documents.

## Demo

**Run 1 (cache empty):**

```powershell
docker run --rm --env-file .env --env DATABASE_URL= `
  -v research-assistant-cache:/home/appuser/.cache/research-assistant `
  research-assistant:runtime ask "Photovoltaic effect" --sources wiki,arxiv,web
$LASTEXITCODE
```

What to point at (the answer wording changes between runs):

- Log lines `source completed: source=wiki ... cache_hit=False`, and the same
  for `arxiv` and `web`, followed by `collection completed: requested_sources=3`.
- `A:` with bracketed citations such as `[1]`.
- `References:` listing `(wikipedia)`, `(arxiv)` and `(web)` entries with URLs.
- Exit code `0`.

**Run 2 (same command, press Up and Enter):**

- Every `source completed` line now shows `cache_hit=True`, and no source is
  fetched again.
- An answer is still printed: answers are never cached, only evidence.

**Optional, 10 seconds, no network:** input validation.

```powershell
docker run --rm research-assistant:runtime ask "Photovoltaic effect" --sources wiki,books
$LASTEXITCODE
```

Expected: `Invalid input: Unknown source(s): books. Valid: wiki, arxiv, web`
and exit code `2`.

## If something fails on stage

| Symptom | What to do |
|---|---|
| A source prints a warning | Keep going: this is the partial-failure design. The answer uses the other sources' evidence. |
| `A: No answer could be produced - synthesis failed.` | Gemini is unavailable or out of quota. Show that the retrieved sources are still listed, then use a fallback below. |
| Tavily quota or key error | Rerun with `--sources wiki,arxiv`. |
| No network, Docker, or keys | Play the screencast, then run a fallback. |

**Fallback 1, offline cache demo (no keys, about 5 seconds):**

```powershell
uv run python tests/cache_verification.py
```

It runs the real CLI with simulated providers and ends with:

```text
Hit rate = hits / eligible lookups
  cold       0 / 15  = 0.0%
  warm      15 / 15  = 100.0%
  combined  15 / 30  = 50.0%
...
RESULT: all checks passed
```

**Fallback 2, full offline test suite in Docker:**

```powershell
docker run --rm --network none research-assistant:test
```

All tests pass without network access or keys.

## Troubleshooting

- `Unable to find image`: run the build commands in step 3.
- `error during connect`: Docker Desktop is not running.
- `Invalid configuration; check environment settings and the .env file.`:
  a value in `.env` is invalid, for example a quoted value or a comment on the
  same line as a setting.
- No `cache_hit` lines: set `LOG_LEVEL=INFO` in `.env`.

More detail: [Docker](../../docs/docker.md),
[cache verification](../../docs/cache-verification.md).
