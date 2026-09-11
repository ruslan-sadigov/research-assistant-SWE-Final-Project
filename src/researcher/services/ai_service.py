"""AI calls with bounded retries, shared HTTP connections, and async synthesis."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import TypeVar

import httpx

import ai
from ai.providers.base import ProviderError
from ai.schemas import AnswerWithCitations, Source
from researcher.config import Settings
from researcher.models import SourceName

logger = logging.getLogger(__name__)
T = TypeVar("T")


class HTTPRetryExhausted(ProviderError):
    """An HTTP operation already consumed its budget; do not retry its fetcher."""


def _retryable(exc: Exception) -> bool:
    """Inspect preserved causes without importing optional provider SDKs."""
    seen: set[int] = set()
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(error, HTTPRetryExhausted) for error in chain):
        return False
    for error in reversed(chain):
        status = getattr(error, "status_code", None)
        if status is None:
            status = getattr(error, "code", None)
        if status is None and isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
        if isinstance(status, int):
            return status in (408, 429) or 500 <= status < 600
        if isinstance(error, (ValueError, TypeError, ImportError)):
            return False
        if isinstance(error, (httpx.TransportError, TimeoutError, ConnectionError)):
            return True
        if type(error).__name__ in ("APIConnectionError", "APITimeoutError"):
            return True
    if isinstance(exc, ProviderError):
        # Supplied providers use untyped ProviderError for setup failures.
        message = str(exc).lower()
        permanent = (
            "is not set",
            "package",
            "unknown llm_provider",
            "unknown web_search_provider",
            "unknown embedding_provider",
            "malformed xml",
        )
        return not any(part in message for part in permanent)
    return False


async def _retry(
    operation: Callable[[], Awaitable[T]],
    settings: Settings,
    label: str,
    *,
    http_operation: bool = False,
) -> T:
    delay = settings.retry_initial_delay_seconds
    for attempt in range(1, settings.retry_max_attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            retryable = _retryable(exc)
            logger.warning(
                "AI operation failed: operation=%s attempt=%d max_attempts=%d error_type=%s",
                label,
                attempt,
                settings.retry_max_attempts,
                type(exc).__name__,
            )
            if not retryable:
                raise
            if attempt == settings.retry_max_attempts:
                if http_operation:
                    raise HTTPRetryExhausted("HTTP retry budget exhausted") from exc
                raise
            await asyncio.sleep(delay)
            delay = min(delay * 2, settings.retry_max_delay_seconds)
    raise AssertionError("Settings must allow at least one attempt")


class RetryingTransport(httpx.AsyncBaseTransport):
    """Retry complete source requests, including response-body read failures.

    Intended for the supplied GET and search POST calls, not arbitrary writes.
    Responses are buffered because the fetchers consume complete JSON/XML.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport, settings: Settings):
        self._transport = transport
        self._settings = settings

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()

        async def send() -> httpx.Response:
            request.stream = httpx.ByteStream(body)
            response = await self._transport.handle_async_request(request)
            response.request = request
            try:
                if response.status_code in (408, 429) or 500 <= response.status_code < 600:
                    response.raise_for_status()
                await response.aread()
                return response
            finally:
                await response.aclose()

        return await _retry(send, self._settings, "http", http_operation=True)

    async def aclose(self) -> None:
        await self._transport.aclose()


class AIService:
    def __init__(
        self,
        settings: Settings,
        *,
        transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        self.settings = settings
        self._transport_factory = transport_factory or httpx.AsyncHTTPTransport

    def open_source_client(self) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.per_source_timeout_seconds),
            transport=RetryingTransport(self._transport_factory(), self.settings),
        )

    async def fetch_sources(
        self,
        source: SourceName,
        query: str,
        *,
        client: httpx.AsyncClient,
    ) -> list[Source]:
        """Use public AI fetchers; only DuckDuckGo ignores the shared client."""
        fetchers = {"wiki": ai.fetch_wikipedia, "arxiv": ai.fetch_arxiv, "web": ai.fetch_web}
        if source not in fetchers:
            raise ValueError(f"Unknown source: {source}")

        async def fetch() -> list[Source]:
            return await fetchers[source](
                query, client=client, max_results=self.settings.max_sources_per_query
            )

        return await _retry(fetch, self.settings, source)

    async def synthesize_answer(
        self,
        question: str,
        sources: list[Source],
    ) -> AnswerWithCitations:
        """Offload each attempt; cancellation cannot stop an active SDK thread."""

        async def synthesize() -> AnswerWithCitations:
            return await asyncio.to_thread(ai.synthesize, question, sources)

        return await _retry(synthesize, self.settings, "synthesis")
