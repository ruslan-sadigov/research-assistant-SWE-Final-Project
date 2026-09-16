"""Tests for the FastAPI wrapper around the research assistant.

Mirrors the fake-AIService pattern in tests/test_cli.py: only the network
boundary (AIService) is faked, so the real Researcher, SourceOrchestrator,
and SourceCache logic runs under test.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from ai.schemas import AnswerWithCitations, Source
from researcher import api
from researcher.config import Settings


class OfflineAIService:
    fetch_count = 0
    synthesis_count = 0
    fail_source: str | None = None
    fail_synthesis = False

    def __init__(self, settings):
        pass

    @asynccontextmanager
    async def open_source_client(self):
        yield None

    async def fetch_sources(self, source, query, *, client):
        type(self).fetch_count += 1
        if source == type(self).fail_source:
            raise RuntimeError("simulated fetch failure")
        origin = "wikipedia" if source == "wiki" else source
        return [
            Source(
                title=f"{source} result",
                url=f"https://example.com/{source}",
                snippet="Evidence",
                origin=origin,
            )
        ]

    async def synthesize_answer(self, question, sources):
        type(self).synthesis_count += 1
        if type(self).fail_synthesis:
            raise RuntimeError("simulated synthesis failure")
        return AnswerWithCitations(
            question=question,
            answer="Answer [1]",
            citations=[{"index": 1, "source": sources[0]}],
        )


@pytest.fixture(autouse=True)
def reset_offline_service():
    OfflineAIService.fetch_count = 0
    OfflineAIService.synthesis_count = 0
    OfflineAIService.fail_source = None
    OfflineAIService.fail_synthesis = False
    yield


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "AIService", OfflineAIService)
    return TestClient(api.create_app())


def test_ask_returns_answer_with_citations(client):
    response = client.post(
        "/ask",
        json={"question": "What is photosynthesis?", "sources": ["wiki"], "use_cache": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["question"] == "What is photosynthesis?"
    assert body["answer"] == "Answer [1]"
    assert body["citations"] == [
        {
            "index": 1,
            "title": "wiki result",
            "url": "https://example.com/wiki",
            "origin": "wikipedia",
        }
    ]
    assert body["sources"] == [
        {"title": "wiki result", "url": "https://example.com/wiki", "origin": "wikipedia"}
    ]
    assert body["warnings"] == []


def test_ask_defaults_to_all_three_sources(client):
    response = client.post("/ask", json={"question": "question", "use_cache": False})
    assert response.status_code == 200
    assert OfflineAIService.fetch_count == 3


def test_ask_rejects_empty_question(client):
    response = client.post("/ask", json={"question": "   ", "use_cache": False})
    assert response.status_code == 422
    assert "empty" in response.json()["detail"].lower()


def test_ask_rejects_unknown_source_via_schema(client):
    response = client.post("/ask", json={"question": "question", "sources": ["bogus"]})
    assert response.status_code == 422


def test_ask_rejects_empty_source_list(client):
    response = client.post("/ask", json={"question": "question", "sources": [], "use_cache": False})
    assert response.status_code == 422
    assert "at least one" in response.json()["detail"].lower()


def test_ask_source_failure_is_reported_as_warning_not_a_crash(client):
    OfflineAIService.fail_source = "wiki"
    response = client.post(
        "/ask", json={"question": "question", "sources": ["wiki"], "use_cache": False}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] is None
    assert body["sources"] == []
    assert any("wiki" in warning for warning in body["warnings"])


def test_ask_synthesis_failure_still_returns_retrieved_sources(client):
    OfflineAIService.fail_synthesis = True
    response = client.post(
        "/ask", json={"question": "question", "sources": ["web"], "use_cache": False}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] is None
    assert len(body["sources"]) == 1
    assert any("Synthesis failed" in warning for warning in body["warnings"])


def test_ask_closes_cache_store_even_on_unexpected_error(monkeypatch, client):
    store = AsyncMock()
    monkeypatch.setattr(api, "create_cache_store", lambda settings, **kwargs: store)
    monkeypatch.setattr(
        api.Researcher, "research", AsyncMock(side_effect=RuntimeError("private-value"))
    )
    response = client.post(
        "/ask", json={"question": "question", "sources": ["web"], "use_cache": False}
    )
    assert response.status_code == 500
    assert "private-value" not in response.text
    store.close.assert_awaited_once()


@pytest.fixture
def client_with_persistent_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "AIService", OfflineAIService)
    settings = Settings(database_url="sqlite:///" + (tmp_path / "sources.sqlite3").as_posix())
    monkeypatch.setattr(api, "load_settings", lambda: settings)
    return TestClient(api.create_app())


def test_ask_reuses_cached_sources_across_requests(client_with_persistent_cache):
    payload = {"question": "question", "sources": ["web"], "use_cache": True}
    first = client_with_persistent_cache.post("/ask", json=payload)
    second = client_with_persistent_cache.post("/ask", json=payload)

    assert first.status_code == second.status_code == 200
    assert OfflineAIService.fetch_count == 1
    assert OfflineAIService.synthesis_count == 2
