import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        mock_wiki.assert_called_once_with("query", client=client)


@pytest.mark.asyncio
async def test_fetch_sources_retry_and_succeed(dummy_settings):
    service = AIService(dummy_settings)
    mock_source = MagicMock()

    with patch(
        "ai.fetch_wikipedia", new_callable=AsyncMock
    ) as mock_wiki, patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_wiki.side_effect = [Exception("Network error"), [mock_source]]
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