-- Reliability and performance hardening from the V2.1 technical audit.

CREATE TABLE IF NOT EXISTS public.bot_sessions (
    user_id BIGINT PRIMARY KEY,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.bot_sessions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS deny_client_access ON public.bot_sessions;
CREATE POLICY deny_client_access ON public.bot_sessions
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

REVOKE ALL ON TABLE public.bot_sessions FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.bot_sessions TO service_role;

CREATE INDEX IF NOT EXISTS idx_bot_sessions_updated_at
    ON public.bot_sessions(updated_at DESC);

-- HNSW is the current Supabase recommendation for changing vector datasets.
-- Partial indexes avoid storing rows that cannot participate in similarity search.
CREATE INDEX IF NOT EXISTS idx_persona_rules_embedding_hnsw
    ON public.persona_rules
    USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL AND status = 'active';

CREATE INDEX IF NOT EXISTS idx_voice_examples_embedding_hnsw
    ON public.voice_examples
    USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL AND status = 'active';

CREATE INDEX IF NOT EXISTS idx_life_experiences_embedding_hnsw
    ON public.life_experiences
    USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL AND status = 'active';

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding_hnsw
    ON public.knowledge_chunks
    USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

-- Avoid paying the storage/write cost for two ANN indexes on the same column.
DROP INDEX IF EXISTS public.idx_knowledge_chunks_embedding_ivfflat;

-- Preserve quality weighting without disabling the ANN index: retrieve a
-- wider nearest-neighbor set first, then rerank that bounded candidate set.
CREATE OR REPLACE FUNCTION public.match_voice_examples(
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
    WITH nearest AS MATERIALIZED (
        SELECT
            ve.id,
            ve.text,
            ve.source_type,
            ve.source_chat,
            ve.occurred_at,
            ve.quality_weight,
            ve.embedding <=> query_embedding AS distance
        FROM public.voice_examples ve
        WHERE ve.status = 'active' AND ve.embedding IS NOT NULL
        ORDER BY ve.embedding <=> query_embedding
        LIMIT GREATEST(match_count * 4, match_count)
    )
    SELECT
        nearest.id,
        nearest.text,
        nearest.source_type,
        nearest.source_chat,
        nearest.occurred_at,
        nearest.quality_weight,
        (1 - nearest.distance)::FLOAT AS similarity
    FROM nearest
    ORDER BY ((1 - nearest.distance) * nearest.quality_weight) DESC
    LIMIT match_count;
$$;

ALTER FUNCTION public.match_voice_examples(vector, integer)
    SECURITY INVOKER SET search_path = public;
REVOKE ALL ON FUNCTION public.match_voice_examples(vector, integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.match_voice_examples(vector, integer)
    TO service_role;

ANALYZE public.bot_sessions;
ANALYZE public.persona_rules;
ANALYZE public.voice_examples;
ANALYZE public.life_experiences;
ANALYZE public.knowledge_chunks;
