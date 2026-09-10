-- Migration 003: IVFFlat vector indexes (run AFTER initial data load)
-- pgvector IVFFlat works best when built on a representative sample of rows.
-- Recommended: load knowledge_chunks + life_experience_decisions first, then run this.

-- Knowledge RAG (Cohere 768-dim embeddings)
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding_ivfflat
    ON knowledge_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Life experience / regret layer (Gemini 768-dim embeddings)
CREATE INDEX IF NOT EXISTS idx_life_experience_embedding_ivfflat
    ON life_experience_decisions
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);

-- Tuning notes:
-- - lists: sqrt(row_count) is a common starting point (e.g. 100 for ~10k rows).
-- - Rebuild index after large bulk inserts: DROP INDEX ...; CREATE INDEX ...
-- - For <1000 rows, sequential scan may be faster; index is optional on free tier.
-- - Run ANALYZE knowledge_chunks; ANALYZE life_experience_decisions; after index creation.

-- Run ANALYZE knowledge_chunks; ANALYZE life_experience_decisions; after index creation.
