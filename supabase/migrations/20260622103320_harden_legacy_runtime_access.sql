-- Keep legacy tables available for rollback while denying all client access.

DROP POLICY IF EXISTS deny_client_access ON public.knowledge_chunks;
CREATE POLICY deny_client_access ON public.knowledge_chunks
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.core_identity_beliefs;
CREATE POLICY deny_client_access ON public.core_identity_beliefs
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.life_experience_decisions;
CREATE POLICY deny_client_access ON public.life_experience_decisions
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.style_patterns;
CREATE POLICY deny_client_access ON public.style_patterns
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.journal_entries;
CREATE POLICY deny_client_access ON public.journal_entries
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.channel_posts_processed;
CREATE POLICY deny_client_access ON public.channel_posts_processed
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.bot_published_posts;
CREATE POLICY deny_client_access ON public.bot_published_posts
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

ALTER FUNCTION public.match_life_experience(vector, integer, boolean)
    SECURITY INVOKER SET search_path = public;
ALTER FUNCTION public.match_knowledge_chunks(vector, integer, text)
    SECURITY INVOKER SET search_path = public;

REVOKE ALL ON FUNCTION public.match_life_experience(vector, integer, boolean)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.match_knowledge_chunks(vector, integer, text)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.match_life_experience(vector, integer, boolean)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.match_knowledge_chunks(vector, integer, text)
    TO service_role;

CREATE INDEX IF NOT EXISTS idx_bot_published_posts_draft_id
    ON public.bot_published_posts(draft_id) WHERE draft_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bot_published_posts_final_version_id
    ON public.bot_published_posts(final_version_id)
    WHERE final_version_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bot_published_posts_journal_id
    ON public.bot_published_posts(journal_id) WHERE journal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bot_published_posts_raw_input_id
    ON public.bot_published_posts(raw_input_id) WHERE raw_input_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_journal_entries_draft_id
    ON public.journal_entries(draft_id) WHERE draft_id IS NOT NULL;

ANALYZE public.bot_published_posts;
ANALYZE public.journal_entries;
