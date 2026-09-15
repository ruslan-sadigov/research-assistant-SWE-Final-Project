# Offline concurrency benchmark

## Purpose

Compare sequential source collection with the actual `SourceOrchestrator`
under repeatable simulated I/O delays. This measures the benefit of overlapping
waiting time, not live API performance or answer quality.

For a separate comparison using actual providers and all five questions, see
the [live benchmark and its limitations](live-benchmark.md).

## Run

From the project root, after `uv sync --locked`:

```powershell
uv run python benchmarks/benchmark_sources.py --repeats 5
```

`--repeats` must be at least 1. No API keys or network access are needed.

## Method

- All five questions from `data/research_questions.json` and three fake sources,
  returning identical evidence in both modes. Every question uses all three
  sources, regardless of its `expected_sources` tags, to compare the same workload.
  Delays use `asyncio.sleep`: wiki 0.1 s, arxiv 0.2 s, web 0.3 s.
- Sequential mode awaits each source in order. Parallel mode uses the actual
  orchestrator with `use_cache=False`.
- Each mode shares one HTTP client across its sources, with a transport that
  rejects network calls. Each source has a five-second deadline.
- Cache access and synthesis are forbidden. No retries are simulated.
- For each question, one warm-up per mode is excluded. Five measured runs per mode alternate
  execution order to reduce ordering bias.
- `perf_counter` measures client setup, collection, and cleanup. Parallel mode
  also includes outcome construction and orchestration bookkeeping.
- Source lists are compared after warm-up and every measured pair. Unexpected
  parallel failures or warnings abort the benchmark.

## Recorded results

Measured on 2026-09-16 under the agent execution environment on Windows 11,
AMD64, Python 3.12.11. Execution used the existing project virtual environment:

```powershell
.\.venv\Scripts\python.exe -B benchmarks/benchmark_sources.py --repeats 5
```

| Sample | Topic | Sequential median (s) | Parallel median (s) | Speedup |
|---|---|---:|---:|---:|
| q1 | Photosynthesis | 0.6282 | 0.3144 | 2.00x |
| q2 | Long context windows | 0.6238 | 0.3126 | 2.00x |
| q3 | Financial crisis | 0.6227 | 0.3135 | 1.99x |
| q4 | Fusion energy | 0.6239 | 0.3133 | 1.99x |
| q5 | CRISPR-Cas9 | 0.6226 | 0.3117 | 2.00x |

Speedup is calculated as sequential median divided by parallel median for each
question. Evidence matched in every warm-up and measured comparison.

The ideal waiting times are 0.6 s sequentially (sum of delays) and 0.3 s
concurrently (longest delay). The measured values include scheduling and
application overhead.

## Limits

- Results are an offline simulation, not a claim about live research speed.
- Network variability, API rate limits, retries, synthesis, and persistent
  storage are not measured.
- Five repetitions summarize this run; they do not establish a statistical
  guarantee. Timings depend on the machine and its workload.
- All questions use identical simulated delays. Differences between topics do
  not measure research difficulty or real provider performance.
- Docker timing has not been measured here. Rerun and record separate results
  if container performance is needed.

## Offline sample-question integration checks

```powershell
uv run python -m pytest tests/test_sample_questions.py -v
```

Ten parameterized cases cover all five supplied questions, both with all three
sources and with each question's `expected_sources` subset (`wikipedia` maps to
CLI source `wiki`). Each case calls the real CLI three times: initial retrieval,
persistent SQLite cache reuse, then explicit cache bypass. Assertions check exit
codes, source selection, synthesis calls, answers, references, and fetch counts.

Only the `ai.fetch_*` and `ai.synthesize` provider boundaries are replaced with
deterministic fakes. HTTPX sends are blocked. The real service, orchestrator,
rendering, and per-test SQLite store execute; no credentials or API quota are
needed. The existing pytest CI jobs discover these cases automatically.

All ten cases passed on 2026-09-16. This verifies integration, not the accuracy of
generated answers or live retrieval for the sample questions.
