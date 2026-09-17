# Autonomous Research Assistant

> An asynchronous research tool that queries external adapters, handles source routing, enforces timeouts, and orchestrates concurrency with resilient retry policies.

Team: Research Assistant Team  •  Topic: 4 •  Course: AI-ENG-110 Software Engineering, AI Academy

Due: September 19, 2026 at 23:59 (UTC+4)

---

## Quick start

```bash
# 1. Clone & install
git clone https://github.com/ruslan-sadigov/research-assistant-SWE-Final-Project/tree/main/.github
cd research-assistant-SWE-Final-Project
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env       
# (DO NOT commit .env — it is in .gitignore)

# 3. Run the smoke tests
pytest tests/test_ai_smoke.py -v   # provided smoke tests
pytest $env:PYTHONPATH="src;." ; python -m pytest tests/test_ai_service.py -v    # your full suite

# 4. Run the demo
python -m researcher demo
```

## Run with Docker

```bash
docker build -t research-assistant .
docker run --env-file .env -p 8000:8000 research-assistant
```

## Environment variables

| Variable | Required? | Default | What it controls |
|---|---|---|---|
| `LLM_PROVIDER` | yes | `anthropic` | `anthropic` \| `openai` \| `gemini` |
| `LLM_MODEL` | yes | (provider-specific) | gemini-3.5-flash-lite |
| `EMBEDDING_PROVIDER` | no | `openai` | `openai` \| `gemini` (Topics 1, 3 only) |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `DATABASE_URL` | no | `sqlite:///./app.db` | SQLite path |
| `MAX_PARALLEL` | no | `10` | semaphore bound for concurrent calls |
| _[...]_ | _[...]_ | _[...]_ | _[...]_ |

The full list is in `.env.example`. 

## How to run the demo

```bash
# CLI
python -m researcher fetch --provider arxiv --query "quantum computing"

```

_[{
  "provider": "arxiv",
  "query": "quantum computing",
  "status": "success",
  "results_count": 5
}]_

## Sequential vs concurrent benchmark

| Workload | $N$ | Sequential | Concurrent (sem=10) | Speedup |
|---|---|---|---|---|
| [Multi-source concurrent query retrieval (arXiv, Wiki, Web)] | [3] | [0.62 s] | [0.31 s] | [2×] |

**Reproduce:**
```bash
python scripts/bench.py --N 20
```

Bottleneck after the parallelization is provider network latency and external rate limits. See `report/report.pdf` §[3.2] for details.

## Testing

```bash
pytest --cov=src --cov-report=term-missing
```

- Total coverage: **98.44%**
- Provided AI smoke tests: **passing**
- All tests run offline (AI module mocked; HTTP layer mocked with `respx` / `unittest.mock`).

## Project layout

```
.
├── ai/                       # PROVIDED — do not modify
├── src/
│   ├── config.py
│   ├── models.py
│   ├── services/             # wrappers around ai/, retries, logging
│   ├── core/                 # business logic
│   ├── concurrency/          # async orchestration
│   ├── storage/              # SQLite + filesystem
│   ├── cli.py
│   
├── tests/
├── data/                     # sample inputs
├── artefacts/                # outputs of demo runs
├── scripts/
│   ├── demo.py
│   └── bench.py
├── report/
│   ├── report.tex
│   └── report.pdf
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md
```

## Architecture in one diagram

![alt text](image.png)


## Limitations

- Wikipedia and full questions. Live runs of the ve supplied questions returned no Wikipedia
matches; short topic queries such as Photovoltaic e ect worked. With another week we would
extract a topic query rst.
- Single LLM, no fallback. A Gemini outage or quota limit means evidence without an answer.
Productionising would need a second provider and quota monitoring.
- Cache freshness. Our weakest design decision: the key ignores provider settings, empty results
live for the full TTL, and nothing evicts old rows.

See `report/report.pdf` §[8] for a full discussion.

## Tools & acknowledgements

We used AI assistants (Claude / Cursor / etc.) as described in §[9] of the report and in `templates/CONTRIBUTION_STATEMENT.md`.

## License

This is academic coursework, not a published library. 