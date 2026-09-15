"""Compare sequential and concurrent collection using simulated source delays."""

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from statistics import median
from time import perf_counter

import httpx

from ai import AnswerWithCitations, Source
from researcher.concurrency.orchestrator import SourceOrchestrator
from researcher.config import Settings
from researcher.models import SourceName

QUESTIONS_PATH = Path(__file__).resolve().parents[1] / "data" / "research_questions.json"
DELAYS: dict[SourceName, float] = {
    "wiki": 0.1,
    "arxiv": 0.2,
    "web": 0.3,
}


class DisabledCache:
    """Fail if either benchmark accidentally accesses the cache."""

    async def get_sources(self, source: SourceName, query: str) -> list[Source] | None:
        raise AssertionError("Benchmark caching must remain disabled.")

    async def set_sources(
        self,
        source: SourceName,
        query: str,
        sources: list[Source],
    ) -> None:
        raise AssertionError("Benchmark caching must remain disabled.")


class SimulatedAIService:
    """Return fixed evidence after an asynchronous delay; never access the network."""

    @asynccontextmanager
    async def open_source_client(self) -> AsyncIterator[httpx.AsyncClient]:
        def reject_network(request: httpx.Request) -> httpx.Response:
            raise AssertionError("This benchmark must not access the network.")

        async with httpx.AsyncClient(transport=httpx.MockTransport(reject_network)) as client:
            yield client

    async def fetch_sources(
        self,
        source: SourceName,
        query: str,
        *,
        client: httpx.AsyncClient,
    ) -> list[Source]:
        await asyncio.sleep(DELAYS[source])
        return [
            Source(
                title=f"Simulated {source} evidence",
                url=f"https://example.com/{source}",
                snippet=f"Offline evidence for: {query}",
                origin="wikipedia" if source == "wiki" else source,
            )
        ]

    async def synthesize_answer(
        self,
        question: str,
        sources: list[Source],
    ) -> AnswerWithCitations:
        raise AssertionError("Synthesis is outside this benchmark.")


async def run_sequential(
    service: SimulatedAIService,
    settings: Settings,
    question: str,
) -> list[Source]:
    """Fetch one source at a time, sharing a client and applying source deadlines."""
    combined: list[Source] = []
    seen_urls: set[str] = set()

    async with service.open_source_client() as client:
        for name in DELAYS:
            async with asyncio.timeout(settings.per_source_timeout_seconds):
                sources = await service.fetch_sources(name, question, client=client)

            for source in sources:
                if source.url not in seen_urls:
                    seen_urls.add(source.url)
                    combined.append(source)

    return combined


async def run_question(question: str, repeats: int) -> tuple[float, float]:
    service = SimulatedAIService()
    settings = Settings(per_source_timeout_seconds=5)
    orchestrator = SourceOrchestrator(settings, service, DisabledCache())

    async def run_parallel() -> list[Source]:
        result = await orchestrator.collect_sources(
            question,
            list(DELAYS),
            use_cache=False,
        )
        if any(outcome.status != "ok" or outcome.warning for outcome in result.outcomes):
            raise RuntimeError("Parallel collection did not complete successfully.")
        return result.sources

    # Warm up both paths; exclude these runs from the reported measurements.
    sequential_evidence = await run_sequential(service, settings, question)
    parallel_evidence = await run_parallel()
    if sequential_evidence != parallel_evidence:
        raise RuntimeError("Sequential and parallel evidence differ.")

    sequential_times: list[float] = []
    parallel_times: list[float] = []

    for index in range(repeats):
        # Alternate order to reduce systematic first-run/second-run bias.
        order = ("sequential", "parallel") if index % 2 == 0 else ("parallel", "sequential")
        evidence_by_mode: dict[str, list[Source]] = {}

        for mode in order:
            started = perf_counter()

            if mode == "sequential":
                evidence_by_mode[mode] = await run_sequential(service, settings, question)
                sequential_times.append(perf_counter() - started)
            else:
                evidence_by_mode[mode] = await run_parallel()
                parallel_times.append(perf_counter() - started)

        if evidence_by_mode["sequential"] != evidence_by_mode["parallel"]:
            raise RuntimeError("Sequential and parallel evidence differ.")

    sequential_median = median(sequential_times)
    parallel_median = median(parallel_times)

    print(f"Question: {question}")
    print("Simulated delays: wiki=0.1s, arxiv=0.2s, web=0.3s")
    print("Caching disabled; no network calls or synthesis.")
    print(f"Measured runs per mode: {repeats}")
    print()
    print("| Mode | Minimum (s) | Median (s) | Maximum (s) |")
    print("|---|---:|---:|---:|")

    for label, samples in (
        ("Sequential", sequential_times),
        ("Parallel", parallel_times),
    ):
        print(f"| {label} | {min(samples):.4f} | " f"{median(samples):.4f} | {max(samples):.4f} |")

    print()
    print(f"Median speedup: {sequential_median / parallel_median:.2f}x")
    print("Evidence matched in every comparison.")
    return sequential_median, parallel_median


async def run_benchmark(repeats: int) -> None:
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]
    if not questions:
        raise ValueError("The sample question dataset must not be empty.")
    print("Offline source-collection benchmark: supplied sample questions")
    print("All three sources are measured for every question, regardless of expected_sources tags.")
    results = []
    for sample in questions:
        print(f"\nSample {sample['id']}")
        sequential, parallel = await run_question(sample["text"], repeats)
        results.append((sample["id"], sequential, parallel))
    print("\n| Sample | Sequential median (s) | Parallel median (s) | Speedup |")
    print("|---|---:|---:|---:|")
    for sample_id, sequential, parallel in results:
        print(f"| {sample_id} | {sequential:.4f} | {parallel:.4f} | {sequential / parallel:.2f}x |")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeats",
        type=int,
        default=5,
        help="Measured runs per mode, excluding warm-up (default: 5).",
    )
    args = parser.parse_args()

    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    asyncio.run(run_benchmark(args.repeats))


if __name__ == "__main__":
    main()
