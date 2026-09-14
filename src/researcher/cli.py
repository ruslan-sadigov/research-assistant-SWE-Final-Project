"""Command-line interface for the research assistant."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import cast

from researcher.concurrency.orchestrator import SourceOrchestrator
from researcher.config import Settings, load_settings
from researcher.core.researcher import Researcher, render_result, sanitize_output, validate_question
from researcher.interfaces import CacheStoreProtocol
from researcher.models import SourceName
from researcher.services.ai_service import AIService
from researcher.services.cache import SourceCache
from researcher.storage.cache_store import InMemoryCacheStore, SqliteCacheStore

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
    if not requested:
        raise ValueError("Select at least one source.")
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


def create_cache_store(settings: Settings, *, use_cache: bool) -> CacheStoreProtocol:
    """Use persistent SQLite unless caching is explicitly bypassed."""
    if not use_cache:
        return InMemoryCacheStore()
    if settings.database_url is None:
        return SqliteCacheStore(Path.home() / ".cache" / "research-assistant" / "sources.sqlite3")
    url = settings.database_url.get_secret_value()
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("DATABASE_URL must use sqlite:/// followed by a file path.")
    path = url[len(prefix) :]
    if not path or path == ":memory:" or any(char in path for char in "?#\x00"):
        raise ValueError(
            "DATABASE_URL must identify a SQLite file without query or fragment options."
        )
    return SqliteCacheStore(Path(path).expanduser())


async def run_ask(
    settings: Settings, question: str, sources: list[SourceName], use_cache: bool
) -> int:
    try:
        store = create_cache_store(settings, use_cache=use_cache)
    except ValueError:
        print(
            "Invalid cache configuration; DATABASE_URL must be a SQLite file URL.", file=sys.stderr
        )
        return 2

    try:
        ai_service = AIService(settings)
        cache = SourceCache(store, settings.cache_ttl_seconds)
        orchestrator = SourceOrchestrator(settings, ai_service, cache)
        researcher = Researcher(orchestrator, ai_service)
        result = await researcher.research(question, sources, use_cache=use_cache)
    except Exception:
        print("Research failed; check configuration and provider availability.", file=sys.stderr)
        return 1
    finally:
        try:
            await store.close()
        except Exception:
            print("Warning: cache cleanup failed.", file=sys.stderr)

    print(render_result(result))
    return 0 if result.answer is not None else 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        settings = load_settings()
    except (ValueError, OSError):
        print(
            "Invalid configuration; check environment settings and the .env file.", file=sys.stderr
        )
        sys.exit(2)
    configure_logging(settings)

    if args.command == "ask":
        try:
            question = validate_question(args.question, settings.max_question_length)
            sources = parse_sources(args.sources)
        except ValueError as exc:
            print(sanitize_output(f"Invalid input: {exc}"), file=sys.stderr)
            sys.exit(2)

        exit_code = asyncio.run(run_ask(settings, question, sources, use_cache=not args.no_cache))
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
