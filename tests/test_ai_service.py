import asyncio
import threading
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ai.providers.base import ProviderError
from researcher.config import Settings
from researcher.services.ai_service import AIService


@pytest.fixture
def dummy_settings():
    return Settings(
        log_level="INFO",
        max_question_length=500,
        per_source_timeout_seconds=5.0,
        max_sources_per_query=3,
        retry_max_attempts=3,
        retry_initial_delay_seconds=1.0,
        retry_max_delay_seconds=4.0,
        cache_ttl_seconds=3600,
    )


@pytest.mark.asyncio
async def test_fetch_sources_wiki_success(dummy_settings):
    service = AIService(dummy_settings)
    mock_source = MagicMock()

    with patch("ai.fetch_wikipedia", new_callable=AsyncMock) as mock_wiki:
        mock_wiki.return_value = [mock_source]
        async with service.open_source_client() as client:
            result = await service.fetch_sources("wiki", "query", client=client)

        assert result == [mock_source]
        mock_wiki.assert_called_once_with("query", client=client, max_results=3)


@pytest.mark.asyncio
async def test_fetch_sources_retry_and_succeed(dummy_settings):
    service = AIService(dummy_settings)
    mock_source = MagicMock()

    with (
        patch("ai.fetch_wikipedia", new_callable=AsyncMock) as mock_wiki,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        mock_wiki.side_effect = [ProviderError("Network error"), [mock_source]]
        async with service.open_source_client() as client:
            result = await service.fetch_sources("wiki", "query", client=client)

        assert result == [mock_source]
        assert mock_wiki.call_count == 2
        mock_sleep.assert_called_once_with(1.0)


@pytest.mark.asyncio
async def test_fetch_sources_cancellation(dummy_settings):
    service = AIService(dummy_settings)

    with patch("ai.fetch_wikipedia", new_callable=AsyncMock) as mock_wiki:
        mock_wiki.side_effect = asyncio.CancelledError()
        async with service.open_source_client() as client:
            with pytest.raises(asyncio.CancelledError):
                await service.fetch_sources("wiki", "query", client=client)


@pytest.mark.asyncio
async def test_synthesize_answer_thread_offload(dummy_settings):
    service = AIService(dummy_settings)
    mock_answer = MagicMock()

    with patch("ai.synthesize") as mock_synthesize:
        mock_synthesize.return_value = mock_answer
        result = await service.synthesize_answer("question", [])

        assert result == mock_answer
        mock_synthesize.assert_called_once_with("question", [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "function"),
    [("wiki", "fetch_wikipedia"), ("arxiv", "fetch_arxiv"), ("web", "fetch_web")],
)
async def test_routes_all_sources_with_client_and_limit(dummy_settings, name, function):
    service = AIService(dummy_settings.model_copy(update={"max_sources_per_query": 7}))
    with patch(f"ai.{function}", new_callable=AsyncMock, return_value=[]) as fetch:
        async with service.open_source_client() as client:
            assert await service.fetch_sources(name, "query", client=client) == []
        fetch.assert_awaited_once_with("query", client=client, max_results=7)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ValueError("bad input"),
        ProviderError("TAVILY_API_KEY is not set."),
        ProviderError("The package is required."),
        RuntimeError("bug"),
    ],
)
async def test_permanent_errors_are_not_retried(dummy_settings, error):
    service = AIService(dummy_settings)
    with (
        patch("ai.fetch_web", new_callable=AsyncMock, side_effect=error) as fetch,
        patch("asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        async with service.open_source_client() as client:
            with pytest.raises(type(error)):
                await service.fetch_sources("web", "query", client=client)
        assert fetch.await_count == 1
        sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_exhaustion_caps_backoff_without_final_sleep(dummy_settings, caplog):
    settings = dummy_settings.model_copy(
        update={"retry_max_attempts": 5, "retry_max_delay_seconds": 2.0}
    )
    service = AIService(settings)
    with (
        patch(
            "ai.fetch_web", new_callable=AsyncMock, side_effect=ProviderError("secret-token")
        ) as fetch,
        patch("asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        async with service.open_source_client() as client:
            with pytest.raises(ProviderError):
                await service.fetch_sources("web", "private-question", client=client)
    assert fetch.await_count == 5
    assert [call.args[0] for call in sleep.await_args_list] == [1, 2, 2, 2]
    assert "secret-token" not in caplog.text
    assert "private-question" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 429, 500, 503])
async def test_http_retries_and_closes_responses(dummy_settings, status):
    responses = []

    def handler(request):
        response = httpx.Response(status if not responses else 200, json={"ok": True})
        responses.append(response)
        return response

    service = AIService(dummy_settings, transport_factory=lambda: httpx.MockTransport(handler))
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
        async with service.open_source_client() as client:
            response = await client.get("https://example.com/search")
    assert response.json() == {"ok": True}
    assert len(responses) == 2
    assert all(response.is_closed for response in responses)
    sleep.assert_awaited_once_with(1.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_http_permanent_failure_is_not_retried_by_fetcher(dummy_settings, status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status)

    service = AIService(dummy_settings, transport_factory=lambda: httpx.MockTransport(handler))
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
        async with service.open_source_client() as client:
            with pytest.raises(ProviderError):
                await service.fetch_sources("wiki", "query", client=client)
    assert len(requests) == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_exhaustion_does_not_multiply_outer_retries(dummy_settings):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503)

    service = AIService(dummy_settings, transport_factory=lambda: httpx.MockTransport(handler))
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
        async with service.open_source_client() as client:
            with pytest.raises(ProviderError):
                await service.fetch_sources("wiki", "query", client=client)
    assert len(requests) == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_wikipedia_summary_request_is_retried_internally(dummy_settings):
    summaries = []

    def handler(request):
        if request.url.path.endswith("api.php"):
            return httpx.Response(200, json=["query", ["Photosynthesis"]])
        summaries.append(request)
        if len(summaries) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"title": "Photosynthesis", "extract": "Plant energy"})

    service = AIService(dummy_settings, transport_factory=lambda: httpx.MockTransport(handler))
    with patch("asyncio.sleep", new_callable=AsyncMock):
        async with service.open_source_client() as client:
            sources = await service.fetch_sources("wiki", "query", client=client)
    assert len(summaries) == 2
    assert sources[0].title == "Photosynthesis"


@pytest.mark.asyncio
async def test_search_post_body_is_replayed(dummy_settings):
    bodies = []

    def handler(request):
        bodies.append(request.content)
        if len(bodies) == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={})

    service = AIService(dummy_settings, transport_factory=lambda: httpx.MockTransport(handler))
    with patch("asyncio.sleep", new_callable=AsyncMock):
        async with service.open_source_client() as client:
            await client.post("https://example.com/search", json={"q": "question"})
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]


@pytest.mark.asyncio
async def test_synthesis_retries_transient_failure(dummy_settings):
    answer = MagicMock()
    with (
        patch("ai.synthesize", side_effect=[ProviderError("temporary"), answer]) as synth,
        patch("asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        assert await AIService(dummy_settings).synthesize_answer("question", []) is answer
    assert synth.call_count == 2
    sleep.assert_awaited_once_with(1.0)


@pytest.mark.asyncio
async def test_synthesis_validation_failure_is_not_retried(dummy_settings):
    with (
        patch("ai.synthesize", side_effect=ValueError("empty sources")) as synth,
        patch("asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        with pytest.raises(ValueError):
            await AIService(dummy_settings).synthesize_answer("question", [])
    assert synth.call_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_synthesis_runs_off_event_loop_and_cancellation_stops_retries(dummy_settings):
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    event_loop_thread = threading.get_ident()
    worker_threads = []

    def blocking_synth(*args):
        worker_threads.append(threading.get_ident())
        loop.call_soon_threadsafe(started.set)
        if not release.wait(3):
            raise RuntimeError("Test worker release timed out")
        raise ProviderError("temporary")

    with patch("ai.synthesize", side_effect=blocking_synth) as synth:
        task = asyncio.create_task(AIService(dummy_settings).synthesize_answer("question", []))
        try:
            await asyncio.wait_for(started.wait(), 1)
            assert len(worker_threads) == 1
            assert worker_threads[0] != event_loop_thread
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    assert synth.call_count == 1


@pytest.mark.asyncio
async def test_cancellation_during_backoff_stops_retry(dummy_settings):
    sleeping = asyncio.Event()

    async def pause(delay):
        sleeping.set()
        await asyncio.Event().wait()

    with (
        patch(
            "ai.fetch_web", new_callable=AsyncMock, side_effect=ProviderError("temporary")
        ) as fetch,
        patch("asyncio.sleep", side_effect=pause),
    ):
        service = AIService(dummy_settings)
        async with service.open_source_client() as client:
            task = asyncio.create_task(service.fetch_sources("web", "question", client=client))
            try:
                await asyncio.wait_for(sleeping.wait(), 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_unknown_source_fails_without_retry(dummy_settings):
    service = AIService(dummy_settings)
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
        async with service.open_source_client() as client:
            with pytest.raises(ValueError, match="Unknown source"):
                await service.fetch_sources("invalid", "question", client=client)
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_response_body_read_failure_is_retried(dummy_settings):
    class BrokenBody(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False

        async def __aiter__(self):
            yield b"partial"
            raise httpx.ReadError("connection interrupted")

        async def aclose(self):
            self.closed = True

    broken = BrokenBody()
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(200, stream=broken)
        return httpx.Response(200, json={"ok": True})

    service = AIService(dummy_settings, transport_factory=lambda: httpx.MockTransport(handler))
    with patch("asyncio.sleep", new_callable=AsyncMock):
        async with service.open_source_client() as client:
            response = await client.get("https://example.com/search")
    assert response.json() == {"ok": True}
    assert broken.closed
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_synthesis_retry_budget_is_bounded(dummy_settings):
    with (
        patch("ai.synthesize", side_effect=ProviderError("temporary")) as synth,
        patch("asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        with pytest.raises(ProviderError):
            await AIService(dummy_settings).synthesize_answer("question", [])
    assert synth.call_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [1, 2]
