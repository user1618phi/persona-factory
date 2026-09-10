-- Runtime hardening and post-review audit support for Persona Factory V2.1.
-- Additive only: no legacy data is deleted by this migration.

ALTER TABLE public.generation_runs
    ADD COLUMN IF NOT EXISTS routing_strategy TEXT,
    ADD COLUMN IF NOT EXISTS primary_provider_failed BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_generation_runs_routing_strategy
    ON public.generation_runs(routing_strategy)
    WHERE routing_strategy IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.persona_cleanup_audit (
    id BIGSERIAL PRIMARY KEY,
    cleanup_version TEXT NOT NULL,
    target_table TEXT NOT NULL,
    target_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_value JSONB,
    new_value JSONB,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.persona_cleanup_audit ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS deny_client_access ON public.persona_cleanup_audit;
CREATE POLICY deny_client_access ON public.persona_cleanup_audit
    AS RESTRICTIVE
    FOR ALL
    TO anon, authenticated
    USING (false)
    WITH CHECK (false);

REVOKE ALL ON public.persona_cleanup_audit FROM anon, authenticated;
GRANT ALL ON public.persona_cleanup_audit TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.persona_cleanup_audit_id_seq TO service_role;

CREATE INDEX IF NOT EXISTS idx_persona_cleanup_audit_version
    ON public.persona_cleanup_audit(cleanup_version, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_persona_cleanup_audit_target
    ON public.persona_cleanup_audit(target_table, target_id);
