# Live source-collection benchmark

## Run

Configure `.env` for Tavily, then run from the project root:

```powershell
uv run python benchmarks/benchmark_live_sources.py --repeats 1
```

This makes real Wikipedia, arXiv, and web-search requests for all five supplied
questions. One repeat means ten web-search operations before any retries. It
never calls Gemini or another LLM. Results are saved incrementally to
`docs/live-benchmark-results.json`; use `--output path/to/results.json` to keep
a later run separate. This script is not run by CI.

## Method

- Use the exact question text from `data/research_questions.json`, with all three
  sources selected for both modes and evidence caching disabled.
- Parallel mode calls the production orchestrator. Sequential mode awaits its
  existing per-source method in order (wiki, arxiv, web), preserving identical
  deadlines, retries, failure handling, and URL deduplication. This benchmark
  intentionally depends on the orchestrator's private methods; its offline tests
  check both paths for partial failures, shared clients, and cache bypass.
- Each mode creates one shared HTTP client. Timings include client setup,
  fetching, retries, rate-limiter waits within collection, and cleanup.
- Alternate which mode runs first between questions. No warm-up calls are made.
- Wait 3.1 seconds before each collection, outside the measured interval, to
  reduce carry-over from ordinary arXiv pacing. The production limiter remains
  active for redirects and server cooldowns. Do not run other live arXiv tasks
  alongside the benchmark.
- Record each source's status, count, elapsed time, and URLs, as well as total
  collection time and unique result count. Timing is not an answer-quality score.

## Results

Measured on 2026-09-16 (Asia/Baku), Windows, Python 3.12.11. The raw record starts
at 2026-09-15 20:42:47 UTC. Settings: ten-second per-source deadline, three maximum
attempts, and three results per source. One measured pair per question:

| Sample | Topic | Sequential (s) | Parallel (s) | Sequential / parallel |
|---|---|---:|---:|---:|
| q1 | Photosynthesis | 6.009 | 3.616 | 1.66x |
| q2 | Long context windows | 4.411 | 5.596 | 0.79x |
| q3 | Financial crisis | 6.521 | 3.624 | 1.80x |
| q4 | Fusion energy | 4.550 | 4.343 | 1.05x |
| q5 | CRISPR-Cas9 | 7.044 | 3.868 | 1.82x |

Every collection returned wiki=empty/0, arxiv=ok/3, and web=ok/3, producing six
unique results. Ordered result URLs matched between the two modes for every
question. There were no failed or timed-out source outcomes.

Parallel collection was faster in four of five pairs, but slower for q2.
Network latency, provider-side caching, and arXiv pacing affect these timings;
one pair cannot identify the cause of q2's difference or establish a reliable
speedup. The separate [offline benchmark](concurrency-benchmark.md) controls
source delays to demonstrate the scheduling benefit reproducibly.

Wikipedia's empty responses expose the limitation of using full natural-language
questions with the supplied search fetcher. These results demonstrate live
collection from arXiv and Tavily, not successful evidence retrieval from all
three sources. No query rewriting or source substitutions were used to improve
the reported outcome. Live synthesis was deliberately excluded.

For stronger performance conclusions, repeat with `--repeats 3` or more and
compare distributions while retaining status and result-count checks. Additional
runs consume provider quota. No extra runs were made solely to obtain a faster
parallel result.
