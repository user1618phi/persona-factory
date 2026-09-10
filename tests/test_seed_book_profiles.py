"""Tests for book profile seeding topic uniqueness."""

from __future__ import annotations

from scripts.seed_book_profiles import _belief_topic
from src.orchestrator.conflict_resolver import resolve_belief_conflicts


def test_belief_topics_are_unique_per_index():
    book = "the story factor"
    topics = [_belief_topic(book, index) for index in range(1, 4)]
    assert len(set(topics)) == 3
    assert topics[0] == "the story factor: убеждение #1"
    assert topics[2] == "the story factor: убеждение #3"


def test_resolve_belief_conflicts_keeps_all_book_beliefs():
    book = "the story factor"
    records = [
        {
            "id": index,
            "layer_type": "beliefs",
            "topic": _belief_topic(book, index),
            "rules_and_values": f"belief {index}",
            "source_book": book,
            "updated_at": "2025-01-01T00:00:00Z",
            "is_deprecated": False,
        }
        for index in range(1, 41)
    ]
    resolved = resolve_belief_conflicts(records)
    assert len(resolved) == 40
