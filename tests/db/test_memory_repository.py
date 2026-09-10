"""Tests for memory_repository."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from src.db import memory_repository


def test_experience_text_builder():
    row = {
        "event_title": "Title",
        "context": "ctx",
        "choice_made": "ch",
        "consequences_lessons": "lessons"
    }
    text = memory_repository._experience_text(row)
    assert text == "Title\nctx\nch\nlessons"


def test_experience_text_builder_missing_fields():
    row = {
        "topic": "Fallback Topic"
    }
    text = memory_repository._experience_text(row)
    assert text == "Fallback Topic\n\n\n"


def test_find_active_experience_duplicate_exact(monkeypatch):
    client = MagicMock()
    table = client.table.return_value
    select = table.select.return_value
    eq1 = select.eq.return_value
    eq2 = eq1.eq.return_value
    eq2.execute.return_value = MagicMock(
        data=[
            {
                "id": 1,
                "event_title": "Title",
                "context": "ctx",
                "choice_made": "ch",
                "consequences_lessons": "lessons",
                "is_regret": False
            }
        ]
    )

    row = {
        "event_title": "  Title ",
        "context": " ctx",
        "choice_made": "ch",
        "consequences_lessons": " lessons  "
    }

    dup = memory_repository._find_active_experience_duplicate(
        client,
        row=row,
        embedding=None,
        is_regret=False,
    )
    assert dup is not None
    assert dup["match_type"] == "exact_experience"
    assert dup["duplicate_of_experience_id"] == 1


def test_find_active_experience_duplicate_semantic(monkeypatch):
    client = MagicMock()
    table = client.table.return_value
    select = table.select.return_value
    eq1 = select.eq.return_value
    eq2 = eq1.eq.return_value
    eq2.execute.return_value = MagicMock(data=[])

    client.rpc.return_value.execute.return_value = MagicMock(
        data=[
            {
                "id": 2,
                "event_title": "Semantic Match",
                "is_regret": False,
                "similarity": 0.95
            }
        ]
    )

    row = {"event_title": "New", "context": "", "choice_made": "", "consequences_lessons": ""}

    dup = memory_repository._find_active_experience_duplicate(
        client,
        row=row,
        embedding=[0.1, 0.2, 0.3],
        is_regret=False,
    )

    assert dup is not None
    assert dup["match_type"] == "semantic_experience_similarity"
    assert dup["duplicate_of_experience_id"] == 2
    assert dup["similarity"] == 0.95
