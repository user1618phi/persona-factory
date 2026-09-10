-- Explicitly preserve the server-only access model.
-- service_role bypasses RLS; anon/authenticated must never access runtime memory.

DROP POLICY IF EXISTS deny_client_access ON public.persona_rules;
CREATE POLICY deny_client_access ON public.persona_rules
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.voice_examples;
CREATE POLICY deny_client_access ON public.voice_examples
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.life_experiences;
CREATE POLICY deny_client_access ON public.life_experiences
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.style_modes;
CREATE POLICY deny_client_access ON public.style_modes
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.memory_candidates;
CREATE POLICY deny_client_access ON public.memory_candidates
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.drafts;
CREATE POLICY deny_client_access ON public.drafts
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.draft_versions;
CREATE POLICY deny_client_access ON public.draft_versions
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

DROP POLICY IF EXISTS deny_client_access ON public.generation_runs;
CREATE POLICY deny_client_access ON public.generation_runs
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

ALTER FUNCTION public.match_voice_examples(vector, integer)
    SECURITY INVOKER SET search_path = public;
ALTER FUNCTION public.match_persona_rules(vector, integer)
    SECURITY INVOKER SET search_path = public;
ALTER FUNCTION public.match_life_experiences(vector, integer, boolean)
    SECURITY INVOKER SET search_path = public;
ALTER FUNCTION public.match_knowledge_chunks_v21(vector, integer, boolean)
    SECURITY INVOKER SET search_path = public;

REVOKE ALL ON FUNCTION public.match_voice_examples(vector, integer)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.match_persona_rules(vector, integer)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.match_life_experiences(vector, integer, boolean)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.match_knowledge_chunks_v21(vector, integer, boolean)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.match_voice_examples(vector, integer)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.match_persona_rules(vector, integer)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.match_life_experiences(vector, integer, boolean)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.match_knowledge_chunks_v21(vector, integer, boolean)
    TO service_role;

CREATE INDEX IF NOT EXISTS idx_drafts_journal_id
    ON public.drafts(journal_id) WHERE journal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_drafts_style_mode
    ON public.drafts(style_mode);
CREATE INDEX IF NOT EXISTS idx_generation_runs_draft_id
    ON public.generation_runs(draft_id) WHERE draft_id IS NOT NULL;

ANALYZE public.drafts;
ANALYZE public.generation_runs;
