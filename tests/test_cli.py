import sys
from unittest.mock import AsyncMock

import pytest

from ai.schemas import AnswerWithCitations
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
    "arguments", [[" "], ["question", "--sources", ""], ["question", "--sources", "bad\x1b[2J"]]
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
