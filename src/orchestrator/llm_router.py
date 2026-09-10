"""LiteLLM routing with task-aware provider preference."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import tiktoken
from litellm import completion

from src.config import get_settings

logger = logging.getLogger(__name__)

LLM_UNAVAILABLE_MESSAGE = (
    "⚠️ Все LLM-шлюзы временно недоступны "
    "(Gemini, запасной Gemini, Groq, Cohere, OpenRouter). "
    "Попробуйте через 5–10 минут. Если ошибка повторяется — проверьте API-ключи и лимиты."
)


class LLMRouterError(RuntimeError):
    """Raised when every model in the fallback chain fails."""

    def __init__(
        self, message: str = LLM_UNAVAILABLE_MESSAGE, *, cause: Exception | None = None
    ):
        super().__init__(message)
        self.cause = cause


@dataclass(frozen=True)
class GeneratedText:
    text: str
    route: str
    model: str
    duration_ms: int
    routing_strategy: str = "default"
    primary_provider_failed: bool = False


def _encoding():
    return tiktoken.get_encoding("o200k_base")


def count_prompt_tokens(system_prompt: str, user_prompt: str) -> int:
    encoding = _encoding()
    return len(encoding.encode(system_prompt)) + len(encoding.encode(user_prompt))


def fit_prompts_to_token_budget(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
) -> tuple[str, str]:
    """Keep prompts under the configured input budget, preferring system context."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    encoding = _encoding()
    system_tokens = encoding.encode(system_prompt)
    user_tokens = encoding.encode(user_prompt)
    if len(system_tokens) + len(user_tokens) <= max_tokens:
        return system_prompt, user_prompt

    def fit(tokens: list[int], budget: int) -> str:
        if len(tokens) <= budget:
            return encoding.decode(tokens)
        if budget <= 1:
            return encoding.decode(tokens[:budget])
        head_size = max(1, int(budget * 0.6))
        tail_size = max(0, budget - head_size)
        return encoding.decode(tokens[:head_size] + tokens[-tail_size:]).rstrip()

    system_budget = min(len(system_tokens), max(1, int(max_tokens * 0.8)))
    fitted_system = fit(system_tokens, system_budget)
    remaining = max(0, max_tokens - len(encoding.encode(fitted_system)))
    fitted_user = fit(user_tokens, remaining)
    logger.warning(
        "LLM prompt trimmed to token budget",
        extra={
            "original_tokens": len(system_tokens) + len(user_tokens),
            "max_input_tokens": max_tokens,
        },
    )
    return fitted_system, fitted_user


def _retry_delay(attempt: int, *, base_seconds: float) -> float:
    return min(base_seconds * (2**attempt), 60.0)


def _llm_routes(routing_strategy: str = "default") -> list[dict[str, Any]]:
    settings = get_settings()
    gemini_routes: list[dict[str, Any]] = []
    groq_routes: list[dict[str, Any]] = []
    cohere_routes: list[dict[str, Any]] = []
    openrouter_routes: list[dict[str, Any]] = []
    gemini_keys: list[tuple[str, str]] = []
    if settings.gemini_api_key:
        gemini_keys.append(("primary", settings.gemini_api_key))
    if settings.gemini_api_key_fallback:
        gemini_keys.append(("fallback", settings.gemini_api_key_fallback))

    for slot, api_key in gemini_keys:
        gemini_routes.append(
            {
                "label": f"gemini/{settings.gemini_model} ({slot})",
                "model": f"gemini/{settings.gemini_model}",
                "api_key": api_key,
            }
        )

    if settings.groq_api_key:
        groq_routes.append(
            {
                "label": f"groq/{settings.groq_model}",
                "model": f"groq/{settings.groq_model}",
                "api_key": settings.groq_api_key,
            }
        )

    if settings.cohere_api_key:
        cohere_keys: list[tuple[str, str]] = [("primary", settings.cohere_api_key)]
        if settings.cohere_api_key_fallback:
            cohere_keys.append(("fallback", settings.cohere_api_key_fallback))
        for slot, api_key in cohere_keys:
            cohere_routes.append(
                {
                    "label": f"cohere/{settings.cohere_llm_model} ({slot})",
                    "model": f"cohere/{settings.cohere_llm_model}",
                    "api_key": api_key,
                }
            )

    if settings.openrouter_api_key:
        openrouter_routes.append(
            {
                "label": f"openrouter/{settings.openrouter_model}",
                "model": f"openrouter/{settings.openrouter_model}",
                "api_key": settings.openrouter_api_key,
            }
        )
    if routing_strategy == "groq_first":
        return groq_routes + gemini_routes + cohere_routes + openrouter_routes
    return gemini_routes + groq_routes + cohere_routes + openrouter_routes


async def generate_text(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_retries: int = 3,
    routing_strategy: str = "default",
) -> str:
    result = await generate_text_with_meta(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        max_retries=max_retries,
        routing_strategy=routing_strategy,
    )
    return result.text


async def generate_text_with_meta(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_retries: int = 3,
    routing_strategy: str = "default",
) -> GeneratedText:
    settings = get_settings()
    system_prompt, user_prompt = fit_prompts_to_token_budget(
        system_prompt,
        user_prompt,
        max_tokens=settings.llm_max_input_tokens,
    )
    models = _llm_routes(routing_strategy)
    if not models:
        raise LLMRouterError(
            "Нет настроенных LLM API-ключей "
            "(GEMINI_API_KEY, GROQ_API_KEY, COHERE_API_KEY или OPENROUTER_API_KEY)."
        )

    last_error: Exception | None = None
    primary_provider_failed = False

    for attempt in range(max_retries):
        for route_index, route in enumerate(models):
            started = time.perf_counter()
            try:
                response = await asyncio.to_thread(
                    completion,
                    model=route["model"],
                    api_key=route["api_key"],
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                )
                content = response.choices[0].message.content
                if content:
                    return GeneratedText(
                        text=content.strip(),
                        route=route["label"],
                        model=route["model"],
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        routing_strategy=routing_strategy,
                        primary_provider_failed=primary_provider_failed,
                    )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == 0 and route_index == 0:
                    primary_provider_failed = True
                logger.warning("LLM route %s failed: %s", route["label"], exc)

        if attempt < max_retries - 1:
            await asyncio.sleep(
                _retry_delay(
                    attempt,
                    base_seconds=settings.llm_retry_base_seconds,
                )
            )

    raise LLMRouterError(cause=last_error)
