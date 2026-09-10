"""Resumable, rate-limited voice corpus import."""

from unittest.mock import MagicMock

from scripts.import_voice_examples import (
    ROOT,
    _existing_fingerprints,
    import_records,
)
from scripts.lib.project_runtime import ensure_project_venv
from src.db.memory_repository import fingerprint


def test_dry_run_reports_only_remaining_rows(monkeypatch):
    records = [{"id": 1, "text": "already"}, {"id": 2, "text": "new"}]
    monkeypatch.setattr(
        "scripts.import_voice_examples._existing_fingerprints",
        lambda: {fingerprint("telegram_chat", "already")},
    )
    result = import_records(
        records,
        source_type="telegram_chat",
        quality_weight=0.45,
        batch_size=20,
        batch_sleep=0,
        rate_limit_wait=0,
        max_rate_limit_retries=1,
        wait_on_quota=False,
        dry_run=True,
    )
    assert result == {
        "remaining": 1,
        "skipped_existing_or_duplicate": 1,
        "inserted": 0,
    }


def test_rate_limit_retries_same_batch_without_losing_progress(monkeypatch):
    records = [{"id": 1, "text": "one"}, {"id": 2, "text": "two"}]
    monkeypatch.setattr(
        "scripts.import_voice_examples._existing_fingerprints", lambda: set()
    )
    calls = {"count": 0}

    def _embed(texts):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("429 Too Many Requests")
        return [[0.1] * 768 for _ in texts]

    monkeypatch.setattr("scripts.import_voice_examples.embed_texts", _embed)
    monkeypatch.setattr("scripts.import_voice_examples.time.sleep", lambda _: None)
    client = MagicMock()
    client.table.return_value.upsert.return_value.execute.return_value = MagicMock(
        data=[{"id": 1}, {"id": 2}]
    )
    monkeypatch.setattr("scripts.import_voice_examples.get_supabase", lambda: client)

    result = import_records(
        records,
        source_type="telegram_chat",
        quality_weight=0.45,
        batch_size=20,
        batch_sleep=0,
        rate_limit_wait=0,
        max_rate_limit_retries=2,
        wait_on_quota=True,
        dry_run=False,
    )
    assert calls["count"] == 2
    assert result["inserted"] == 2


def test_default_quota_behavior_stops_cleanly(monkeypatch):
    records = [{"id": 1, "text": "one"}]
    monkeypatch.setattr(
        "scripts.import_voice_examples._existing_fingerprints", lambda: set()
    )
    monkeypatch.setattr(
        "scripts.import_voice_examples.embed_texts",
        MagicMock(side_effect=RuntimeError("429 Too Many Requests")),
    )
    result = import_records(
        records,
        source_type="telegram_chat",
        quality_weight=0.45,
        batch_size=20,
        batch_sleep=0,
        rate_limit_wait=0,
        max_rate_limit_retries=20,
        wait_on_quota=False,
        dry_run=False,
    )
    assert result["status"] == "quota_exhausted"
    assert result["remaining"] == 1
    assert result["inserted"] == 0


def test_global_python_reexecs_project_venv(monkeypatch):
    expected_venv = ROOT / ".venv"
    monkeypatch.delenv("PERSONA_SKIP_VENV_CHECK", raising=False)
    monkeypatch.setattr("scripts.lib.project_runtime.sys.prefix", "/global/python")
    monkeypatch.setattr(
        "scripts.lib.project_runtime.sys.argv",
        ["scripts/import_voice_examples.py", "--dry-run"],
    )
    called = {}

    def _execv(executable, argv):
        called["executable"] = executable
        called["argv"] = argv

    monkeypatch.setattr("scripts.lib.project_runtime.os.execv", _execv)
    monkeypatch.setattr(
        "scripts.lib.project_runtime.Path.exists",
        lambda path: path == expected_venv / "bin" / "python",
    )
    ensure_project_venv("scripts/import_voice_examples.py", ROOT)
    assert called["executable"] == str(expected_venv / "bin" / "python")
    assert called["argv"][-1] == "--dry-run"


def test_existing_fingerprints_reads_every_postgrest_page(monkeypatch):
    pages = [
        [{"fingerprint": f"fp-{index}"} for index in range(1000)],
        [{"fingerprint": "fp-1000"}, {"fingerprint": "fp-1001"}],
    ]
    query = MagicMock()
    query.select.return_value = query
    query.order.return_value = query
    query.range.return_value = query
    query.execute.side_effect = [
        MagicMock(data=pages[0]),
        MagicMock(data=pages[1]),
    ]
    client = MagicMock()
    client.table.return_value = query
    monkeypatch.setattr("scripts.import_voice_examples.get_supabase", lambda: client)

    result = _existing_fingerprints()
    assert len(result) == 1002
    assert "fp-1001" in result
    assert query.range.call_args_list[0].args == (0, 999)
    assert query.range.call_args_list[1].args == (1000, 1999)
