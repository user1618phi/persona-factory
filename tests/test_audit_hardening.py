"""Regression tests for V2.1 audit hardening."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from src.bot import session_store
from src.config import get_settings
from src.db import memory_repository
from src.orchestrator.brainstorm import _routing_strategy
from src.orchestrator.llm_router import (
    _llm_routes,
    _retry_delay,
    count_prompt_tokens,
    fit_prompts_to_token_budget,
)

ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, data=None):
        self.data = data or []


class _FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.operation = ""
        self.payload = None

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def upsert(self, payload, **_kwargs):
        self.operation = "upsert"
        self.payload = payload
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return self.client.execute(self)


class _DuplicateRuleClient:
    def __init__(self):
        self.upserts = []
        self.updates = []

    def table(self, table):
        return _FakeQuery(self, table)

    def rpc(self, *_args, **_kwargs):
        return _Response([])

    def execute(self, query):
        if query.operation == "select" and query.table == "memory_candidates":
            return _Response(
                [
                    {
                        "id": 42,
                        "candidate_type": "identity",
                        "topic": "семья",
                        "content": {"content": "желание иметь семью"},
                        "source_type": "note",
                        "source_id": None,
                        "source_quote": "хочу семью",
                        "confidence": 0.8,
                        "metadata": {},
                    }
                ]
            )
        if query.operation == "select" and query.table == "persona_rules":
            return _Response(
                [
                    {
                        "id": 25,
                        "rule_type": "identity",
                        "topic": "семейная жизнь",
                        "content": "  Желание   иметь семью ",
                    }
                ]
            )
        if query.operation == "upsert":
            self.upserts.append((query.table, query.payload))
        if query.operation == "update":
            self.updates.append((query.table, query.payload))
        return _Response([])


class _DuplicateExperienceClient:
    def __init__(self):
        self.upserts = []
        self.updates = []

    def table(self, table):
        return _FakeQuery(self, table)

    def rpc(self, *_args, **_kwargs):
        return _Response([])

    def execute(self, query):
        if query.operation == "select" and query.table == "memory_candidates":
            return _Response(
                [
                    {
                        "id": 43,
                        "candidate_type": "experience",
                        "topic": "Проекты",
                        "content": {
                            "event_title": "Проекты",
                            "context": "тяжело",
                            "choice_made": "делаю проекты",
                            "consequences_lessons": "становится легче",
                        },
                        "source_type": "note",
                        "source_id": None,
                        "source_quote": "делаю проекты",
                        "confidence": 0.8,
                        "metadata": {},
                    }
                ]
            )
        if query.operation == "select" and query.table == "life_experiences":
            return _Response(
                [
                    {
                        "id": 7,
                        "event_title": "Проекты",
                        "context": "тяжело",
                        "choice_made": "делаю проекты",
                        "consequences_lessons": "становится легче",
                        "is_regret": False,
                    }
                ]
            )
        if query.operation == "upsert":
            self.upserts.append((query.table, query.payload))
        if query.operation == "update":
            self.updates.append((query.table, query.payload))
        return _Response([])


def test_llm_routes_follow_fallback_chain(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("GEMINI_API_KEY", "gem-primary")
    monkeypatch.setenv("GEMINI_API_KEY_FALLBACK", "gem-fallback")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("COHERE_API_KEY", "cohere-key")
    monkeypatch.setenv("COHERE_API_KEY_FALLBACK", "cohere-fallback")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    get_settings.cache_clear()

    labels = [route["label"] for route in _llm_routes()]
    assert labels[0].startswith("gemini/") and "(primary)" in labels[0]
    assert labels[1].startswith("gemini/") and "(fallback)" in labels[1]
    assert labels[2].startswith("groq/")
    assert labels[3].startswith("cohere/") and "(primary)" in labels[3]
    assert labels[4].startswith("cohere/") and "(fallback)" in labels[4]
    assert labels[5].startswith("openrouter/")


def test_llm_routes_can_prefer_groq_for_fast_drafts(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("GEMINI_API_KEY", "gem-primary")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    get_settings.cache_clear()

    labels = [route["label"] for route in _llm_routes("groq_first")]
    assert labels[0].startswith("groq/")
    assert labels[1].startswith("gemini/")


def test_short_and_normal_drafts_use_groq_first_strategy():
    assert _routing_strategy(intent="develop", length_mode="short") == "groq_first"
    assert _routing_strategy(intent="improve", length_mode="normal") == "groq_first"
    assert _routing_strategy(intent="develop", length_mode="expanded") == "default"


def test_settings_are_cached():
    get_settings.cache_clear()
    assert get_settings() is get_settings()


def test_prompt_budget_trims_oversized_user_input():
    system, user = fit_prompts_to_token_budget(
        "system rules",
        "длинный текст " * 500,
        max_tokens=100,
    )
    assert system
    assert user
    assert count_prompt_tokens(system, user) <= 100


def test_retry_delay_is_exponential_and_capped():
    assert _retry_delay(0, base_seconds=5) == 5
    assert _retry_delay(1, base_seconds=5) == 10
    assert _retry_delay(10, base_seconds=5) == 60


def test_session_store_upserts_filtered_payload(monkeypatch):
    client = MagicMock()
    table = client.table.return_value
    upsert = table.upsert.return_value
    upsert.execute.return_value = MagicMock(data=[])
    monkeypatch.setattr(session_store, "get_supabase", lambda: client)

    session_store.save_session(42, {"review_edit": {"id": 1}, "ignored": "secret"})

    payload = table.upsert.call_args.args[0]
    assert payload["user_id"] == 42
    assert payload["payload"] == {"review_edit": {"id": 1}}
    assert table.upsert.call_args.kwargs["on_conflict"] == "user_id"


def test_session_store_uses_memory_fallback_on_db_failure(monkeypatch):
    session_store._fallback_sessions.clear()

    def _unavailable():
        raise RuntimeError("offline")

    monkeypatch.setattr(session_store, "get_supabase", _unavailable)
    session_store.save_session(7, {"mode": "review"})
    assert session_store.load_session(7) == {"mode": "review"}
    session_store.clear_session(7)
    assert session_store.load_session(7) == {}


def test_health_returns_503_when_database_is_unavailable(monkeypatch):
    import main

    def _fail():
        raise RuntimeError("db down")

    monkeypatch.setattr(main, "_database_healthcheck", _fail)
    response = asyncio.run(main.health())
    assert response.status_code == 503


def test_runtime_ingest_has_no_legacy_personality_writer_dependency():
    source = (ROOT / "src" / "orchestrator" / "memory_ingest.py").read_text(
        encoding="utf-8"
    )
    assert "personality_writer" not in source
    assert "core_identity_beliefs" not in source
    assert "life_experience_decisions" not in source


def test_system_prompt_contains_anti_repetition_rule():
    source = (ROOT / "src" / "orchestrator" / "context_builder.py").read_text(
        encoding="utf-8"
    )
    assert "Не повторяй одну и ту же мысль разными словами" in source
    assert "Не используй литературные обороты" in source


def test_accepted_draft_voice_examples_are_low_weight():
    source = (ROOT / "src" / "orchestrator" / "publisher.py").read_text(
        encoding="utf-8"
    )
    assert 'source_type="accepted_draft"' in source
    assert "quality_weight=0.4" in source


def test_generation_callback_is_answered_before_llm(monkeypatch):
    from src.bot import handlers

    events: list[str] = []

    class FakeMessage:
        async def answer(self, *_args, **_kwargs):
            events.append("message")

    class FakeCallback:
        message = FakeMessage()

    async def fake_safe_answer(_callback):
        events.append("answer")

    def fake_get_draft(_draft_id):
        events.append("get_draft")
        return {
            "raw_input": "мысль",
            "intent": "develop",
            "length_mode": "short",
            "journal_id": None,
        }

    def fake_update_draft(*_args, **_kwargs):
        events.append("update")

    async def fake_generate_draft(**_kwargs):
        events.append("generate")
        return "готовый текст", {}

    def fake_add_draft_version(*_args, **_kwargs):
        events.append("version")

    monkeypatch.setattr(handlers, "safe_callback_answer", fake_safe_answer)
    monkeypatch.setattr(handlers, "get_draft", fake_get_draft)
    monkeypatch.setattr(handlers, "update_draft", fake_update_draft)
    monkeypatch.setattr(handlers, "generate_draft", fake_generate_draft)
    monkeypatch.setattr(handlers, "add_draft_version", fake_add_draft_version)

    asyncio.run(handlers._generate_and_send(FakeCallback(), "draft-id", "my_voice"))

    assert events.index("answer") < events.index("generate")


def test_accept_candidate_skips_duplicate_active_rule(monkeypatch):
    client = _DuplicateRuleClient()
    monkeypatch.setattr(memory_repository, "get_supabase", lambda: client)
    monkeypatch.setattr(
        "src.embeddings.gemini.embed_text",
        lambda _text: [0.1, 0.2, 0.3],
    )

    status = memory_repository.accept_review_item("candidate", 42, 123456789)

    assert status == "skipped_duplicate"
    assert client.upserts == []
    assert client.updates[0][0] == "memory_candidates"
    assert client.updates[0][1]["status"] == "skipped"
    assert (
        client.updates[0][1]["metadata"]["skip_reason"]
        == "duplicate_active_persona_rule"
    )


def test_accept_candidate_skips_duplicate_active_experience(monkeypatch):
    client = _DuplicateExperienceClient()
    monkeypatch.setattr(memory_repository, "get_supabase", lambda: client)
    monkeypatch.setattr(
        "src.embeddings.gemini.embed_text",
        lambda _text: [0.1, 0.2, 0.3],
    )

    status = memory_repository.accept_review_item("candidate", 43, 123456789)

    assert status == "skipped_duplicate"
    assert client.upserts == []
    assert client.updates[0][0] == "memory_candidates"
    assert client.updates[0][1]["status"] == "skipped"
    assert (
        client.updates[0][1]["metadata"]["skip_reason"]
        == "duplicate_active_life_experience"
    )
