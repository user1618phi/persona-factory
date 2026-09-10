"""Supabase client factory."""

from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from src.config import get_settings


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    return create_client(settings.supabase_url, settings.supabase_key)
