"""Ensure every committed migration is valid PostgreSQL syntax."""

from pathlib import Path

from pglast import parse_sql

ROOT = Path(__file__).resolve().parents[1]


def test_all_migrations_parse():
    files = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))
    files += sorted((ROOT / "supabase" / "post_cleanup").glob("*.sql"))
    assert files
    for path in files:
        parse_sql(path.read_text(encoding="utf-8"))


def test_audit_hardening_migration_covers_sessions_rls_and_vectors():
    path = next(
        (ROOT / "supabase" / "migrations").glob("*_audit_reliability_hardening.sql")
    )
    sql = path.read_text(encoding="utf-8").lower()
    assert "create table if not exists public.bot_sessions" in sql
    assert "alter table public.bot_sessions enable row level security" in sql
    assert sql.count("using hnsw") == 4
    assert "vector_cosine_ops" in sql
    assert "with nearest as materialized" in sql
    assert "drop index if exists public.idx_knowledge_chunks_embedding_ivfflat" in sql


def test_runtime_post_review_migration_covers_audit_and_generation_metadata():
    path = next(
        (ROOT / "supabase" / "migrations").glob("*_runtime_post_review_hardening.sql")
    )
    sql = path.read_text(encoding="utf-8").lower()
    assert "add column if not exists routing_strategy" in sql
    assert "add column if not exists primary_provider_failed" in sql
    assert "create table if not exists public.persona_cleanup_audit" in sql
    assert "alter table public.persona_cleanup_audit enable row level security" in sql
    assert "grant all on public.persona_cleanup_audit to service_role" in sql


def test_knowledge_verification_migration_indexes_queue_and_archives_rule_duplicates():
    path = next(
        (ROOT / "supabase" / "migrations").glob(
            "*_knowledge_verification_and_rule_dedup.sql"
        )
    )
    sql = path.read_text(encoding="utf-8").lower()
    assert "idx_knowledge_chunks_verification_queue" in sql
    assert "verification_status, category, created_at desc" in sql
    assert "partition by" in sql
    assert "regexp_replace(lower(trim(content))" in sql
    assert "duplicate_active_rule_content" in sql
    assert "source_type = 'accepted_draft'" in sql
    assert "quality_weight = 0.4" in sql
    assert "content_refined_by" in sql


def test_bot_ops_events_migration_is_server_only():
    path = next((ROOT / "supabase" / "migrations").glob("*_bot_ops_events.sql"))
    sql = path.read_text(encoding="utf-8").lower()
    assert "create table if not exists public.bot_ops_events" in sql
    assert "alter table public.bot_ops_events enable row level security" in sql
    assert "create policy deny_client_access on public.bot_ops_events" in sql
    assert "grant select, insert, update, delete on table public.bot_ops_events" in sql
    assert "idx_bot_ops_events_type_created" in sql
