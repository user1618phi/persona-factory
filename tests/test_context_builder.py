"""Tests for context_builder factual-knowledge gating."""

from __future__ import annotations

from src.orchestrator.context_builder import needs_factual_knowledge


def test_needs_factual_knowledge_keyword_match():
    assert needs_factual_knowledge("Что такое халял?", []) is True


def test_needs_factual_knowledge_similarity_alone_skips():
    rows = [{"similarity": 0.75}]
    assert needs_factual_knowledge("быть правым и быть услышанным", rows) is False


def test_needs_factual_knowledge_technical_keyword_match():
    assert needs_factual_knowledge("как работает api telegram?", []) is True


def test_needs_factual_knowledge_low_similarity_skips():
    rows = [{"similarity": 0.4}]
    assert needs_factual_knowledge("расскажи про жизнь", rows) is False


def test_needs_factual_knowledge_empty_rows():
    assert needs_factual_knowledge("расскажи про жизнь", []) is False
