import pytest

from researcher.cli import build_parser, parse_sources


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
