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


@pytest.mark.asyncio
async def test_wikipedia_requests_identify_application(dummy_settings):
    requests = []

    def handler(request):
        requests.append(request)
        user_agent = request.headers["User-Agent"]
        assert "ResearchAssistant/" in user_agent
        assert (
            "https://github.com/ruslan-sadigov/research-assistant-SWE-Final-Project" in user_agent
        )
        if request.url.path.endswith("api.php"):
            return httpx.Response(200, json=["query", ["Photosynthesis"]])
        return httpx.Response(200, json={"title": "Photosynthesis", "extract": "Plant energy"})

    service = AIService(dummy_settings, transport_factory=lambda: httpx.MockTransport(handler))
    async with service.open_source_client() as client:
        sources = await service.fetch_sources("wiki", "query", client=client)
    assert len(requests) == 2
    assert sources[0].title == "Photosynthesis"


@pytest.mark.asyncio
@pytest.mark.parametrize("transient_failure", [False, True])
async def test_arxiv_redirect_preserves_query_and_retry_policy(dummy_settings, transient_failure):
    requests = []
    responses = []
    https_attempts = 0

    def handler(request):
        nonlocal https_attempts
        requests.append(request)
        assert request.url.params["search_query"] == "all:quantum physics"
        assert request.url.params["max_results"] == "3"
        assert "ResearchAssistant/" in request.headers["User-Agent"]
        if request.url.scheme == "http":
            response = httpx.Response(
                301, headers={"Location": str(request.url.copy_with(scheme="https"))}
            )
        else:
            https_attempts += 1
            if transient_failure and https_attempts == 1:
                response = httpx.Response(503)
            else:
                response = httpx.Response(
                    200,
                    text='<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Quantum physics</title><summary>Evidence</summary><id>https://arxiv.org/abs/1234.5678</id></entry></feed>',
                )
        responses.append(response)
        return response

    service = AIService(dummy_settings, transport_factory=lambda: httpx.MockTransport(handler))
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
        async with service.open_source_client() as client:
            sources = await service.fetch_sources("arxiv", "quantum physics", client=client)
    assert sources[0].title == "Quantum physics"
    assert sources[0].origin == "arxiv"
    assert [request.url.scheme for request in requests] == (
        ["http", "https", "https"] if transient_failure else ["http", "https"]
    )
    assert sleep.await_count == int(transient_failure)
    assert all(response.is_closed for response in responses)


@pytest.mark.asyncio
async def test_redirect_loop_stops_without_outer_retries(dummy_settings):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(301, headers={"Location": str(request.url)})

    service = AIService(dummy_settings, transport_factory=lambda: httpx.MockTransport(handler))
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
        async with service.open_source_client() as client:
            with pytest.raises(ProviderError) as exc:
                await service.fetch_sources("arxiv", "query", client=client)
    assert isinstance(exc.value.__cause__, httpx.TooManyRedirects)
    assert len(requests) == 6
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429, 503])
async def test_failure_logs_http_status_without_sensitive_details(dummy_settings, caplog, status):
    settings = dummy_settings.model_copy(update={"retry_max_attempts": 1})

    def handler(request):
        return httpx.Response(status, text="private-response-body")

    service = AIService(settings, transport_factory=lambda: httpx.MockTransport(handler))
    async with service.open_source_client() as client:
        with pytest.raises(ProviderError):
            await service.fetch_sources("arxiv", "private-query", client=client)
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "researcher.services.ai_service"
    ]
    assert messages
    assert all(f"http_status={status}" in message for message in messages)
    assert all(
        "private-query" not in message and "private-response-body" not in message
        for message in messages
    )


def test_http_status_handles_sdk_errors_and_cycles():
    from researcher.services.ai_service import _http_status

    error = RuntimeError("private-error")
    error.code = 404
    wrapped = ProviderError("private-wrapper")
    wrapped.__cause__ = error
    assert _http_status(wrapped) == 404
    error.code = "private-value"
    error.__cause__ = wrapped
    assert _http_status(wrapped) is None


@pytest.mark.asyncio
async def test_failure_log_has_no_status_for_transport_error(dummy_settings, caplog):
    def handler(request):
        raise httpx.ConnectError("private-error", request=request)

    settings = dummy_settings.model_copy(update={"retry_max_attempts": 1})
    service = AIService(settings, transport_factory=lambda: httpx.MockTransport(handler))
    async with service.open_source_client() as client:
        with pytest.raises(ProviderError):
            await service.fetch_sources("arxiv", "query", client=client)
    assert "http_status=None" in caplog.text
    assert "private-error" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["http", "https"])
async def test_arxiv_error_feed_rejected_without_retry(dummy_settings, scheme, caplog):
    xml = f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>{scheme}://arxiv.org/api/errors#incorrect_id_format</id><title>Error</title><summary>private-error-detail</summary></entry></feed>'
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=xml)

    service = AIService(dummy_settings, transport_factory=lambda: httpx.MockTransport(handler))
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
        async with service.open_source_client() as client:
            with pytest.raises(ProviderError, match="API error feed") as error:
                await service.fetch_sources("arxiv", "question", client=client)
    assert len(requests) == 1
    sleep.assert_not_awaited()
    assert "private-error-detail" not in str(error.value)
    assert "private-error-detail" not in caplog.text


@pytest.mark.asyncio
async def test_arxiv_error_feed_not_cached_or_synthesized(dummy_settings):
    from researcher.concurrency.orchestrator import SourceOrchestrator
    from researcher.core.researcher import Researcher

    xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#invalid_query</id><title>Error</title><summary>Invalid query</summary></entry></feed>'
    service = AIService(
        dummy_settings,
        transport_factory=lambda: httpx.MockTransport(
            lambda request: httpx.Response(200, text=xml)
        ),
    )
    cache = MagicMock()
    cache.get_sources = AsyncMock(return_value=None)
    cache.set_sources = AsyncMock()
    with patch.object(service, "synthesize_answer", new_callable=AsyncMock) as synthesize:
        result = await Researcher(
            SourceOrchestrator(dummy_settings, service, cache), service
        ).research("question", ["arxiv"])
    assert result.answer is None
    assert result.collection.sources == []
    assert result.collection.outcomes[0].status == "failed"
    cache.set_sources.assert_not_awaited()
    synthesize.assert_not_awaited()


@pytest.mark.asyncio
async def test_arxiv_paper_titled_error_is_valid(dummy_settings):
    xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/1234.5678</id><title>Error</title><summary>A paper about errors</summary></entry></feed>'
    service = AIService(
        dummy_settings,
        transport_factory=lambda: httpx.MockTransport(
            lambda request: httpx.Response(200, text=xml)
        ),
    )
    async with service.open_source_client() as client:
        sources = await service.fetch_sources("arxiv", "question", client=client)
    assert len(sources) == 1
    assert sources[0].title == "Error"
