import asyncio
import logging
from typing import AsyncContextManager

import httpx

import ai
from ai.schemas import AnswerWithCitations, Source
from researcher.config import Settings
from researcher.models import SourceName

logger = logging.getLogger(__name__)


class AIService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def open_source_client(self) -> AsyncContextManager[httpx.AsyncClient]:
        """Provides an HTTP client context for source fetching."""
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.per_source_timeout_seconds)
        )

    async def fetch_sources(
        self,
        source: SourceName,
        query: str,
        *,
        client: httpx.AsyncClient,
    ) -> list[Source]:
        """Routes source queries with exponential backoff retries.

        Note: DuckDuckGo (fetch_web) uses its own internal library and does
        not utilize the injected httpx client.
        """
        attempts = 0
        delay = self.settings.retry_initial_delay_seconds

        while attempts < self.settings.retry_max_attempts:
            attempts += 1
            try:
                if source == "wiki":
                    return await ai.fetch_wikipedia(query, client=client)
                elif source == "arxiv":
                    return await ai.fetch_arxiv(query, client=client)
                elif source == "web":
                    return await ai.fetch_web(query)
                else:
                    raise ValueError(f"Unknown source: {source}")
            except Exception as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise

                logger.warning(
                    "Fetch failed for %s (attempt %d/%d): %s",
                    source,
                    attempts,
                    self.settings.retry_max_attempts,
                    exc,
                )

                if attempts >= self.settings.retry_max_attempts:
                    raise

                await asyncio.sleep(delay)
                delay = min(delay * 2, self.settings.retry_max_delay_seconds)

        return []

    async def synthesize_answer(
        self,
        question: str,
        sources: list[Source],
    ) -> AnswerWithCitations:
        """Offloads the synchronous ai.synthesize execution to a thread."""
        return await asyncio.to_thread(ai.synthesize, question, sources)