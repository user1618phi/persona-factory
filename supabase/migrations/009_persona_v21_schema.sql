-- The Persona Factory V2.1: additive memory, drafting and review schema.
-- This migration is intentionally non-destructive. Legacy tables remain available.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS persona_rules (
    id BIGSERIAL PRIMARY KEY,
    rule_type VARCHAR(32) NOT NULL CHECK (
        rule_type IN (
            'identity', 'belief', 'style', 'forbidden_phrase',
            'forbidden_topic', 'content_preference'
        )
    ),
    topic VARCHAR(160) NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(768),
    source_type VARCHAR(32) NOT NULL CHECK (
        source_type IN ('manual', 'note', 'channel_post', 'legacy', 'correction')
    ),
    source_id TEXT,
    source_quote TEXT,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (
        confidence >= 0 AND confidence <= 1
    ),
    status VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'active', 'rejected', 'archived')
    ),
    fingerprint TEXT NOT NULL UNIQUE,
    confirmed_at TIMESTAMPTZ,
    confirmed_by BIGINT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS voice_examples (
    id BIGSERIAL PRIMARY KEY,
    text TEXT NOT NULL,
    embedding VECTOR(768),
    source_type VARCHAR(32) NOT NULL CHECK (
        source_type IN ('telegram_chat', 'channel_post', 'manual', 'accepted_draft')
    ),
    source_external_id TEXT,
    source_chat TEXT,
    occurred_at TIMESTAMPTZ,
    quality_weight DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (
        quality_weight >= 0 AND quality_weight <= 1
    ),
    status VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (
        status IN ('pending', 'active', 'rejected', 'archived')
    ),
    fingerprint TEXT NOT NULL UNIQUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS life_experiences (
    id BIGSERIAL PRIMARY KEY,
    event_title VARCHAR(180) NOT NULL,
    context TEXT NOT NULL,
    choice_made TEXT NOT NULL,
    consequences_lessons TEXT NOT NULL,
    is_regret BOOLEAN NOT NULL DEFAULT FALSE,
    event_timestamp TIMESTAMPTZ,
    embedding VECTOR(768),
    source_type VARCHAR(32) NOT NULL CHECK (
        source_type IN ('manual', 'note', 'channel_post', 'legacy')
    ),
    source_id TEXT,
    source_quote TEXT,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (
        confidence >= 0 AND confidence <= 1
    ),
    status VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'active', 'rejected', 'archived')
    ),
    fingerprint TEXT NOT NULL UNIQUE,
    confirmed_at TIMESTAMPTZ,
    confirmed_by BIGINT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS style_modes (
    id BIGSERIAL PRIMARY KEY,
    slug VARCHAR(64) NOT NULL UNIQUE,
    display_name VARCHAR(120) NOT NULL,
    instruction TEXT NOT NULL,
    source_book VARCHAR(200),
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memory_candidates (
    id BIGSERIAL PRIMARY KEY,
    candidate_type VARCHAR(32) NOT NULL CHECK (
        candidate_type IN (
            'identity', 'belief', 'style', 'forbidden_phrase',
            'forbidden_topic', 'content_preference', 'experience', 'regret'
        )
    ),
    topic VARCHAR(180),
    content JSONB NOT NULL,
    source_type VARCHAR(32) NOT NULL CHECK (
        source_type IN ('note', 'channel_post', 'correction', 'legacy')
    ),
    source_id TEXT,
    source_quote TEXT,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (
        confidence >= 0 AND confidence <= 1
    ),
    status VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'accepted', 'rejected', 'skipped')
    ),
    fingerprint TEXT NOT NULL UNIQUE,
    reviewed_at TIMESTAMPTZ,
    reviewed_by BIGINT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id BIGINT NOT NULL,
    raw_input TEXT NOT NULL,
    intent VARCHAR(32) NOT NULL CHECK (
        intent IN ('improve', 'develop', 'memory', 'style_rule')
    ),
    length_mode VARCHAR(16) NOT NULL DEFAULT 'short' CHECK (
        length_mode IN ('short', 'normal', 'expanded')
    ),
    style_mode VARCHAR(64) NOT NULL DEFAULT 'my_voice'
        REFERENCES style_modes(slug),
    state VARCHAR(24) NOT NULL DEFAULT 'configuring' CHECK (
        state IN (
            'configuring', 'generating', 'drafted', 'correcting',
            'preview', 'publishing', 'published', 'cancelled', 'failed'
        )
    ),
    final_text TEXT,
    pending_instruction TEXT,
    publish_token UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    journal_id BIGINT REFERENCES journal_entries(id),
    published_message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS draft_versions (
    id BIGSERIAL PRIMARY KEY,
    draft_id UUID NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    change_type VARCHAR(24) NOT NULL CHECK (
        change_type IN ('generated', 'alternative', 'rewrite', 'shorter', 'longer', 'manual')
    ),
    instruction TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(draft_id, version_number)
);

CREATE TABLE IF NOT EXISTS generation_runs (
    id BIGSERIAL PRIMARY KEY,
    draft_id UUID REFERENCES drafts(id) ON DELETE SET NULL,
    provider_route TEXT,
    model TEXT,
    duration_ms INTEGER,
    context_chars INTEGER NOT NULL DEFAULT 0,
    persona_rules_count INTEGER NOT NULL DEFAULT 0,
    voice_examples_count INTEGER NOT NULL DEFAULT 0,
    experiences_count INTEGER NOT NULL DEFAULT 0,
    knowledge_chunks_count INTEGER NOT NULL DEFAULT 0,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    error_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE journal_entries
    ADD COLUMN IF NOT EXISTS intent VARCHAR(32),
    ADD COLUMN IF NOT EXISTS processing_state VARCHAR(24) DEFAULT 'received',
    ADD COLUMN IF NOT EXISTS draft_id UUID;

ALTER TABLE bot_published_posts
    ADD COLUMN IF NOT EXISTS draft_id UUID,
    ADD COLUMN IF NOT EXISTS journal_id BIGINT,
    ADD COLUMN IF NOT EXISTS publish_token UUID,
    ADD COLUMN IF NOT EXISTS final_version_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'journal_entries_draft_id_fkey'
    ) THEN
        ALTER TABLE journal_entries
            ADD CONSTRAINT journal_entries_draft_id_fkey
            FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'bot_published_posts_draft_id_fkey'
    ) THEN
        ALTER TABLE bot_published_posts
            ADD CONSTRAINT bot_published_posts_draft_id_fkey
            FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'bot_published_posts_journal_id_fkey'
    ) THEN
        ALTER TABLE bot_published_posts
            ADD CONSTRAINT bot_published_posts_journal_id_fkey
            FOREIGN KEY (journal_id) REFERENCES journal_entries(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'bot_published_posts_final_version_id_fkey'
    ) THEN
        ALTER TABLE bot_published_posts
            ADD CONSTRAINT bot_published_posts_final_version_id_fkey
            FOREIGN KEY (final_version_id) REFERENCES draft_versions(id) ON DELETE SET NULL;
    END IF;
END $$;

ALTER TABLE knowledge_chunks
    ADD COLUMN IF NOT EXISTS canonical_source VARCHAR(200),
    ADD COLUMN IF NOT EXISTS fingerprint TEXT,
    ADD COLUMN IF NOT EXISTS verification_status VARCHAR(16) DEFAULT 'unverified'
        CHECK (verification_status IN ('unverified', 'verified', 'rejected')),
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_knowledge_fingerprint
    ON knowledge_chunks(fingerprint) WHERE fingerprint IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_persona_rules_status_type
    ON persona_rules(status, rule_type);
CREATE INDEX IF NOT EXISTS idx_voice_examples_status_weight
    ON voice_examples(status, quality_weight DESC);
CREATE INDEX IF NOT EXISTS idx_life_experiences_status
    ON life_experiences(status, is_regret);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_review
    ON memory_candidates(status, created_at);
CREATE INDEX IF NOT EXISTS idx_drafts_owner_state
    ON drafts(owner_user_id, state, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_publish_token
    ON bot_published_posts(publish_token) WHERE publish_token IS NOT NULL;

CREATE OR REPLACE FUNCTION match_voice_examples(
    query_embedding VECTOR(768),
    match_count INT DEFAULT 6
)
RETURNS TABLE (
    id BIGINT,
    text TEXT,
    source_type VARCHAR(32),
    source_chat TEXT,
    occurred_at TIMESTAMPTZ,
    quality_weight DOUBLE PRECISION,
    similarity FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT
        ve.id, ve.text, ve.source_type, ve.source_chat, ve.occurred_at,
        ve.quality_weight,
        (1 - (ve.embedding <=> query_embedding))::FLOAT AS similarity
    FROM voice_examples ve
    WHERE ve.status = 'active' AND ve.embedding IS NOT NULL
    ORDER BY ((1 - (ve.embedding <=> query_embedding)) * ve.quality_weight) DESC
    LIMIT match_count;
$$;

CREATE OR REPLACE FUNCTION match_persona_rules(
    query_embedding VECTOR(768),
    match_count INT DEFAULT 12
)
RETURNS TABLE (
    id BIGINT,
    rule_type VARCHAR(32),
    topic VARCHAR(160),
    content TEXT,
    similarity FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT
        pr.id, pr.rule_type, pr.topic, pr.content,
        (1 - (pr.embedding <=> query_embedding))::FLOAT AS similarity
    FROM persona_rules pr
    WHERE pr.status = 'active' AND pr.embedding IS NOT NULL
    ORDER BY pr.embedding <=> query_embedding
    LIMIT match_count;
$$;

CREATE OR REPLACE FUNCTION match_life_experiences(
    query_embedding VECTOR(768),
    match_count INT DEFAULT 4,
    include_regrets BOOLEAN DEFAULT TRUE
)
RETURNS TABLE (
    id BIGINT,
    event_title VARCHAR(180),
    context TEXT,
    choice_made TEXT,
    consequences_lessons TEXT,
    is_regret BOOLEAN,
    event_timestamp TIMESTAMPTZ,
    similarity FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT
        le.id, le.event_title, le.context, le.choice_made,
        le.consequences_lessons, le.is_regret, le.event_timestamp,
        (1 - (le.embedding <=> query_embedding))::FLOAT AS similarity
    FROM life_experiences le
    WHERE le.status = 'active'
      AND le.embedding IS NOT NULL
      AND (include_regrets OR le.is_regret = FALSE)
    ORDER BY le.embedding <=> query_embedding
    LIMIT match_count;
$$;

CREATE OR REPLACE FUNCTION match_knowledge_chunks_v21(
    query_embedding VECTOR(768),
    match_count INT DEFAULT 3,
    verified_only BOOLEAN DEFAULT FALSE
)
RETURNS TABLE (
    id BIGINT,
    content TEXT,
    category VARCHAR(50),
    source_book VARCHAR(200),
    canonical_source VARCHAR(200),
    chapter_title VARCHAR(300),
    verification_status VARCHAR(16),
    created_at TIMESTAMPTZ,
    similarity FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT
        kc.id, kc.content, kc.category, kc.source_book, kc.canonical_source,
        kc.chapter_title, kc.verification_status, kc.created_at,
        (1 - (kc.embedding <=> query_embedding))::FLOAT AS similarity
    FROM knowledge_chunks kc
    WHERE kc.embedding IS NOT NULL
      AND (NOT verified_only OR kc.verification_status = 'verified')
    ORDER BY kc.embedding <=> query_embedding
    LIMIT match_count;
$$;

INSERT INTO style_modes (slug, display_name, instruction, source_book, is_default)
VALUES
    (
        'my_voice',
        'Только мой голос',
        'Не добавляй внешнюю стилизацию. Следуй только активным правилам и реальным примерам речи автора.',
        NULL,
        TRUE
    ),
    (
        'clear',
        'Кратко и ясно',
        'Используй информационный стиль: убирай воду, штампы и лишний пафос; ставь смысл и конкретику впереди формы.',
        'Пиши, сокращай',
        FALSE
    ),
    (
        'storytelling',
        'Storytelling',
        'Строй текст вокруг одной реальной ситуации, напряжения, выбора и вывода. Не выдумывай события и детали.',
        'The Story Factor',
        FALSE
    ),
    (
        'show_your_work',
        'Покажи процесс',
        'Показывай конкретный процесс, промежуточный результат или урок из текущей работы без саморекламы и искусственного пафоса.',
        'Покажи свою работу',
        FALSE
    )
ON CONFLICT (slug) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    instruction = EXCLUDED.instruction,
    source_book = EXCLUDED.source_book,
    is_default = EXCLUDED.is_default,
    enabled = TRUE,
    updated_at = NOW();

ALTER TABLE persona_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE voice_examples ENABLE ROW LEVEL SECURITY;
ALTER TABLE life_experiences ENABLE ROW LEVEL SECURITY;
ALTER TABLE style_modes ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE draft_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE generation_runs ENABLE ROW LEVEL SECURITY;
