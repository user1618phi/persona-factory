"""Tests for regret import script safety."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

from scripts import import_regrets


def test_import_regrets_uses_schema_allowed_source_type(tmp_path: Path):
    payload = [
        {
            "event_title": "Ошибка в проекте",
            "source_quote": "Я слишком поздно попросил помощь.",
            "confidence": 0.9,
        }
    ]
    source = tmp_path / "regrets.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    created = []

    def create(**kwargs):
        created.append(kwargs)
        return {"id": 1}

    with (
        patch.object(sys, "argv", ["import_regrets.py", str(source)]),
        patch("scripts.import_regrets.create_memory_candidate", side_effect=create),
    ):
        assert import_regrets.main() == 0

    assert created[0]["source_type"] == "note"
    assert created[0]["metadata"]["origin"] == "manual_regret_import"
