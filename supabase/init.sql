-- The Persona Factory — Supabase schema (Phase 2)
-- Run in Supabase SQL Editor

CREATE EXTENSION IF NOT EXISTS vector;

-- Knowledge layer (books, docs)
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id BIGSERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    embedding VECTOR(768),
    category VARCHAR(50) NOT NULL CHECK (category IN ('islam', 'finance', 'it', 'psychology', 'branding')),
    source_book VARCHAR(200),
    chapter_title VARCHAR(300),
    chunk_index INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Identity + Beliefs layers
CREATE TABLE IF NOT EXISTS core_identity_beliefs (
    id BIGSERIAL PRIMARY KEY,
    layer_type VARCHAR(20) NOT NULL CHECK (layer_type IN ('identity', 'beliefs')),
    topic VARCHAR(100) NOT NULL,
    rules_and_values TEXT NOT NULL,
    source_book VARCHAR(200),
    is_deprecated BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Experience, decisions, regrets
CREATE TABLE IF NOT EXISTS life_experience_decisions (
    id BIGSERIAL PRIMARY KEY,
    event_title VARCHAR(150) NOT NULL,
    context TEXT NOT NULL,
    choice_made TEXT NOT NULL,
    consequences_lessons TEXT NOT NULL,
    is_regret BOOLEAN DEFAULT FALSE,
    event_timestamp TIMESTAMPTZ,
    embedding VECTOR(768),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Style layer
CREATE TABLE IF NOT EXISTS style_patterns (
    id BIGSERIAL PRIMARY KEY,
    pattern_type VARCHAR(50) NOT NULL,
    data_payload JSONB NOT NULL,
    confidence_score FLOAT NOT NULL DEFAULT 0.5,
    source_book VARCHAR(200),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Vector indexes (run after initial data load for better IVFFlat performance)
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_category ON knowledge_chunks(category);
CREATE INDEX IF NOT EXISTS idx_core_identity_layer ON core_identity_beliefs(layer_type);
CREATE INDEX IF NOT EXISTS idx_life_experience_regret ON life_experience_decisions(is_regret);

-- Similarity search: knowledge
CREATE OR REPLACE FUNCTION match_knowledge_chunks(
    query_embedding VECTOR(768),
    match_count INT DEFAULT 5,
    filter_category TEXT DEFAULT NULL
)
RETURNS TABLE (
    id BIGINT,
    content TEXT,
    category VARCHAR(50),
    source_book VARCHAR(200),
    chapter_title VARCHAR(300),
    created_at TIMESTAMPTZ,
    similarity FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT
        kc.id,
        kc.content,
        kc.category,
        kc.source_book,
        kc.chapter_title,
        kc.created_at,
        1 - (kc.embedding <=> query_embedding) AS similarity
    FROM knowledge_chunks kc
    WHERE kc.embedding IS NOT NULL
      AND (filter_category IS NULL OR kc.category = filter_category)
    ORDER BY kc.embedding <=> query_embedding
    LIMIT match_count;
$$;

-- Similarity search: life experience
CREATE OR REPLACE FUNCTION match_life_experience(
    query_embedding VECTOR(768),
    match_count INT DEFAULT 5,
    include_regrets BOOLEAN DEFAULT TRUE
)
RETURNS TABLE (
    id BIGINT,
    event_title VARCHAR(150),
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
        led.id,
        led.event_title,
        led.context,
        led.choice_made,
        led.consequences_lessons,
        led.is_regret,
        led.event_timestamp,
        1 - (led.embedding <=> query_embedding) AS similarity
    FROM life_experience_decisions led
    WHERE led.embedding IS NOT NULL
      AND (include_regrets OR led.is_regret = FALSE)
    ORDER BY led.embedding <=> query_embedding
    LIMIT match_count;
$$;

-- Memory Journal + Publish Pipeline
CREATE TABLE IF NOT EXISTS journal_entries (
    id BIGSERIAL PRIMARY KEY,
    raw_text TEXT NOT NULL,
    extracted_json JSONB,
    applied BOOLEAN DEFAULT FALSE,
    ingest_report JSONB,
    source_type VARCHAR(20) DEFAULT 'note',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS channel_posts_processed (
    id BIGSERIAL PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    post_text TEXT NOT NULL,
    post_date TIMESTAMPTZ,
    source_type VARCHAR(20) DEFAULT 'channel',
    ingest_report JSONB,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(channel_id, message_id)
);

CREATE TABLE IF NOT EXISTS bot_published_posts (
    id BIGSERIAL PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    raw_input_id BIGINT REFERENCES journal_entries(id),
    published_text TEXT NOT NULL,
    published_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(channel_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_journal_entries_created ON journal_entries(created_at DESC);

-- Keep-alive cron (optional — requires pg_cron extension on Supabase Pro or self-hosted)
-- Prevents free-tier project pause from long inactivity. Run every 72 hours:
-- SELECT cron.schedule('persona_factory_keepalive', '0 0 */3 * *', $$SELECT 1$$);
-- Verify: SELECT * FROM cron.job;
-- Remove: SELECT cron.unschedule('persona_factory_keepalive');
