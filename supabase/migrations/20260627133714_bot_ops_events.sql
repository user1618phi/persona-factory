CREATE TABLE IF NOT EXISTS public.bot_ops_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'channel_sync_success',
            'channel_sync_error',
            'youtube_success',
            'youtube_error',
            'publish_memory_candidates',
            'status_error'
        )
    ),
    status TEXT NOT NULL CHECK (status IN ('success', 'error', 'info')),
    message_id BIGINT,
    source_id TEXT,
    summary TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bot_ops_events_type_created
    ON public.bot_ops_events(event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_bot_ops_events_created
    ON public.bot_ops_events(created_at DESC);

ALTER TABLE public.bot_ops_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS deny_client_access ON public.bot_ops_events;
CREATE POLICY deny_client_access ON public.bot_ops_events
    AS RESTRICTIVE FOR ALL TO anon, authenticated
    USING (FALSE) WITH CHECK (FALSE);

REVOKE ALL ON TABLE public.bot_ops_events FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.bot_ops_events TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.bot_ops_events_id_seq TO service_role;
