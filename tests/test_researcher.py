import pytest

from ai.schemas import AnswerWithCitations, Citation, Source
from researcher.core.researcher import Researcher, render_result, validate_question
from researcher.models import CollectionResult, ResearchResult, SourceOutcome


def test_validate_question_strips_whitespace():
    result = validate_question("  What is photosynthesis?  ", max_length=100)
    assert result == "What is photosynthesis?"


def test_validate_question_rejects_empty_string():
    with pytest.raises(ValueError):
        validate_question("", max_length=100)


def test_validate_question_rejects_whitespace_only():
    with pytest.raises(ValueError):
        validate_question("     ", max_length=100)


@pytest.mark.parametrize("raw", ["???", " . ! ", "\uff1f"])
def test_validate_question_rejects_question_without_letters_or_digits(raw):
    with pytest.raises(ValueError, match="letter or digit"):
        validate_question(raw, max_length=100)


@pytest.mark.parametrize("raw", ["C++?", "42", "Fotosintez n\u0259dir?"])
def test_validate_question_accepts_any_letter_or_digit(raw):
    assert validate_question(raw, max_length=100) == raw


def test_validate_question_rejects_too_long():
    with pytest.raises(ValueError):
        validate_question("a" * 101, max_length=100)


def test_validate_question_accepts_exact_max_length():
    question = "a" * 100
    result = validate_question(question, max_length=100)
    assert result == question


def _make_source(origin: str = "wikipedia") -> Source:
    return Source(
        title="Photosynthesis",
        url="https://en.wikipedia.org/wiki/Photosynthesis",
        snippet="A biological process",
        origin=origin,
    )


def test_render_result_with_answer_and_citations():
    source = _make_source()
    result = ResearchResult(
        question="What is photosynthesis?",
        answer=AnswerWithCitations(
            question="What is photosynthesis?",
            answer="Photosynthesis converts light into energy [1].",
            citations=[Citation(index=1, source=source)],
        ),
        collection=CollectionResult(
            outcomes=[
                SourceOutcome(
                    source="wiki",
                    status="ok",
                    sources=[source],
                    elapsed_seconds=0.5,
                )
            ],
            sources=[source],
            elapsed_seconds=0.5,
        ),
    )

    text = render_result(result)

    assert "Q: What is photosynthesis?" in text
    assert "Photosynthesis converts light into energy [1]." in text
    assert "[1] (wikipedia) Photosynthesis" in text
    assert "https://en.wikipedia.org/wiki/Photosynthesis" in text


def test_render_result_with_no_answer():
    result = ResearchResult(
        question="What is photosynthesis?",
        answer=None,
        collection=CollectionResult(outcomes=[], sources=[], elapsed_seconds=0.0),
        warnings=["No sources were available."],
    )

    text = render_result(result)

    assert "No answer could be produced" in text
    assert "No sources were available." in text


class FakeOrchestrator:
    def __init__(self, collection: CollectionResult) -> None:
        self._collection = collection

    async def collect_sources(self, question, selected_sources, *, use_cache=True):
        return self._collection


class FakeAIService:
    def __init__(self, answer: AnswerWithCitations | None = None, should_fail: bool = False):
        self._answer = answer
        self._should_fail = should_fail

    async def synthesize_answer(self, question, sources):
        if self._should_fail:
            raise RuntimeError("synthesis unavailable")
        return self._answer


@pytest.mark.asyncio
async def test_research_returns_answer_when_sources_found():
    source = _make_source()
    collection = CollectionResult(
        outcomes=[SourceOutcome(source="wiki", status="ok", sources=[source], elapsed_seconds=0.1)],
        sources=[source],
        elapsed_seconds=0.1,
    )
    answer = AnswerWithCitations(
        question="What is photosynthesis?",
        answer="It converts light into energy [1].",
        citations=[Citation(index=1, source=source)],
    )
    researcher = Researcher(FakeOrchestrator(collection), FakeAIService(answer=answer))

    result = await researcher.research("What is photosynthesis?", ["wiki"])

    assert result.answer is not None
    assert result.answer.answer == "It converts light into energy [1]."


@pytest.mark.asyncio
async def test_research_returns_none_when_no_sources():
    collection = CollectionResult(outcomes=[], sources=[], elapsed_seconds=0.0)
    researcher = Researcher(FakeOrchestrator(collection), FakeAIService())

    result = await researcher.research("Obscure question", ["wiki"])

    assert result.answer is None
    assert any("No sources" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_research_handles_synthesis_failure():
    source = _make_source()
    collection = CollectionResult(
        outcomes=[SourceOutcome(source="wiki", status="ok", sources=[source], elapsed_seconds=0.1)],
        sources=[source],
        elapsed_seconds=0.1,
    )
    researcher = Researcher(FakeOrchestrator(collection), FakeAIService(should_fail=True))

    result = await researcher.research("What is photosynthesis?", ["wiki"])

    assert result.answer is None
    assert any("Synthesis failed" in w for w in result.warnings)


def test_render_synthesis_failure_preserves_evidence():
    source = _make_source()
    result = ResearchResult(
        question="Question", collection=CollectionResult(sources=[source], elapsed_seconds=0)
    )
    text = render_result(result)
    assert "synthesis failed" in text
    assert "no sources were retrieved" not in text
    assert source.title in text
    assert source.url in text


def test_render_escapes_terminal_controls_in_all_fields():
    marker = "danger\x1b[2J\r\b\u202e"
    source = Source(
        title=marker, url="https://example.com/" + marker, snippet="Evidence", origin="web"
    )
    result = ResearchResult(
        question=marker,
        answer=AnswerWithCitations(
            question=marker, answer=marker, citations=[Citation(index=1, source=source)]
        ),
        collection=CollectionResult(sources=[source], elapsed_seconds=0),
        warnings=[marker],
    )
    text = render_result(result)
    for character in ["\x1b", "\r", "\b", "\u202e"]:
        assert character not in text
    assert "References:" in text
    assert "Warnings:" in text
    assert "\n" in text


@pytest.mark.asyncio
async def test_synthesis_error_does_not_expose_provider_details():
    class FailingService:
        async def synthesize_answer(self, question, sources):
            raise RuntimeError("private-value")

    collection = CollectionResult(sources=[_make_source()], elapsed_seconds=0)
    result = await Researcher(FakeOrchestrator(collection), FailingService()).research(
        "Question", ["wiki"]
    )
    assert "private-value" not in render_result(result)
    assert result.collection.sources == collection.sources
