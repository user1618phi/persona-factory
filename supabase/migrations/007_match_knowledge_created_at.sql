-- Migration 007: expose created_at from match_knowledge_chunks for recency reranking

DROP FUNCTION IF EXISTS match_knowledge_chunks(VECTOR, INT, TEXT);

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
