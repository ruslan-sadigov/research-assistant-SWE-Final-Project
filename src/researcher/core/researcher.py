from src.researcher.models import ResearchResult


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


def render_result(result: ResearchResult) -> str:
    """Turn a ResearchResult into human-readable text for the CLI."""
    lines: list[str] = [f"Q: {result.question}"]

    if result.answer is None:
        lines.append("A: No answer could be produced — no sources were retrieved.")
    else:
        lines.append(f"A: {result.answer.answer}")
        if result.answer.citations:
            lines.append("")
            lines.append("References: ")

            for citation in result.answer.citations:
                source = citation.source
                lines.append(f"  [{citation.index}] ({source.origin}) {source.title}")
                lines.append(f"    {source.url}")

    if result.warnings:
        lines.append("")
        lines.append("Warnings: ")
        for warning in result.warnings:
            lines.append(f"  -{warning}")

    return "\n".join(lines)
