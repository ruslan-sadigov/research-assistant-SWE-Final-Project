import pytest

from researcher.core.researcher import validate_question


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
