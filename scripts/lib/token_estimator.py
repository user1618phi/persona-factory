"""Rough token estimation for chunk sizing."""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    try:
        import tiktoken

        encoder = tiktoken.get_encoding("cl100k_base")
        return len(encoder.encode(text))
    except Exception:
        return max(1, len(text) // 4)
