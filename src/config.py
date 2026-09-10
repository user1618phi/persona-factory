"""Central configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    my_user_id: int
    gemini_api_key: str
    gemini_api_key_fallback: str
    gemini_model: str
    groq_api_key: str
    groq_model: str
    cohere_api_key: str
    cohere_api_key_fallback: str
    cohere_llm_model: str
    openrouter_api_key: str
    openrouter_model: str
    supabase_url: str
    supabase_key: str
    telegram_bot_token: str
    telegram_channel_id: int
    channel_sync_enabled: bool
    channel_min_post_chars: int
    embedding_model: str
    embedding_dimension: int
    recency_lambda: float = 0.002
    knowledge_similarity_threshold: float = 0.62
    knowledge_retrieval_limit: int = 5
    context_max_chars: int = 30_000
    persona_rule_limit: int = 20
    voice_example_limit: int = 6
    experience_limit: int = 4
    llm_max_input_tokens: int = 100_000
    llm_retry_base_seconds: float = 5.0
    youtube_caption_languages: str = "ru,en"
    youtube_audio_chunk_seconds: int = 600
    youtube_max_summary_chars: int = 60_000
    youtube_ingest_timeout_seconds: int = 900
    supadata_api_key: str = ""
    project_root: Path = ROOT


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        my_user_id=int(os.getenv("MY_USER_ID", "0")),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_api_key_fallback=os.getenv("GEMINI_API_KEY_FALLBACK", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        cohere_api_key=os.getenv("COHERE_API_KEY", ""),
        cohere_api_key_fallback=os.getenv("COHERE_API_KEY_FALLBACK", ""),
        cohere_llm_model=os.getenv("COHERE_LLM_MODEL", "command-r-plus-08-2024"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        openrouter_model=os.getenv(
            "OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct"
        ),
        supabase_url=os.getenv("SUPABASE_URL", ""),
        supabase_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_channel_id=int(os.getenv("TELEGRAM_CHANNEL_ID", "0")),
        channel_sync_enabled=os.getenv("CHANNEL_SYNC_ENABLED", "false").lower()
        in ("1", "true", "yes"),
        channel_min_post_chars=int(os.getenv("CHANNEL_MIN_POST_CHARS", "80")),
        embedding_model=os.getenv("EMBEDDING_MODEL", "gemini-embedding-001"),
        embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "768")),
        recency_lambda=float(os.getenv("RECENCY_LAMBDA", "0.002")),
        knowledge_similarity_threshold=float(
            os.getenv("KNOWLEDGE_SIMILARITY_THRESHOLD", "0.62")
        ),
        knowledge_retrieval_limit=int(os.getenv("KNOWLEDGE_RETRIEVAL_LIMIT", "5")),
        context_max_chars=int(os.getenv("CONTEXT_MAX_CHARS", "30000")),
        persona_rule_limit=int(os.getenv("PERSONA_RULE_LIMIT", "20")),
        voice_example_limit=int(os.getenv("VOICE_EXAMPLE_LIMIT", "6")),
        experience_limit=int(os.getenv("EXPERIENCE_LIMIT", "4")),
        llm_max_input_tokens=int(os.getenv("LLM_MAX_INPUT_TOKENS", "100000")),
        llm_retry_base_seconds=float(os.getenv("LLM_RETRY_BASE_SECONDS", "5")),
        youtube_caption_languages=os.getenv("YOUTUBE_CAPTION_LANGUAGES", "ru,en"),
        youtube_audio_chunk_seconds=int(
            os.getenv("YOUTUBE_AUDIO_CHUNK_SECONDS", "600")
        ),
        youtube_max_summary_chars=int(
            os.getenv("YOUTUBE_MAX_SUMMARY_CHARS", "60000")
        ),
        youtube_ingest_timeout_seconds=int(
            os.getenv("YOUTUBE_INGEST_TIMEOUT_SECONDS", "900")
        ),
        supadata_api_key=os.getenv("SUPADATA_API_KEY", ""),
    )
