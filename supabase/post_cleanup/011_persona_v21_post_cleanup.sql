-- Run after scripts/clean_knowledge.py --apply verifies duplicate fingerprints are gone.

DROP INDEX IF EXISTS idx_knowledge_fingerprint;
CREATE UNIQUE INDEX idx_knowledge_fingerprint
    ON knowledge_chunks(fingerprint) WHERE fingerprint IS NOT NULL;

ANALYZE knowledge_chunks;
ANALYZE voice_examples;
ANALYZE persona_rules;
ANALYZE life_experiences;
