import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from researcher.services.ai_service import AIService


@pytest.fixture
def dummy_settings():
    """Provides a default dummy Settings object for testing AIService."""
    settings = MagicMock()
    settings.per_source_timeout_seconds = 5.0
    settings.retry_max_attempts = 3
    settings.retry_initial_delay_seconds = 0.01
    settings.retry_max_delay_seconds = 0.05
    return settings


@pytest.mark.asyncio
async def test_open_source_client_lifecycle(dummy_settings):
    """Verifies that open_source_client correctly yields an AsyncClient and closes it."""
    service = AIService(dummy_settings)
    async with service.open_source_client() as client:
        assert client is not None
        assert not client.is_closed
    assert client.is_closed


@pytest.mark.asyncio
async def test_fetch_sources_wiki_success(dummy_settings):
    """Verifies successful source fetching for Wikipedia provider."""
    service = AIService(dummy_settings)
    mock_source = MagicMock()

    with patch("ai.fetch_wikipedia", new_callable=AsyncMock) as mock_wiki:
        mock_wiki.return_value = [mock_source]
        async with service.open_source_client() as client:
            result = await service.fetch_sources("wiki", "python async", client=client)

        assert result == [mock_source]
        mock_wiki.assert_called_once_with("python async", client=client)


@pytest.mark.asyncio
async def test_fetch_sources_retry_recovery(dummy_settings):
    """Verifies that transient errors trigger retries and recover on subsequent attempts."""
    service = AIService(dummy_settings)
    mock_source = MagicMock()

    with patch("ai.fetch_wikipedia", new_callable=AsyncMock) as mock_wiki, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # First call fails, second call succeeds
        mock_wiki.side_effect = [Exception("Network blip"), [mock_source]]

        async with service.open_source_client() as client:
            result = await service.fetch_sources("wiki", "query", client=client)

        assert result == [mock_source]
        assert mock_wiki.call_count == 2
        mock_sleep.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_sources_cancellation_not_retried(dummy_settings):
    """Verifies that CancelledError immediately re-raises without retry delays."""
    service = AIService(dummy_settings)

    with patch("ai.fetch_wikipedia", new_callable=AsyncMock) as mock_wiki, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_wiki.side_effect = asyncio.CancelledError()

        async with service.open_source_client() as client:
            with pytest.raises(asyncio.CancelledError):
                await service.fetch_sources("wiki", "query", client=client)

        assert mock_wiki.call_count == 1
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_synthesize_answer_offloaded_to_thread(dummy_settings):
    """Verifies that synchronous ai.synthesize call is offloaded to a thread."""
    service = AIService(dummy_settings)

    with patch("ai.synthesize") as mock_synth, \
         patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = "Synthesized response"

        res = await service.synthesize_answer("prompt", ["sources"])

        assert res == "Synthesized response"
        mock_to_thread.assert_called_once_with(mock_synth, "prompt", ["sources"])


@pytest.mark.asyncio
async def test_fetch_sources_invalid_source(dummy_settings):
    """Verifies ValueError is raised when an unsupported provider string is passed."""
    service = AIService(dummy_settings)
    async with service.open_source_client() as client:
        with pytest.raises(ValueError, match="Unknown source provider"):
            await service.fetch_sources("unsupported_source", "query", client=client)


@pytest.mark.asyncio
async def test_fetch_sources_max_retries_exceeded(dummy_settings):
    """Verifies exception propagation after reaching retry_max_attempts limit."""
    service = AIService(dummy_settings)

    with patch("ai.fetch_wikipedia", new_callable=AsyncMock) as mock_wiki, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_wiki.side_effect = Exception("Persistent connection error")

        async with service.open_source_client() as client:
            with pytest.raises(Exception, match="Persistent connection error"):
                await service.fetch_sources("wiki", "query", client=client)

        assert mock_wiki.call_count == dummy_settings.retry_max_attempts


@pytest.mark.asyncio
async def test_fetch_sources_arxiv_and_web_routing(dummy_settings):
    """Verifies correct routing for arXiv and web search provider targets."""
    service = AIService(dummy_settings)
    mock_source = MagicMock()

    with patch("ai.fetch_arxiv", new_callable=AsyncMock) as mock_arxiv, \
         patch("ai.fetch_web", new_callable=AsyncMock) as mock_web:

        mock_arxiv.return_value = [mock_source]
        mock_web.return_value = [mock_source]

        async with service.open_source_client() as client:
            res_arxiv = await service.fetch_sources("arxiv", "query", client=client)
            res_web = await service.fetch_sources("web", "query", client=client)

        assert res_arxiv == [mock_source]
        assert res_web == [mock_source]
        mock_arxiv.assert_called_once_with("query", client=client)
        mock_web.assert_called_once_with("query")