"""HTTP API for the research assistant.

Reuses the same components as `cli.py` (Researcher, SourceOrchestrator,
AIService, SourceCache) so the web API and the CLI share identical behavior.
This module adds no new business logic.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from researcher.cli import create_cache_store
from researcher.concurrency.orchestrator import SourceOrchestrator
from researcher.config import load_settings
from researcher.core.researcher import Researcher, validate_question
from researcher.models import SourceName
from researcher.services.ai_service import AIService
from researcher.services.cache import SourceCache

VALID_SOURCES = ("wiki", "arxiv", "web")
DEFAULT_SOURCES: list[SourceName] = ["wiki", "arxiv", "web"]


class AskRequest(BaseModel):
    question: str
    sources: list[SourceName] = Field(default_factory=lambda: list(DEFAULT_SOURCES))
    use_cache: bool = True


class SourceItem(BaseModel):
    title: str
    url: str
    origin: str


class Citation(SourceItem):
    index: int


class AskResponse(BaseModel):
    question: str
    answer: str | None
    citations: list[Citation]
    sources: list[SourceItem]
    warnings: list[str]


def create_app() -> FastAPI:
    settings = load_settings()
    app = FastAPI(title="Research Assistant API")

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
        allow_methods=["POST"],
        allow_headers=["*"],
    )

    @app.post("/ask", response_model=AskResponse)
    async def ask(request: AskRequest) -> AskResponse:
        try:
            question = validate_question(request.question, settings.max_question_length)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        unknown = [s for s in request.sources if s not in VALID_SOURCES]
        if unknown or not request.sources:
            raise HTTPException(status_code=422, detail="Select at least one valid source.")

        store = create_cache_store(settings, use_cache=request.use_cache)
        try:
            ai_service = AIService(settings)
            cache = SourceCache(store, settings.cache_ttl_seconds)
            orchestrator = SourceOrchestrator(settings, ai_service, cache)
            researcher = Researcher(orchestrator, ai_service)
            result = await researcher.research(
                question, request.sources, use_cache=request.use_cache
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="Research failed; check configuration and provider availability.",
            ) from exc
        finally:
            await store.close()

        citations = (
            [
                Citation(
                    index=c.index, title=c.source.title, url=c.source.url, origin=c.source.origin
                )
                for c in result.answer.citations
            ]
            if result.answer
            else []
        )
        sources = [
            SourceItem(title=s.title, url=s.url, origin=s.origin) for s in result.collection.sources
        ]

        return AskResponse(
            question=result.question,
            answer=result.answer.answer if result.answer else None,
            citations=citations,
            sources=sources,
            warnings=result.warnings,
        )

    return app


app = create_app()
