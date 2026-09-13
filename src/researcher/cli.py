"""Command-line interface for the research assistant."""

import argparse
import asyncio
import logging
import sys
from typing import cast

from researcher.concurrency.orchestrator import SourceOrchestrator
from researcher.config import Settings, load_settings
from researcher.core.researcher import Researcher, render_result, validate_question
from researcher.models import SourceName
from researcher.services.ai_service import AIService
from researcher.services.cache import SourceCache
from researcher.storage.cache_store import InMemoryCacheStore

VALID_SOURCES = ("wiki", "arxiv", "web")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="researcher")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask = subparsers.add_parser("ask", help="Ask a research question")
    ask.add_argument("question", type=str, help="The question to research")
    ask.add_argument(
        "--sources",
        type=str,
        default="wiki,arxiv,web",
        help="Comma-separated sources to use (wiki, arxiv, web)",
    )
    ask.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the cache for this request",
    )

    return parser


def parse_sources(raw: str) -> list[SourceName]:
    """Turn '--sources wiki,arxiv' into a validated, deduplicated list."""
    requested = [item.strip().lower() for item in raw.split(",") if item.strip()]
    unknown = [item for item in requested if item not in VALID_SOURCES]
    if unknown:
        raise ValueError(
            f"Unknown source(s): {', '.join(unknown)}. Valid: {', '.join(VALID_SOURCES)}"
        )
    deduplicated = list(dict.fromkeys(requested))
    return cast(list[SourceName], deduplicated)


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def run_ask(
    settings: Settings, question: str, sources: list[SourceName], use_cache: bool
) -> int:
    ai_service = AIService(settings)
    cache = SourceCache(InMemoryCacheStore(), settings.cache_ttl_seconds)
    orchestrator = SourceOrchestrator(settings, ai_service, cache)
    researcher = Researcher(orchestrator, ai_service)

    try:
        result = await researcher.research(question, sources, use_cache=use_cache)
    except Exception as exc:
        print(f"Research failed: {exc}", file=sys.stderr)
        return 1

    print(render_result(result))
    return 0 if result.answer is not None else 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    settings = load_settings()
    configure_logging(settings)

    if args.command == "ask":
        try:
            question = validate_question(args.question, settings.max_question_length)
            sources = parse_sources(args.sources)
        except ValueError as exc:
            print(f"Invalid input: {exc}", file=sys.stderr)
            sys.exit(2)

        exit_code = asyncio.run(run_ask(settings, question, sources, use_cache=not args.no_cache))
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
