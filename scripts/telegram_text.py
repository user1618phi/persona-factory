"""Shared utilities for parsing Telegram export JSON."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator


URL_ONLY_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
CREDENTIAL_LIKE_RE = re.compile(
    r"^(password|пароль|pass|логин|login|token|api[_-]?key)\s*[:=]",
    re.IGNORECASE,
)


def flatten_telegram_text(text_field: Any) -> str:
    """Telegram exports text as a string or a list of entities."""
    if text_field is None:
        return ""
    if isinstance(text_field, str):
        return text_field.strip()
    if isinstance(text_field, list):
        parts: list[str] = []
        for item in text_field:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
        return "".join(parts).strip()
    return str(text_field).strip()


def is_noise_message(text: str, *, min_length: int = 15) -> bool:
    if not text:
        return True
    if len(text) < min_length:
        return True
    if URL_ONLY_RE.match(text):
        return True
    if CREDENTIAL_LIKE_RE.match(text):
        return True
    return False


def iter_export_files(export_root: Path) -> Iterator[Path]:
    for path in sorted(export_root.rglob("result.json")):
        if path.is_file():
            yield path


def _repair_json_text(raw: str) -> str:
    """Best-effort fixes for truncated or slightly broken Telegram exports."""
    text = raw.strip()
    if not text:
        return text
    # Drop trailing incomplete key/value fragments after last complete object
    if text.count("{") > text.count("}"):
        last_brace = text.rfind("}")
        if last_brace != -1:
            text = text[: last_brace + 1]
            if not text.endswith("}"):
                text += "}"
    # Close unclosed arrays/objects at end of file
    while text.count("[") > text.count("]"):
        text += "]"
    while text.count("{") > text.count("}"):
        text += "}"
    return text


def load_json(path: Path, *, repair: bool = False) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        raw = handle.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        if not repair:
            raise ValueError(
                f"Invalid JSON in {path}: {exc}. "
                "Re-export from Telegram or run data_cleaner.py with --repair-json."
            ) from exc
        try:
            repaired = _repair_json_text(raw)
            return json.loads(repaired)
        except json.JSONDecodeError as repair_exc:
            raise ValueError(
                f"Invalid JSON in {path}: {exc}. Repair attempt failed: {repair_exc}"
            ) from repair_exc


def iter_chat_sources(payload: dict[str, Any], source_file: str) -> Iterator[dict[str, Any]]:
    """Yield chat dicts from both ChatExport and DataExport formats."""
    if "chats" in payload and isinstance(payload["chats"], dict):
        for chat in payload["chats"].get("list", []):
            yield {
                "name": chat.get("name", "unknown"),
                "type": chat.get("type", "unknown"),
                "id": chat.get("id"),
                "messages": chat.get("messages", []),
                "source_file": source_file,
            }
        return

    if "messages" in payload:
        yield {
            "name": payload.get("name", "unknown"),
            "type": payload.get("type", "unknown"),
            "id": payload.get("id"),
            "messages": payload.get("messages", []),
            "source_file": source_file,
        }
