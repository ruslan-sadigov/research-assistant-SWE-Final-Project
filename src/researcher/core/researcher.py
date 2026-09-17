import unicodedata

from researcher.interfaces import AIServiceProtocol, SourceOrchestratorProtocol
from researcher.models import (
    ResearchResult,
    SourceName,
)


def validate_question(question: str, max_length: int) -> str:
    """Validate and clean a raw research question.

    Raises
    ------
    ValueError
        If the question is empty (after trimming whitespace), has no letters
        or digits, or exceeds max_length characters.
    """
    cleaned = question.strip()

    if not cleaned:
        raise ValueError("Question must not be empty.")

    # Punctuation-only questions have no cache key and nothing to search for.
    if not any(character.isalnum() for character in cleaned):
        raise ValueError("Question must contain at least one letter or digit.")

    if len(cleaned) > max_length:
        raise ValueError(
            f"Question is {len(cleaned)} characters, " f"which exceeds the maximum of {max_length}."
        )

    return cleaned


def sanitize_output(text: str) -> str:
    """Escape terminal controls and Unicode formatting controls for plain-text output."""
    return "".join(
        (
            character
            if character in "\n\t" or not unicodedata.category(character).startswith("C")
            else ascii(character)[1:-1]
        )
        for character in text
    )


def render_result(result: ResearchResult) -> str:
    """Turn a ResearchResult into human-readable text for the CLI."""
    lines: list[str] = [f"Q: {result.question}"]

    if result.answer is None:
        if result.collection.sources:
            lines.append("A: No answer could be produced - synthesis failed.")
            lines.extend(["", "Retrieved sources:"])
            for source in result.collection.sources:
                lines.extend([f"  ({source.origin}) {source.title}", f"    {source.url}"])
        else:
            lines.append("A: No answer could be produced - no sources were retrieved.")
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
            lines.append(f"  - {warning}")

    return sanitize_output("\n".join(lines))


class Researcher:
    """Coordinate source collection and answer synthesis for one question."""

    def __init__(
        self, orchestrator: SourceOrchestratorProtocol, ai_service: AIServiceProtocol
    ) -> None:
        self._orchestrator = orchestrator
        self._ai_service = ai_service

    async def research(
        self,
        question: str,
        selected_sources: list[SourceName],
        *,
        use_cache: bool = True,
    ) -> ResearchResult:
        collection = await self._orchestrator.collect_sources(
            question, selected_sources, use_cache=use_cache
        )

        warnings = [outcome.warning for outcome in collection.outcomes if outcome.warning]

        if not collection.sources:
            warnings.append("No sources were retrieved; no answer could be produced.")
            return ResearchResult(
                question=question,
                answer=None,
                collection=collection,
                warnings=warnings,
            )

        try:
            answer = await self._ai_service.synthesize_answer(question, collection.sources)
        except Exception:
            warnings.append("Synthesis failed; check provider configuration and availability.")
            return ResearchResult(
                question=question,
                answer=None,
                collection=collection,
                warnings=warnings,
            )

        return ResearchResult(
            question=question,
            answer=answer,
            collection=collection,
            warnings=warnings,
        )
