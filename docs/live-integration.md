# Live CLI integration results

Recorded from user-provided terminal output on 2026-09-16, using the local
Windows CLI and `gemini-3.5-flash-lite`. These are individual live observations,
not controlled benchmarks or guarantees of provider availability.

## Web search and synthesis

```powershell
uv run python -m researcher ask "How do solar panels convert sunlight into electricity?" --sources web --no-cache
```

Tavily returned HTTP 200 and three sources in 0.530 seconds. Collection took
0.763 seconds. Gemini returned HTTP 200; the CLI printed an answer and three
numbered web references. The SDK's AFC warning did not prevent completion.

## All three sources

```powershell
uv run python -m researcher ask "Photovoltaic effect" --sources wiki,arxiv,web --no-cache
```

| Source | Status | Results | Elapsed seconds |
|---|---|---|---|
| Wikipedia | ok | 3 | 3.968 |
| arXiv | ok | 3 | 3.757 |
| Web (Tavily) | ok | 3 | 1.608 |

Collection completed in 4.194 seconds. Nine retrieved results became eight
unique sources after URL deduplication. Gemini returned HTTP 200 and produced
an answer citing Wikipedia, arXiv, and web evidence. References retained their
original evidence indices: [1], [3], [4], and [8]. No source warnings appeared.

The overlapping source requests demonstrate concurrent collection. Do not use
this single run as a measured sequential-versus-parallel speedup; the controlled
offline benchmark is documented separately.

## Other completed checks

- Wikipedia-only and arXiv-only retrieval and Gemini synthesis succeeded in
  earlier user runs.
- The user confirmed `cache_hit=True` when repeating an arXiv query with caching
  enabled. Source caching occurs before synthesis and survives synthesis failure.
- A previous live run preserved Wikipedia evidence when arXiv timed out and
  produced an answer with an arXiv warning.
- Gemini intermittently returned 503. A direct diagnostic confirmed the model
  was experiencing high demand; later calls succeeded.

These records cover the host CLI. Container verification is recorded separately
in [Docker setup](docker.md). Credentials remain in local configuration only.
