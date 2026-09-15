"""Measure real source collection; consumes search quota but never calls an LLM."""

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from researcher.concurrency.orchestrator import SourceOrchestrator
from researcher.config import load_settings
from researcher.models import CollectionResult
from researcher.services.ai_service import AIService
from researcher.services.cache import SourceCache
from researcher.storage.cache_store import InMemoryCacheStore

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ["wiki", "arxiv", "web"]


async def collect(orchestrator, question, mode):
    if mode == "parallel":
        return await orchestrator.collect_sources(question, SOURCES, use_cache=False)
    started = perf_counter()
    # Reuse the exact per-source deadline/error handling of production; only
    # scheduling changes. Both modes open one shared client per collection.
    async with orchestrator._ai_service.open_source_client() as client:
        outcomes = []
        for source in SOURCES:
            outcomes.append(
                await orchestrator._fetch_one(source, question, client=client, use_cache=False)
            )
    unique = {}
    for outcome in outcomes:
        for source in outcome.sources:
            unique.setdefault(source.url, source)
    return CollectionResult(
        outcomes=outcomes, sources=list(unique.values()), elapsed_seconds=perf_counter() - started
    )


async def run(args):
    settings = load_settings()
    questions = json.loads((ROOT / "data/research_questions.json").read_text(encoding="utf-8"))[
        "questions"
    ]
    store = InMemoryCacheStore()
    orchestrator = SourceOrchestrator(
        settings, AIService(settings), SourceCache(store, settings.cache_ttl_seconds)
    )
    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "repeats": args.repeats,
        "per_source_timeout_seconds": settings.per_source_timeout_seconds,
        "retry_max_attempts": settings.retry_max_attempts,
        "max_sources_per_query": settings.max_sources_per_query,
        "cache": False,
        "synthesis": False,
        "runs": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for index, sample in enumerate(questions):
            for repeat in range(args.repeats):
                order = ("sequential", "parallel")
                if (index + repeat) % 2:
                    order = tuple(reversed(order))
                for mode in order:
                    # Allow ordinary arXiv inter-request spacing before timing.
                    # The real limiter still enforces redirects and Retry-After.
                    await asyncio.sleep(3.1)
                    result = await collect(orchestrator, sample["text"], mode)
                    record = {
                        "sample": sample["id"],
                        "question": sample["text"],
                        "repeat": repeat + 1,
                        "mode": mode,
                        "elapsed_seconds": result.elapsed_seconds,
                        "unique_results": len(result.sources),
                        "outcomes": [
                            {
                                "source": outcome.source,
                                "status": outcome.status,
                                "results": len(outcome.sources),
                                "elapsed_seconds": outcome.elapsed_seconds,
                                "urls": [source.url for source in outcome.sources],
                            }
                            for outcome in result.outcomes
                        ],
                    }
                    report["runs"].append(record)
                    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                    statuses = ", ".join(
                        f"{o.source}={o.status}/{len(o.sources)}" for o in result.outcomes
                    )
                    print(
                        f"{sample['id']} {mode}: {result.elapsed_seconds:.3f}s; {statuses}",
                        flush=True,
                    )
    finally:
        await store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/live-benchmark-results.json")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    # Persist only safe result metadata; do not print raw provider errors or keys.
    logging.disable(logging.CRITICAL)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
