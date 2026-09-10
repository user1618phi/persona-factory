from scripts.backup_supabase import TABLES


def test_backup_covers_legacy_and_v21_tables():
    assert set(TABLES) == {
        "knowledge_chunks",
        "core_identity_beliefs",
        "life_experience_decisions",
        "style_patterns",
        "journal_entries",
        "channel_posts_processed",
        "bot_published_posts",
        "persona_rules",
        "voice_examples",
        "life_experiences",
        "style_modes",
        "memory_candidates",
        "drafts",
        "draft_versions",
        "generation_runs",
    }
