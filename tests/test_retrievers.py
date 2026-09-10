"""Tests for recency_score."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.db.retrievers import recency_score


def test_recency_score_no_timestamp_returns_similarity():
    assert recency_score(0.9, None, lambda_coef=0.002) == 0.9


def test_recency_score_recent_record_higher_than_old():
    now = datetime.now(timezone.utc)
    recent = recency_score(0.8, now - timedelta(days=1), lambda_coef=0.002)
    old = recency_score(0.8, now - timedelta(days=365), lambda_coef=0.002)
    assert recent > old


def test_recency_score_zero_age_near_similarity():
    now = datetime.now(timezone.utc)
    score = recency_score(0.75, now, lambda_coef=0.002)
    assert abs(score - 0.75) < 0.001
