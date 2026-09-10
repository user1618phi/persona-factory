"""Tests for studio HTTP API (review + stats)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("STUDIO_API_SECRET", "test-studio-secret")
    monkeypatch.setenv("MY_USER_ID", "123456789")
    return TestClient(app)


def test_studio_stats_requires_secret(client):
    response = client.get("/api/v1/stats")
    assert response.status_code == 401


def test_studio_stats_returns_counts(client):
    def fake_count(table: str, *, filters=None):
        mapping = {
            ("persona_rules", "active"): 3,
            ("life_experiences", "active"): 2,
            ("memory_candidates", "pending"): 4,
            ("persona_rules", "pending"): 1,
            ("life_experiences", "pending"): 0,
            ("voice_examples", "active"): 10,
        }
        status = filters.get("status") if filters else None
        return mapping.get((table, status), 0)

    with patch("src.api.studio_routes._count_rows", side_effect=fake_count):
        response = client.get(
            "/api/v1/stats",
            headers={"X-Studio-Secret": os.environ["STUDIO_API_SECRET"]},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_rules"] == 3
    assert payload["active_experiences"] == 2
    assert payload["pending_review"] == 5
    assert payload["voice_examples"] == 10


def test_review_accept_calls_repository(client):
    accept = MagicMock()
    with patch("src.api.studio_routes.accept_review_item", accept):
        response = client.post(
            "/api/v1/review/accept",
            headers={"X-Studio-Secret": os.environ["STUDIO_API_SECRET"]},
            json={"kind": "candidate", "record_id": 42},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    accept.assert_called_once_with("candidate", 42, 123456789)


def test_review_accept_reports_skipped_duplicate(client):
    accept = MagicMock(return_value="skipped_duplicate")
    with patch("src.api.studio_routes.accept_review_item", accept):
        response = client.post(
            "/api/v1/review/accept",
            headers={"X-Studio-Secret": os.environ["STUDIO_API_SECRET"]},
            json={"kind": "candidate", "record_id": 42},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "skipped_duplicate"}


def test_review_reject_calls_repository(client):
    reject = MagicMock()
    with patch("src.api.studio_routes.reject_review_item", reject):
        response = client.post(
            "/api/v1/review/reject",
            headers={"X-Studio-Secret": os.environ["STUDIO_API_SECRET"]},
            json={"kind": "rule", "record_id": 7},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "rejected"}
    reject.assert_called_once_with("rule", 7, 123456789)


def test_interview_generate_uses_llm_router(client):
    async def _fake_generate(**_kwargs):
        return '{"question": "Тест?", "options": ["A", "B"]}'

    with patch("src.api.studio_routes.generate_text", _fake_generate):
        response = client.post(
            "/api/v1/interview/generate-question",
            headers={"X-Studio-Secret": os.environ["STUDIO_API_SECRET"]},
            json={"journal_entries": [], "persona_rules": []},
        )

    assert response.status_code == 200
    assert response.json() == {"question": "Тест?", "options": ["A", "B"]}


def test_interview_extract_creates_memory_candidate(client):
    create = MagicMock(return_value={"id": 99})
    async def _fake_generate(**_kwargs):
        return (
            '{"topic": "Дисциплина", "rules_and_values": "Сон важнее коммитов", '
            '"candidate_type": "belief"}'
        )

    with (
        patch("src.api.studio_routes.generate_text", _fake_generate),
        patch("src.api.studio_routes.create_memory_candidate", create),
    ):
        response = client.post(
            "/api/v1/interview/extract-answer",
            headers={"X-Studio-Secret": os.environ["STUDIO_API_SECRET"]},
            json={
                "question": "Как ты относишься к сну?",
                "answer": "Сон важнее коммитов",
                "create_candidate": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["candidate_id"] == 99
    create.assert_called_once()


def test_knowledge_chunks_endpoint_fetches_review_queue(client):
    rows = [{"id": 1, "content": "chunk", "verification_status": "unverified"}]
    fetch_queue = MagicMock(return_value=rows)
    with patch("src.api.studio_routes.fetch_knowledge_review_queue", fetch_queue):
        response = client.get(
            "/api/v1/knowledge/chunks?category=islam&status=unverified&limit=25",
            headers={"X-Studio-Secret": os.environ["STUDIO_API_SECRET"]},
        )

    assert response.status_code == 200
    assert response.json() == rows
    fetch_queue.assert_called_once_with(
        category="islam",
        status="unverified",
        limit=25,
    )


def test_knowledge_verify_endpoint_updates_status(client):
    update = MagicMock(return_value=3)
    with patch("src.api.studio_routes.update_knowledge_verification", update):
        response = client.post(
            "/api/v1/knowledge/verify",
            headers={"X-Studio-Secret": os.environ["STUDIO_API_SECRET"]},
            json={"ids": [1, 2, 3], "status": "verified"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "verified", "updated": 3}
    update.assert_called_once_with(ids=[1, 2, 3], status="verified")
