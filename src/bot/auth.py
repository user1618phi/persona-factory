"""Shared Telegram owner authorization."""

from __future__ import annotations

from src.config import get_settings


def is_owner(user_id: int | None) -> bool:
    return bool(user_id and user_id == get_settings().my_user_id)
