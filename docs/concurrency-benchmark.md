# Offline concurrency benchmark

## Purpose

Compare sequential source collection with the actual `SourceOrchestrator`
under repeatable simulated I/O delays. This measures the benefit of overlapping
waiting time, not live API performance or answer quality.

## Run

From the project root, after `uv sync --locked`:

```powershell
uv run python benchmarks/benchmark_sources.py --repeats 5
```

`--repeats` must be at least 1. No API keys or network access are needed.

## Method

- One fixed question and three fake sources, returning identical evidence in
  both modes. Delays use `asyncio.sleep`: wiki 0.1 s, arxiv 0.2 s, web 0.3 s.
- Sequential mode awaits each source in order. Parallel mode uses the actual
  orchestrator with `use_cache=False`.
- Each mode shares one HTTP client across its sources, with a transport that
  rejects network calls. Each source has a five-second deadline.
- Cache access and synthesis are forbidden. No retries are simulated.
- One warm-up per mode is excluded. Five measured runs per mode alternate
  execution order to reduce ordering bias.
- `perf_counter` measures client setup, collection, and cleanup. Parallel mode
  also includes outcome construction and orchestration bookkeeping.
- Source lists are compared after warm-up and every measured pair. Unexpected
  parallel failures or warnings abort the benchmark.

## Recorded results

Measured on 2026-09-10 under the agent execution environment on Windows 11,
AMD64, Python 3.12.11. Execution used the existing project virtual environment:

```powershell
.\.venv\Scripts\python.exe -B benchmarks/benchmark_sources.py --repeats 5
```

| Mode | Minimum (s) | Median (s) | Maximum (s) |
|---|---:|---:|---:|
| Sequential | 0.6211 | 0.6213 | 0.6248 |
| Parallel | 0.3110 | 0.3118 | 0.3121 |

Median speedup: **1.99x**, calculated as sequential median divided by parallel
median. Evidence matched in every comparison.

The ideal waiting times are 0.6 s sequentially (sum of delays) and 0.3 s
concurrently (longest delay). The measured values include scheduling and
application overhead.

## Limits

- Results are an offline simulation, not a claim about live research speed.
- Network variability, API rate limits, retries, synthesis, and persistent
  storage are not measured.
- Five repetitions summarize this run; they do not establish a statistical
  guarantee. Timings depend on the machine and its workload.
- This uses one fixed question. A later live benchmark can use the five sample
  questions with equal source limits, cache bypass, and matched retry settings.
- Docker timing has not been measured here. Rerun and record separate results
  if container performance is needed.
