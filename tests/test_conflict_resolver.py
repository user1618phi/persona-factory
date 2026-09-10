"""Tests for conflict_resolver."""

from __future__ import annotations

from src.orchestrator.conflict_resolver import find_belief_conflicts, resolve_belief_conflicts


def test_resolve_belief_conflicts_picks_newest():
    records = [
        {
            "id": 1,
            "layer_type": "beliefs",
            "topic": "Tone",
            "rules_and_values": "old",
            "updated_at": "2024-01-01T00:00:00Z",
            "is_deprecated": False,
        },
        {
            "id": 2,
            "layer_type": "beliefs",
            "topic": "Tone",
            "rules_and_values": "new",
            "updated_at": "2025-01-01T00:00:00Z",
            "is_deprecated": False,
        },
    ]
    resolved = resolve_belief_conflicts(records)
    assert len(resolved) == 1
    assert resolved[0]["id"] == 2


def test_find_belief_conflicts_detects_losers():
    records = [
        {
            "id": 10,
            "layer_type": "beliefs",
            "topic": "Finance",
            "updated_at": "2024-06-01",
            "is_deprecated": False,
        },
        {
            "id": 11,
            "layer_type": "beliefs",
            "topic": "Finance",
            "updated_at": "2025-06-01",
            "is_deprecated": False,
        },
    ]
    conflicts = find_belief_conflicts(records)
    assert len(conflicts) == 1
    assert conflicts[0]["winner_id"] == 11
    assert 10 in conflicts[0]["loser_ids"]


def test_deprecated_records_excluded():
    records = [
        {
            "id": 1,
            "layer_type": "beliefs",
            "topic": "X",
            "updated_at": "2025-01-01",
            "is_deprecated": True,
        },
        {
            "id": 2,
            "layer_type": "beliefs",
            "topic": "X",
            "updated_at": "2024-01-01",
            "is_deprecated": False,
        },
    ]
    assert len(resolve_belief_conflicts(records)) == 1
    assert resolve_belief_conflicts(records)[0]["id"] == 2
