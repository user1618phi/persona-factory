-- Backfill legacy author data into reviewable V2.1 memory.
-- Book-derived identity/style rows are archived in legacy storage, not deleted.

INSERT INTO persona_rules (
    rule_type, topic, content, source_type, source_id, confidence,
    status, fingerprint, metadata, created_at, updated_at
)
SELECT
    CASE WHEN cib.layer_type = 'identity' THEN 'identity' ELSE 'belief' END,
    cib.topic,
    cib.rules_and_values,
    'legacy',
    cib.id::TEXT,
    0.6,
    'pending',
    encode(
        digest(
            lower(trim(cib.layer_type)) || ':' ||
            lower(trim(cib.topic)) || ':' ||
            lower(regexp_replace(trim(cib.rules_and_values), '\s+', ' ', 'g')),
            'sha256'
        ),
        'hex'
    ),
    jsonb_build_object('legacy_table', 'core_identity_beliefs'),
    cib.updated_at,
    cib.updated_at
FROM core_identity_beliefs cib
WHERE cib.source_book IS NULL
  AND cib.is_deprecated = FALSE
ON CONFLICT (fingerprint) DO NOTHING;

INSERT INTO life_experiences (
    event_title, context, choice_made, consequences_lessons, is_regret,
    event_timestamp, embedding, source_type, source_id, confidence,
    status, fingerprint, metadata, created_at, updated_at
)
SELECT
    led.event_title,
    led.context,
    led.choice_made,
    led.consequences_lessons,
    led.is_regret,
    led.event_timestamp,
    led.embedding,
    'legacy',
    led.id::TEXT,
    0.6,
    'pending',
    encode(
        digest(
            lower(trim(led.event_title)) || ':' ||
            lower(regexp_replace(trim(led.context), '\s+', ' ', 'g')),
            'sha256'
        ),
        'hex'
    ),
    jsonb_build_object('legacy_table', 'life_experience_decisions'),
    led.created_at,
    led.created_at
FROM life_experience_decisions led
ON CONFLICT (fingerprint) DO NOTHING;

UPDATE core_identity_beliefs
SET is_deprecated = TRUE
WHERE source_book IS NOT NULL
  AND is_deprecated = FALSE;

UPDATE knowledge_chunks
SET
    canonical_source = CASE
        WHEN source_book = '627d32b823571642642689' THEN 'Пиши, сокращай'
        WHEN source_book IN ('launch read stamped', 'Launch by Michael Stelzner')
            THEN 'Launch by Michael Stelzner'
        WHEN source_book = 'the hero and the outlaw' THEN 'The Hero and the Outlaw'
        WHEN source_book = 'the story factor' THEN 'The Story Factor'
        WHEN source_book = 'покажи свою работу остин клеон' THEN 'Покажи свою работу'
        WHEN source_book = 'islam discipline excerpt' THEN 'Islam Discipline — curated'
        ELSE source_book
    END,
    fingerprint = encode(
        digest(
            lower(
                regexp_replace(
                    trim(content),
                    '\s+',
                    ' ',
                    'g'
                )
            ),
            'sha256'
        ),
        'hex'
    ),
    verification_status = CASE
        WHEN category IN ('islam', 'finance') THEN 'unverified'
        ELSE verification_status
    END
WHERE fingerprint IS NULL OR canonical_source IS NULL;

UPDATE bot_published_posts
SET journal_id = raw_input_id
WHERE journal_id IS NULL AND raw_input_id IS NOT NULL;
