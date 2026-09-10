-- Migration 002: knowledge metadata + book profile provenance
-- Run in Supabase SQL Editor after init.sql

ALTER TABLE knowledge_chunks
    ADD COLUMN IF NOT EXISTS source_book VARCHAR(200),
    ADD COLUMN IF NOT EXISTS chapter_title VARCHAR(300),
    ADD COLUMN IF NOT EXISTS chunk_index INTEGER;

ALTER TABLE core_identity_beliefs
    ADD COLUMN IF NOT EXISTS source_book VARCHAR(200);

ALTER TABLE style_patterns
    ADD COLUMN IF NOT EXISTS source_book VARCHAR(200);

ALTER TABLE knowledge_chunks
    DROP CONSTRAINT IF EXISTS knowledge_chunks_category_check;

ALTER TABLE knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_category_check
    CHECK (category IN ('islam', 'finance', 'it', 'psychology', 'branding'));

CREATE INDEX IF NOT EXISTS idx_knowledge_source_book ON knowledge_chunks(source_book);
CREATE INDEX IF NOT EXISTS idx_identity_source_book ON core_identity_beliefs(source_book);
CREATE INDEX IF NOT EXISTS idx_style_source_book ON style_patterns(source_book);

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
