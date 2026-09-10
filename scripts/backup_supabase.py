#!/usr/bin/env python3
"""Export the application schema through Supabase REST before production changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.project_runtime import ensure_project_venv  # noqa: E402

ensure_project_venv(__file__, ROOT)

import httpx  # noqa: E402

from src.config import get_settings  # noqa: E402

TABLES = (
    "knowledge_chunks",
    "core_identity_beliefs",
    "life_experience_decisions",
    "style_patterns",
    "journal_entries",
    "channel_posts_processed",
    "bot_published_posts",
    "persona_rules",
    "voice_examples",
    "life_experiences",
    "style_modes",
    "memory_candidates",
    "drafts",
    "draft_versions",
    "generation_runs",
)


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("wb") as handle:
        for row in rows:
            line = (
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=_json_default,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            handle.write(line)
            digest.update(line)
    return {"rows": len(rows), "sha256": digest.hexdigest(), "file": path.name}


def _fetch_all(client: httpx.Client, base_url: str, table: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    page_size = 1000
    while True:
        response = client.get(
            f"{base_url}/rest/v1/{table}",
            params={"select": "*", "order": "id.asc", "offset": offset, "limit": page_size},
        )
        response.raise_for_status()
        page = response.json()
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def create_backup(output_root: Path) -> Path:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_key:
        raise RuntimeError("Supabase credentials are not configured")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = output_root / stamp
    output_dir.mkdir(parents=True, exist_ok=False)

    headers = {
        "apikey": settings.supabase_key,
        "Authorization": f"Bearer {settings.supabase_key}",
        "Accept": "application/json",
    }
    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "supabase_url": settings.supabase_url,
        "tables": {},
    }

    with httpx.Client(headers=headers, timeout=120.0) as client:
        schema_response = client.get(
            f"{settings.supabase_url.rstrip('/')}/rest/v1/",
            headers={**headers, "Accept": "application/openapi+json"},
        )
        schema_response.raise_for_status()
        schema_path = output_dir / "openapi.json"
        schema_bytes = json.dumps(
            schema_response.json(), ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8")
        schema_path.write_bytes(schema_bytes)
        manifest["openapi_sha256"] = hashlib.sha256(schema_bytes).hexdigest()

        for table in TABLES:
            rows = _fetch_all(client, settings.supabase_url.rstrip("/"), table)
            manifest["tables"][table] = _write_jsonl(output_dir / f"{table}.jsonl", rows)

    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8")
    (output_dir / "manifest.json").write_bytes(manifest_bytes)
    (output_dir / "manifest.sha256").write_text(
        hashlib.sha256(manifest_bytes).hexdigest() + "\n", encoding="utf-8"
    )
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up production Supabase tables.")
    parser.add_argument("--output-root", type=Path, default=Path("backups/supabase"))
    args = parser.parse_args()
    output = create_backup(args.output_root)
    print(output)


if __name__ == "__main__":
    main()
