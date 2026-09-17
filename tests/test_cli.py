import asyncio
import io
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from ai.schemas import AnswerWithCitations, Source
from researcher import cli
from researcher.cli import build_parser, parse_sources
from researcher.config import Settings
from researcher.models import CollectionResult, ResearchResult


def test_parse_sources_splits_and_validates():
    assert parse_sources("wiki,arxiv") == ["wiki", "arxiv"]


def test_parse_sources_deduplicates_preserving_order():
    assert parse_sources("wiki,wiki,web") == ["wiki", "web"]


def test_parse_sources_normalizes_case_and_whitespace():
    assert parse_sources(" WIKI , Arxiv ") == ["wiki", "arxiv"]


def test_parse_sources_rejects_unknown_source():
    with pytest.raises(ValueError):
        parse_sources("wiki,bogus")


def test_build_parser_parses_ask_command():
    parser = build_parser()
    args = parser.parse_args(["ask", "What is photosynthesis?", "--sources", "web", "--no-cache"])

    assert args.command == "ask"
    assert args.question == "What is photosynthesis?"
    assert args.sources == "web"
    assert args.no_cache is True


def test_build_parser_defaults_sources_and_cache():
    parser = build_parser()
    args = parser.parse_args(["ask", "A question"])

    assert args.sources == "wiki,arxiv,web"
    assert args.no_cache is False


@pytest.mark.parametrize("raw", ["", " ", ", ,"])
def test_parse_sources_rejects_empty_selection(raw):
    with pytest.raises(ValueError, match="at least one"):
        parse_sources(raw)


@pytest.mark.parametrize("code", [0, 1])
def test_main_forwards_arguments_and_exit_code(monkeypatch, code):
    settings = Settings(_env_file=None)
    run = AsyncMock(return_value=code)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "run_ask", run)
    monkeypatch.setattr(
        sys, "argv", ["researcher", "ask", " question ", "--sources", "wiki,web", "--no-cache"]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == code
    run.assert_awaited_once_with(settings, "question", ["wiki", "web"], use_cache=False)


@pytest.mark.parametrize(
    "arguments",
    [[" "], ["???"], ["question", "--sources", ""], ["question", "--sources", "bad\x1b[2J"]],
)
def test_main_invalid_input_does_not_run_research(monkeypatch, capsys, arguments):
    run = AsyncMock()
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(cli, "run_ask", run)
    monkeypatch.setattr(sys, "argv", ["researcher", "ask", *arguments])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    run.assert_not_awaited()
    output = capsys.readouterr()
    assert not output.out
    assert "Invalid input" in output.err
    assert "\x1b" not in output.err


def test_main_escapes_characters_the_output_encoding_cannot_represent(monkeypatch):
    # Windows uses the ANSI code page when output is piped or redirected.
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1251", newline="\n")

    async def run(*args, **kwargs):
        print("Fotosintez n\u0259dir \u2192 \u5149\u5408\u4f5c\u7528")
        return 0

    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(cli, "run_ask", run)
    monkeypatch.setattr(sys, "argv", ["researcher", "ask", "question"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    stdout.flush()
    assert stdout.buffer.getvalue().decode("cp1251") == (
        "Fotosintez n\\u0259dir \\u2192 \\u5149\\u5408\\u4f5c\\u7528\n"
    )


def test_main_invalid_settings_has_no_traceback_or_raw_error(monkeypatch, capsys):
    def fail():
        raise ValueError("private-value")

    monkeypatch.setattr(cli, "load_settings", fail)
    monkeypatch.setattr(sys, "argv", ["researcher", "ask", "question"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "Invalid configuration" in output.err
    assert "private-value" not in output.err
    assert not output.out


@pytest.mark.asyncio
@pytest.mark.parametrize("has_answer", [True, False])
async def test_run_ask_renders_result_and_sets_exit_code(monkeypatch, capsys, has_answer):
    result = ResearchResult(
        question="question",
        collection=CollectionResult(elapsed_seconds=0),
        answer=(
            AnswerWithCitations(question="question", answer="Answer", citations=[])
            if has_answer
            else None
        ),
    )
    run = AsyncMock(return_value=result)
    monkeypatch.setattr(cli.Researcher, "research", run)
    code = await cli.run_ask(Settings(_env_file=None), "question", ["wiki"], False)
    assert code == (0 if has_answer else 1)
    run.assert_awaited_once_with("question", ["wiki"], use_cache=False)
    output = capsys.readouterr()
    assert "Q: question" in output.out
    assert not output.err


@pytest.mark.asyncio
async def test_run_ask_hides_unexpected_error_details(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.Researcher, "research", AsyncMock(side_effect=RuntimeError("private-value"))
    )
    assert await cli.run_ask(Settings(_env_file=None), "question", ["wiki"], True) == 1
    output = capsys.readouterr()
    assert "Research failed" in output.err
    assert "private-value" not in output.err
    assert not output.out


class OfflineAIService:
    fetch_count = 0
    synthesis_count = 0

    def __init__(self, settings):
        pass

    @asynccontextmanager
    async def open_source_client(self):
        yield None

    async def fetch_sources(self, source, query, *, client):
        type(self).fetch_count += 1
        return [
            Source(title="Evidence", url="https://example.com", snippet="Evidence", origin="web")
        ]

    async def synthesize_answer(self, question, sources):
        type(self).synthesis_count += 1
        return AnswerWithCitations(question=question, answer="Answer", citations=[])


@pytest.fixture
def offline_cli(monkeypatch):
    OfflineAIService.fetch_count = 0
    OfflineAIService.synthesis_count = 0
    monkeypatch.setattr(cli, "AIService", OfflineAIService)


def test_cli_reuses_sqlite_between_separate_event_loops(tmp_path, offline_cli):
    path = tmp_path / "sources.sqlite3"
    settings = Settings(database_url="sqlite:///" + path.as_posix())
    for _ in range(2):
        assert asyncio.run(cli.run_ask(settings, "question", ["web"], True)) == 0
    assert path.exists()
    assert OfflineAIService.fetch_count == 1
    assert OfflineAIService.synthesis_count == 2
    assert asyncio.run(cli.run_ask(settings, "question", ["web"], False)) == 0
    assert OfflineAIService.fetch_count == 2
    assert asyncio.run(cli.run_ask(settings, "question", ["web"], True)) == 0
    assert OfflineAIService.fetch_count == 2


def test_cli_refetches_after_fetch_settings_change(tmp_path, monkeypatch, offline_cli):
    url = "sqlite:///" + (tmp_path / "sources.sqlite3").as_posix()
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")
    runs = [
        (Settings(database_url=url, max_sources_per_query=3), 1),
        (Settings(database_url=url, max_sources_per_query=3), 1),
        (Settings(database_url=url, max_sources_per_query=5), 2),
    ]
    for settings, expected_fetches in runs:
        assert asyncio.run(cli.run_ask(settings, "question", ["web"], True)) == 0
        assert OfflineAIService.fetch_count == expected_fetches
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "serper")
    assert asyncio.run(cli.run_ask(runs[-1][0], "question", ["web"], True)) == 0
    assert OfflineAIService.fetch_count == 3


@pytest.mark.asyncio
async def test_no_cache_does_not_create_database(tmp_path, offline_cli):
    path = tmp_path / "unused.sqlite3"
    settings = Settings(database_url="sqlite:///" + path.as_posix())
    assert await cli.run_ask(settings, "question", ["web"], False) == 0
    assert not path.exists()


@pytest.mark.asyncio
async def test_default_cache_uses_home_directory(tmp_path, monkeypatch, offline_cli):
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    assert await cli.run_ask(Settings(database_url=None), "question", ["web"], True) == 0
    assert (tmp_path / ".cache/research-assistant/sources.sqlite3").exists()


@pytest.mark.asyncio
async def test_relative_cache_url(tmp_path, monkeypatch, offline_cli):
    monkeypatch.chdir(tmp_path)
    settings = Settings(database_url="sqlite:///cache/sources.sqlite3")
    assert await cli.run_ask(settings, "question", ["web"], True) == 0
    assert (tmp_path / "cache/sources.sqlite3").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "postgresql://secret@host/db",
        "sqlite:///",
        "sqlite:///:memory:",
        "sqlite:///file?mode=ro",
        "sqlite:///file#fragment",
    ],
)
async def test_invalid_database_url_fails_without_exposing_value(url, capsys):
    assert await cli.run_ask(Settings(database_url=url), "question", ["web"], True) == 2
    output = capsys.readouterr()
    assert "Invalid cache configuration" in output.err
    assert url not in output.err
    assert not output.out


@pytest.mark.asyncio
async def test_unwritable_cache_preserves_answer(tmp_path, offline_cli, capsys):
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied")
    settings = Settings(database_url="sqlite:///" + (parent / "db.sqlite3").as_posix())
    assert await cli.run_ask(settings, "question", ["web"], True) == 0
    assert "Answer" in capsys.readouterr().out
    assert OfflineAIService.fetch_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, RuntimeError("private-value"), asyncio.CancelledError()])
async def test_cache_closed_after_success_error_or_cancellation(monkeypatch, failure):
    store = AsyncMock()
    monkeypatch.setattr(cli, "create_cache_store", lambda settings, **kwargs: store)
    result = ResearchResult(question="question", collection=CollectionResult(elapsed_seconds=0))
    monkeypatch.setattr(
        cli.Researcher, "research", AsyncMock(return_value=result, side_effect=failure)
    )
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await cli.run_ask(Settings(), "question", ["web"], True)
    else:
        assert await cli.run_ask(Settings(), "question", ["web"], True) == 1
    store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_failure_does_not_discard_answer(monkeypatch, capsys):
    store = AsyncMock()
    store.close.side_effect = RuntimeError("private-value")
    monkeypatch.setattr(cli, "create_cache_store", lambda settings, **kwargs: store)
    result = ResearchResult(
        question="question",
        collection=CollectionResult(elapsed_seconds=0),
        answer=AnswerWithCitations(question="question", answer="Answer", citations=[]),
    )
    monkeypatch.setattr(cli.Researcher, "research", AsyncMock(return_value=result))
    assert await cli.run_ask(Settings(), "question", ["web"], True) == 0
    output = capsys.readouterr()
    assert "Answer" in output.out
    assert "cache cleanup failed" in output.err
    assert "private-value" not in output.err
