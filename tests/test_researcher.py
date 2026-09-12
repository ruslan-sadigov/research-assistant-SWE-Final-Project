import pytest

from ai.schemas import AnswerWithCitations, Citation, Source
from src.researcher.core.researcher import render_result, validate_question
from src.researcher.models import CollectionResult, ResearchResult, SourceOutcome


def test_validate_question_strips_whitespace():
    result = validate_question("  What is photosynthesis?  ", max_length=100)
    assert result == "What is photosynthesis?"


def test_validate_question_rejects_empty_string():
    with pytest.raises(ValueError):
        validate_question("", max_length=100)


def test_validate_question_rejects_whitespace_only():
    with pytest.raises(ValueError):
        validate_question("     ", max_length=100)


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
