"""Compatibility wrapper for the review-gated V2.1 style learning loop."""

from __future__ import annotations

from typing import Any

from src.orchestrator.learning import create_style_correction_candidate


def apply_style_correction(
    instruction: str,
    *,
    draft_id: str = "legacy",
) -> dict[str, Any]:
    """Create a pending candidate; never mutate active style directly."""
    candidate = create_style_correction_candidate(instruction, draft_id=draft_id)
    return {
        "instruction": instruction,
        "candidate_id": candidate.get("id") if candidate else None,
        "status": "pending",
    }
