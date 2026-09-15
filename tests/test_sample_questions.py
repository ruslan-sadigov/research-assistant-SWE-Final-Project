"""Exercise the complete CLI flow with supplied questions and offline providers."""

import json
import sys
from pathlib import Path

import httpx
import pytest

import ai
from researcher import cli
from researcher.config import Settings

SAMPLES = json.loads(
    (Path(__file__).resolve().parents[1] / "data" / "research_questions.json").read_text(
        encoding="utf-8"
    )
)["questions"]
SOURCE_NAMES = {"wikipedia": "wiki", "arxiv": "arxiv", "web": "web"}


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample["id"])
@pytest.mark.parametrize("selection", ["all", "expected"])
def test_sample_question_cli(monkeypatch, tmp_path, capsys, sample, selection):
    question = sample["text"]
    selected = (
        ["wiki", "arxiv", "web"]
        if selection == "all"
        else [SOURCE_NAMES[name] for name in sample["expected_sources"]]
    )
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'sources.sqlite3').as_posix()}")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def reject_network(*args, **kwargs):
        raise AssertionError("Sample-question smoke tests must remain offline.")

    monkeypatch.setattr(httpx.AsyncClient, "send", reject_network)
    monkeypatch.setattr(httpx.Client, "send", reject_network)
    fetches = []
    syntheses = []

    def make_fetcher(name):
        async def fetch(query, **kwargs):
            assert query == question
            fetches.append(name)
            return [
                ai.Source(
                    title=f"Offline {name} evidence for {sample['id']}",
                    url=f"https://example.com/{sample['id']}/{name}",
                    snippet=f"Simulated evidence for {question}",
                    origin="wikipedia" if name == "wiki" else name,
                )
            ]

        return fetch

    for name, function in (
        ("wiki", "fetch_wikipedia"),
        ("arxiv", "fetch_arxiv"),
        ("web", "fetch_web"),
    ):
        monkeypatch.setattr(ai, function, make_fetcher(name))

    def synthesize(query, sources):
        assert query == question
        assert [SOURCE_NAMES[source.origin] for source in sources] == selected
        syntheses.append(query)
        return ai.AnswerWithCitations(
            question=query,
            answer="Offline answer " + " ".join(f"[{i}]" for i in range(1, len(sources) + 1)),
            citations=[ai.Citation(index=i, source=source) for i, source in enumerate(sources, 1)],
        )

    monkeypatch.setattr(ai, "synthesize", synthesize)
    # A new CLI invocation creates and closes its own SQLite store each time.
    # Check initial retrieval, persistent cache reuse, and explicit bypass.
    for invocation, bypass in enumerate((False, False, True), 1):
        arguments = ["researcher", "ask", question, "--sources", ",".join(selected)]
        if bypass:
            arguments.append("--no-cache")
        monkeypatch.setattr(sys, "argv", arguments)
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
        output = capsys.readouterr()
        assert f"Q: {question}" in output.out
        assert "A: Offline answer" in output.out
        assert "References:" in output.out
        assert "Warnings:" not in output.out
        assert output.err == ""
        for i, name in enumerate(selected, 1):
            assert f"[{i}]" in output.out
            assert f"https://example.com/{sample['id']}/{name}" in output.out
        assert len(syntheses) == invocation
        for name in selected:
            assert fetches.count(name) == (2 if bypass else 1)
        assert set(fetches) == set(selected)
