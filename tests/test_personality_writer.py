"""Tests for personality_writer."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.db.personality_writer import (
    belief_text_differs,
    insert_identity_item,
    seed_profile_data,
)


def test_belief_text_differs_detects_change():
    assert belief_text_differs("old value", "completely new value")
    assert not belief_text_differs("same", "same")
    assert not belief_text_differs("longer text here", "longer")


def test_insert_identity_skips_duplicate():
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table
    select = MagicMock()
    table.select.return_value = select
    select.eq.return_value = select
    select.limit.return_value = select
    select.execute.return_value = MagicMock(
        data=[{"id": 1, "rules_and_values": "existing", "layer_type": "identity", "topic": "Mission"}]
    )

    result = insert_identity_item(
        client,
        {"topic": "Mission", "rules_and_values": "existing"},
        allow_update=False,
    )
    assert result == "skipped"
    table.insert.assert_not_called()


def test_seed_profile_data_counts_inserts(monkeypatch):
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table
    select = MagicMock()
    table.select.return_value = select
    select.eq.return_value = select
    select.limit.return_value = select
    select.execute.return_value = MagicMock(data=[])

    insert = MagicMock()
    table.insert.return_value = insert
    insert.execute.return_value = MagicMock(data=[{"id": 1}])

    monkeypatch.setattr("src.db.personality_writer.get_supabase", lambda: client)
    monkeypatch.setattr("src.db.personality_writer.embed_text", lambda text: [0.1] * 3)

    profile = {
        "identity": [{"topic": "Goals", "rules_and_values": "Build products"}],
        "beliefs": [],
        "decisions": [],
        "regrets": [],
        "style": {},
    }
    report = seed_profile_data(profile)
    assert report["inserted"]["identity"] == 1
