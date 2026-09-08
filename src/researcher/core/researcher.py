def validate_question(question: str, max_length: int) -> str:
    """Validate and clean a raw research question.

    Raises
    ------
    ValueError
        If the question is empty (after trimming whitespace) or exceeds
        max_length characters.
    """
    cleaned = question.strip()

    if not cleaned:
        raise ValueError("Question must not be empty.")

    if len(cleaned) > max_length:
        raise ValueError(
            f"Question is {len(cleaned)} characters, " f"which exceeds the maximum of {max_length}."
        )

    return cleaned
