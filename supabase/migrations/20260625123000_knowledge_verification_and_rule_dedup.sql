-- Add review queue support for verified knowledge and harden reviewed memory data.

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_verification_queue
    ON public.knowledge_chunks(verification_status, category, created_at DESC);

WITH ranked_rules AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY
                rule_type,
                regexp_replace(lower(trim(content)), '\s+', ' ', 'g')
            ORDER BY confidence DESC, confirmed_at ASC NULLS LAST, id ASC
        ) AS duplicate_rank
    FROM public.persona_rules
    WHERE status = 'active'
),
duplicate_rules AS (
    SELECT id
    FROM ranked_rules
    WHERE duplicate_rank > 1
)
UPDATE public.persona_rules pr
SET
    status = 'archived',
    metadata = COALESCE(pr.metadata, '{}'::jsonb) || jsonb_build_object(
        'archive_reason',
        'duplicate_active_rule_content',
        'archived_by_migration',
        '20260625123000_knowledge_verification_and_rule_dedup'
    ),
    updated_at = NOW()
FROM duplicate_rules dup
WHERE pr.id = dup.id;

UPDATE public.voice_examples
SET
    quality_weight = 0.4,
    metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
        'quality_weight_adjusted_by',
        '20260625123000_knowledge_verification_and_rule_dedup',
        'previous_quality_weight',
        quality_weight
    )
WHERE source_type = 'accepted_draft'
  AND status = 'active'
  AND quality_weight > 0.4;

UPDATE public.life_experiences
SET
    context = 'У меня были все шансы поступить в университет, но я не смог и долго не понимал, как честно об этом говорить.',
    choice_made = 'Не поступил в университет и начал избегать публичного признания этого провала.',
    consequences_lessons = 'Этот опыт связан со страхом показывать неудачу. Личный блог может стать способом честно признать провал, не прятаться за образом успешности и постепенно двигаться дальше.',
    confidence = GREATEST(confidence, 0.85),
    metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
        'content_refined_by',
        '20260625123000_knowledge_verification_and_rule_dedup',
        'embedding_refresh_needed',
        true
    ),
    updated_at = NOW()
WHERE id = 11
  AND status = 'active';

ANALYZE public.knowledge_chunks;
ANALYZE public.persona_rules;
ANALYZE public.voice_examples;
ANALYZE public.life_experiences;
