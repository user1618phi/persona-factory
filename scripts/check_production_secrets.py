#!/usr/bin/env python3
"""Check that production secrets are configured (does not rotate keys)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

PLACEHOLDER_MARKERS = (
    "your_",
    "change_me",
    "replace_me",
    "example",
    "xxx",
)

REQUIRED = (
    "MY_USER_ID",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHANNEL_ID",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "COHERE_API_KEY",
)

RECOMMENDED = (
    "GEMINI_API_KEY_FALLBACK",
    "OPENROUTER_API_KEY",
)

RAILWAY_REQUIRED = (
    *REQUIRED,
    "STUDIO_API_SECRET",
    "SUPADATA_API_KEY",
)


def _load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _looks_placeholder(value: str) -> bool:
    lowered = value.lower()
    return not value or any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _railway_variables(*, service: str | None, environment: str | None) -> dict[str, str]:
    command = ["railway", "variable", "list", "--json"]
    if service:
        command.extend(["--service", service])
    if environment:
        command.extend(["--environment", environment])
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Railway CLI is not installed or not in PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"Railway variable list failed: {detail}") from exc

    payload = json.loads(result.stdout or "{}")
    if isinstance(payload, dict):
        return {str(key): str(value) for key, value in payload.items()}
    if isinstance(payload, list):
        variables: dict[str, str] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            key = item.get("name") or item.get("key")
            value = item.get("value")
            if key is not None:
                variables[str(key)] = "" if value is None else str(value)
        return variables
    raise RuntimeError("Unexpected Railway JSON payload")


def _check_values(
    values: dict[str, str],
    *,
    required: tuple[str, ...],
    recommended: tuple[str, ...],
    channel_sync_required: bool,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for key in required:
        value = values.get(key, "")
        if _looks_placeholder(value):
            errors.append(f"MISSING or placeholder: {key}")

    for key in recommended:
        value = values.get(key, "")
        if _looks_placeholder(value):
            warnings.append(f"Recommended not set: {key}")

    if values.get("CHANNEL_SYNC_ENABLED", "false").lower() not in ("1", "true", "yes"):
        message = "CHANNEL_SYNC_ENABLED is not true — channel post learning is OFF"
        if channel_sync_required:
            errors.append(message)
        else:
            warnings.append(message)

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check local or Railway production secrets without printing values."
    )
    parser.add_argument(
        "--railway",
        action="store_true",
        help="Check Railway variables instead of local .env/environment",
    )
    parser.add_argument("--service", default=None, help="Railway service name")
    parser.add_argument("--environment", default=None, help="Railway environment name")
    args = parser.parse_args()

    if args.railway:
        try:
            values = _railway_variables(
                service=args.service,
                environment=args.environment,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        title = "Railway production secrets check"
        required = RAILWAY_REQUIRED
        channel_sync_required = True
    else:
        _load_dotenv()
        values = dict(os.environ)
        title = "Local env secrets check"
        required = REQUIRED
        channel_sync_required = False

    errors, warnings = _check_values(
        values,
        required=required,
        recommended=RECOMMENDED,
        channel_sync_required=channel_sync_required,
    )

    print(title)
    print("=" * 40)
    if errors:
        for item in errors:
            print(f"ERROR: {item}")
    else:
        print("Required secrets: OK")
    for item in warnings:
        print(f"WARN: {item}")

    if errors:
        print("\nSee docs/SECRETS_ROTATION_CHECKLIST.md")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
