"""Run the installed CLI offline in separate containers sharing a cache volume."""

import sys

import ai
from researcher.cli import main


def run() -> None:
    mode = sys.argv[1]
    if mode not in {"populate", "reuse"}:
        raise ValueError("Expected populate or reuse")
    fetches = []
    syntheses = []

    async def fetch(query, *, max_results=3, client=None):
        fetches.append(query)
        return [
            ai.Source(
                title="Offline evidence",
                url="https://example.com/docker-smoke",
                snippet="Evidence for the Docker integration test.",
                origin="web",
            )
        ]

    def synthesize(question, sources):
        syntheses.append(question)
        assert sources[0].url == "https://example.com/docker-smoke"
        return ai.AnswerWithCitations(
            question=question,
            answer="Offline integration succeeded [1].",
            citations=[ai.Citation(index=1, source=sources[0])],
        )

    # Replace only the provider boundary; run real CLI, service, orchestrator,
    # result rendering, and SQLite storage from the installed runtime package.
    ai.fetch_web = fetch
    ai.synthesize = synthesize
    sys.argv = ["researcher", "ask", "Docker cache persistence check", "--sources", "web"]
    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0, f"CLI exit code: {exc.code}"
    else:
        raise AssertionError("CLI did not exit")
    assert len(fetches) == (1 if mode == "populate" else 0), fetches
    assert len(syntheses) == 1, syntheses
    print(f"Docker CLI smoke passed: {mode}")


if __name__ == "__main__":
    run()
