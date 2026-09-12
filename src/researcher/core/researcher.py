from researcher.concurrency.orchestrator import SourceOrchestrator
from researcher.models import (
    ResearchResult,
    SourceName,
)
from researcher.services.ai_service import AIService


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


class Researcher:
    """Coordinate source collection and answer synthesis for one question."""

    def __init__(self, orchestrator: SourceOrchestrator, ai_service: AIService) -> None:
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
        except Exception as exc:
            warnings.append(f"Synthesis failed: {exc}")
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
