"""Measure the source-cache hit rate through the real CLI, fully offline.

Run from the project root::

    uv run python tests/cache_verification.py
    uv run python tests/cache_verification.py --json
    uv run python tests/cache_verification.py --show-logs

Only the supplied ``ai.fetch_wikipedia``, ``ai.fetch_arxiv``, ``ai.fetch_web``
and ``ai.synthesize`` functions are replaced, the same provider boundary that
``tests/docker_smoke.py`` uses. ``researcher.cli.main`` then runs the real
settings loader, Researcher, SourceOrchestrator, AIService, SourceCache and
SqliteCacheStore. HTTP requests are rejected, every database is created in a
new temporary directory, and the process works from that directory, so a local
``.env`` file or personal cache is never read or changed.

Hit rate = cache hits / eligible cache lookups. A cache-enabled invocation makes
one lookup per selected source, and each lookup ends in one ``source completed``
INFO record whose ``cache_hit`` field is counted. A ``--no-cache`` invocation
makes no lookups, so it is reported separately instead of as a 0% run. Every
lookup that is not a hit must cause exactly one provider fetch; this is checked.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import platform
import re
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

import ai
from researcher import cli
from researcher.services.cache import normalize_query

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_FILE = ROOT / "data" / "research_questions.json"
SOURCES = ("wiki", "arxiv", "web")
ORIGINS = {"wiki": "wikipedia", "arxiv": "arxiv", "web": "web"}
FETCHERS = {"wiki": "fetch_wikipedia", "arxiv": "fetch_arxiv", "web": "fetch_web"}
# Pin every application setting (the .env.example defaults) so shell variables
# cannot change the measurement.
SETTINGS = {
    "LOG_LEVEL": "INFO",
    "MAX_QUESTION_LENGTH": "2000",
    "PER_SOURCE_TIMEOUT_SECONDS": "10",
    "MAX_SOURCES_PER_QUERY": "3",
    "RETRY_MAX_ATTEMPTS": "3",
    "RETRY_INITIAL_DELAY_SECONDS": "0.5",
    "RETRY_MAX_DELAY_SECONDS": "4",
    "CACHE_TTL_SECONDS": "86400",
}
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
COMPLETED = re.compile(r"source completed: source=(\w+) status=(\w+) cache_hit=(True|False) ")
CORRUPT_BYTES = b"not a SQLite database\n" * 8


class SimulatedProviders:
    """Offline replacements for the supplied fetchers and synthesizer."""

    def __init__(self, question_ids: dict[str, str]) -> None:
        self.question_ids = question_ids
        self.empty_sources: set[str] = set()
        self.fetches: Counter[str] = Counter()
        self.syntheses = 0

    def fetcher(self, source: str) -> Callable[..., Any]:
        async def fetch(query: str, *, max_results: int = 3, **_: Any) -> list[ai.Source]:
            self.fetches[source] += 1
            if source in self.empty_sources:
                return []
            question_id = self.question_ids.get(normalize_query(query), "other")
            evidence = ai.Source(
                title=f"Simulated {source} evidence for {question_id}",
                url=f"https://example.org/simulated/{question_id}/{source}",
                snippet=f"Offline {source} result for {question_id}.",
                origin=ORIGINS[source],
            )
            return [evidence][:max_results]

        return fetch

    def synthesize(
        self, question: str, sources: list[ai.Source], **_: Any
    ) -> ai.AnswerWithCitations:
        self.syntheses += 1
        markers = " ".join(f"[{index}]" for index in range(1, len(sources) + 1))
        return ai.AnswerWithCitations(
            question=question,
            answer=f"Simulated answer {markers}",
            citations=[
                ai.Citation(index=index, source=source) for index, source in enumerate(sources, 1)
            ],
        )


class LogCollector(logging.Handler):
    """Keep the application's log messages for counting."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("researcher"):
            self.messages.append(record.getMessage())


@dataclass
class PassResult:
    """Counts for one group of CLI invocations."""

    name: str
    description: str
    cache_enabled: bool
    exit_codes: list[int] = field(default_factory=list)
    lookups: int = 0
    hits: int = 0
    read_failures: int = 0
    write_failures: int = 0
    fetches: int = 0
    syntheses: int = 0
    warning_lines: int = 0
    outcomes: list[tuple[str, str, bool]] = field(default_factory=list)
    first_output: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "cache_enabled": self.cache_enabled,
            "invocations": len(self.exit_codes),
            "exit_codes": self.exit_codes,
            **rate(self.hits, self.lookups),
            "read_failures": self.read_failures,
            "write_failures": self.write_failures,
            "fetches": self.fetches,
            "syntheses": self.syntheses,
            "warning_lines": self.warning_lines,
            "outcomes": [
                {"source": source, "status": status, "cache_hit": hit}
                for source, status, hit in self.outcomes
            ],
        }


def rate(hits: int, lookups: int) -> dict[str, Any]:
    return {"hits": hits, "lookups": lookups, "hit_rate": hits / lookups if lookups else None}


@contextlib.contextmanager
def isolated_process(
    workdir: Path, providers: SimulatedProviders, collector: LogCollector, show_logs: bool
) -> Iterator[None]:
    """Pin settings, stay offline and capture logs; restore everything afterwards."""
    saved_env = {name: os.environ.get(name) for name in [*SETTINGS, "DATABASE_URL"]}
    saved_cwd = Path.cwd()
    saved_functions = {name: getattr(ai, name) for name in [*FETCHERS.values(), "synthesize"]}
    saved_sends = (httpx.Client.send, httpx.AsyncClient.send)
    root = logging.getLogger()
    saved_level = root.level
    handlers: list[logging.Handler] = [collector]
    if show_logs:
        echo = logging.StreamHandler(sys.stderr)
        echo.setFormatter(logging.Formatter(LOG_FORMAT))
        handlers.append(echo)

    def reject_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("cache verification must not use the network")

    os.environ.update(SETTINGS)
    os.chdir(workdir)  # load_settings() reads ./.env and this directory has none
    for source, name in FETCHERS.items():
        setattr(ai, name, providers.fetcher(source))
    setattr(ai, "synthesize", providers.synthesize)
    setattr(httpx.Client, "send", reject_network)
    setattr(httpx.AsyncClient, "send", reject_network)
    # cli.configure_logging() uses logging.basicConfig(), which leaves an
    # already configured root logger alone.
    root.setLevel(logging.INFO)
    for handler in handlers:
        root.addHandler(handler)
    try:
        yield
    finally:
        for handler in handlers:
            root.removeHandler(handler)
        root.setLevel(saved_level)
        setattr(httpx.Client, "send", saved_sends[0])
        setattr(httpx.AsyncClient, "send", saved_sends[1])
        for name, function in saved_functions.items():
            setattr(ai, name, function)
        os.chdir(saved_cwd)
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def use_database(path: Path) -> None:
    os.environ["DATABASE_URL"] = "sqlite:///" + path.as_posix()


def run_cli(question: str, *, no_cache: bool) -> tuple[int, str]:
    """Run ``researcher ask`` in this process; return the exit code and stdout."""
    stdout = io.StringIO()
    saved_argv = sys.argv
    sys.argv = ["researcher", "ask", question, "--sources", ",".join(SOURCES)]
    if no_cache:
        sys.argv.append("--no-cache")
    try:
        with contextlib.redirect_stdout(stdout):
            cli.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    else:
        raise AssertionError("the CLI returned without calling sys.exit()")
    finally:
        sys.argv = saved_argv
    return code, stdout.getvalue()


def run_pass(
    name: str,
    description: str,
    questions: list[str],
    providers: SimulatedProviders,
    collector: LogCollector,
    *,
    no_cache: bool = False,
) -> PassResult:
    result = PassResult(name, description, cache_enabled=not no_cache)
    fetches_before = sum(providers.fetches.values())
    syntheses_before = providers.syntheses
    first_message = len(collector.messages)
    for question in questions:
        code, output = run_cli(question, no_cache=no_cache)
        result.exit_codes.append(code)
        result.warning_lines += sum(line.startswith("  - ") for line in output.splitlines())
        result.first_output = result.first_output or output
    for message in collector.messages[first_message:]:
        match = COMPLETED.search(message)
        if match:
            result.outcomes.append((match[1], match[2], match[3] == "True"))
        result.read_failures += message.startswith("cache read failed:")
        result.write_failures += message.startswith("cache write failed:")
    if result.cache_enabled:
        result.lookups = len(result.outcomes)
        result.hits = sum(hit for _, _, hit in result.outcomes)
    result.fetches = sum(providers.fetches.values()) - fetches_before
    result.syntheses = providers.syntheses - syntheses_before
    return result


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count_rows(path: Path) -> int:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()
    return int(count)


def describe_schema(path: Path, sample_key: tuple[str, str]) -> dict[str, Any]:
    """Read the layout back from SQLite rather than restating the source code."""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        objects = conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type DESC, name"
        ).fetchall()
        columns = conn.execute("PRAGMA table_info(cache_entries)").fetchall()
        indexes = conn.execute("PRAGMA index_list(cache_entries)").fetchall()
        indexed = {
            row[1]: [info[2] for info in conn.execute(f"PRAGMA index_info('{row[1]}')")]
            for row in indexes
        }
        sample = conn.execute(
            "SELECT payload FROM cache_entries WHERE source = ? AND query_key = ?", sample_key
        ).fetchone()
        return {
            "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
            "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
            "objects": [{"type": kind, "name": name, "sql": sql} for kind, name, sql in objects],
            "columns": [
                {"name": c[1], "type": c[2], "not_null": bool(c[3]), "primary_key": c[5]}
                for c in columns
            ],
            "indexes": [
                {"name": i[1], "unique": bool(i[2]), "origin": i[3], "columns": indexed[i[1]]}
                for i in indexes
            ],
            "foreign_keys": conn.execute("PRAGMA foreign_key_list(cache_entries)").fetchall(),
            "sample_payload": json.loads(sample[0]) if sample else None,
        }


def environment() -> dict[str, str]:
    def version(package: str) -> str:
        try:
            return metadata.version(package)
        except metadata.PackageNotFoundError:
            return "not installed"

    try:
        commit = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "research-assistant": version("research-assistant"),
        "pydantic": version("pydantic"),
        "httpx": version("httpx"),
        "git": commit,
    }


def reformat(question: str) -> str:
    """Keep the words; change case, spacing and trailing punctuation."""
    return "  " + "   ".join(question.rstrip("?").split()).upper() + " ?!  "


def run_verification(workdir: Path, *, show_logs: bool = False) -> dict[str, Any]:
    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))["questions"]
    texts = [question["text"] for question in questions]
    providers = SimulatedProviders(
        {normalize_query(question["text"]): question["id"] for question in questions}
    )
    collector = LogCollector()
    per_pass = len(texts) * len(SOURCES)
    main_db = workdir / "sources.sqlite3"
    corrupt_db = workdir / "corrupt.sqlite3"
    corrupt_db.write_bytes(CORRUPT_BYTES)

    with isolated_process(workdir, providers, collector, show_logs):

        def run(*args: Any, **kwargs: Any) -> PassResult:
            return run_pass(*args, providers=providers, collector=collector, **kwargs)

        use_database(main_db)
        cold = run("cold", "first run, empty cache", texts)
        rows_after_cold = count_rows(main_db)
        warm = run("warm", "same questions again", texts)
        reformatted = run(
            "reformatted", "same words, other case/spacing/punctuation", map_list(reformat, texts)
        )
        before_bypass = file_digest(main_db)
        bypass = run("no-cache", "same questions with --no-cache", texts, no_cache=True)
        unchanged_by_bypass = file_digest(main_db) == before_bypass
        rows_final = count_rows(main_db)
        schema = describe_schema(main_db, ("wiki", normalize_query(texts[0])))

        use_database(corrupt_db)
        corrupt = run("corrupt-file", "cache file is not a database", texts[:1])
        corrupt_untouched = corrupt_db.read_bytes() == CORRUPT_BYTES

        use_database(workdir / "empty-wiki.sqlite3")
        providers.empty_sources = {"wiki"}
        empty_first = run("empty-wiki-1", "Wikipedia finds nothing", texts[:1])
        empty_second = run("empty-wiki-2", "same question again", texts[:1])
        providers.empty_sources = set()

    cached = [cold, warm, reformatted, corrupt, empty_first, empty_second]
    everything = [*cached, bypass]
    checks = [
        ("cold run: every lookup is a miss", cold.lookups == per_pass and cold.hits == 0),
        (
            "warm run: every lookup is a hit and nothing is fetched",
            warm.lookups == warm.hits == per_pass and warm.fetches == 0,
        ),
        (
            "reformatted questions reuse the same entries",
            reformatted.hits == per_pass and reformatted.fetches == 0,
        ),
        ("one SQLite row per (source, question)", rows_after_cold == rows_final == per_pass),
        (
            "every lookup that is not a hit causes exactly one fetch",
            all(p.fetches == p.lookups - p.hits for p in cached),
        ),
        (
            "every invocation exits with 0 and synthesizes an answer",
            all(set(p.exit_codes) == {0} and p.syntheses == len(p.exit_codes) for p in everything),
        ),
        (
            "--no-cache: no lookups, every source fetched, SQLite file unchanged",
            bypass.lookups == 0 and bypass.fetches == per_pass and unchanged_by_bypass,
        ),
        (
            "corrupt file: read and write fail per source, answer kept, file untouched",
            corrupt.read_failures == corrupt.write_failures == len(SOURCES)
            and corrupt.hits == 0
            and corrupt.warning_lines == len(SOURCES)
            and corrupt_untouched,
        ),
        (
            "empty Wikipedia result is stored and served as a hit",
            ("wiki", "empty", False) in empty_first.outcomes
            and ("wiki", "empty", True) in empty_second.outcomes
            and empty_second.fetches == 0,
        ),
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": environment(),
        "workload": {
            "questions": [question["id"] for question in questions],
            "sources": list(SOURCES),
            "lookups_per_pass": per_pass,
            "settings": SETTINGS,
            "providers": "simulated: ai.fetch_* and ai.synthesize replaced, HTTP blocked",
        },
        "headline": {
            "cold": rate(cold.hits, cold.lookups),
            "warm": rate(warm.hits, warm.lookups),
            "combined": rate(cold.hits + warm.hits, cold.lookups + warm.lookups),
        },
        "passes": [p.as_dict() for p in everything[:3] + [bypass] + everything[3:6]],
        "database": {
            "rows_after_cold": rows_after_cold,
            "rows_final": rows_final,
            "unchanged_by_no_cache": unchanged_by_bypass,
            "corrupt_file_untouched": corrupt_untouched,
        },
        "schema": schema,
        "sample_output": cold.first_output,
        "checks": [{"check": text, "passed": bool(ok)} for text, ok in checks],
        "passed": all(ok for _, ok in checks),
    }


def map_list(function: Callable[[str], str], values: list[str]) -> list[str]:
    return [function(value) for value in values]


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def print_report(report: dict[str, Any]) -> None:
    workload = report["workload"]
    print("Offline cache hit-rate verification (simulated providers, no network)")
    print(f"Generated: {report['generated_at']}")
    print("Environment: " + ", ".join(f"{k}={v}" for k, v in report["environment"].items()))
    print(
        f"Workload: {len(workload['questions'])} supplied questions x "
        f"{','.join(workload['sources'])}, one CLI invocation per question; "
        f"CACHE_TTL_SECONDS={workload['settings']['CACHE_TTL_SECONDS']}; new temporary SQLite"
    )
    print()
    print("Hit rate = hits / eligible lookups")
    for name, value in report["headline"].items():
        print(
            f"  {name:<9}{value['hits']:>3} / {value['lookups']:<3} = "
            f"{percent(value['hit_rate'])}"
        )
    print()
    header = (
        f"{'pass':<14}{'runs':>5}{'lookups':>9}{'hits':>6}{'hit rate':>10}"
        f"{'fetches':>9}{'answers':>9}{'warning lines':>15}"
    )
    print(header)
    print("-" * len(header))
    for p in report["passes"]:
        lookups = p["lookups"] if p["cache_enabled"] else "-"
        hits = p["hits"] if p["cache_enabled"] else "-"
        print(
            f"{p['name']:<14}{p['invocations']:>5}{lookups!s:>9}{hits!s:>6}"
            f"{percent(p['hit_rate']):>10}{p['fetches']:>9}{p['syntheses']:>9}"
            f"{p['warning_lines']:>15}"
        )
    database = report["database"]
    print()
    print(
        f"SQLite rows after the cold run: {database['rows_after_cold']}; "
        f"after all passes: {database['rows_final']}"
    )
    schema = report["schema"]
    print(
        f"Schema: user_version={schema['user_version']}, "
        f"journal_mode={schema['journal_mode']}, foreign keys={len(schema['foreign_keys'])}"
    )
    for item in schema["objects"]:
        print(f"  {item['type']} {item['name']}")
        print(textwrap.indent(item["sql"] or "(created automatically for the primary key)", "    "))
    for index in schema["indexes"]:
        print(
            f"  index {index['name']}: unique={index['unique']}, origin={index['origin']}, "
            f"columns={','.join(index['columns'])}"
        )
    print("  sample payload (wiki, q1):")
    print(textwrap.indent(json.dumps(schema["sample_payload"], indent=2), "    "))
    print()
    print("CLI output of the first cold invocation:")
    print(textwrap.indent(report["sample_output"].rstrip(), "    "))
    print()
    for item in report["checks"]:
        print(f"[{'PASS' if item['passed'] else 'FAIL'}] {item['check']}")
    print()
    print("RESULT: " + ("all checks passed" if report["passed"] else "some checks FAILED"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure cache hit rates through the real CLI with simulated providers."
    )
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument("--show-logs", action="store_true", help="echo application logs")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="cache-verification-", ignore_cleanup_errors=True
    ) as workdir:
        report = run_verification(Path(workdir), show_logs=args.show_logs)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
