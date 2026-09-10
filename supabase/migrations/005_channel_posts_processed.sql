-- Dedup for channel sync ingest
CREATE TABLE IF NOT EXISTS channel_posts_processed (
    id BIGSERIAL PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    post_text TEXT NOT NULL,
    post_date TIMESTAMPTZ,
    source_type VARCHAR(20) DEFAULT 'channel',
    ingest_report JSONB,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(channel_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_posts_channel ON channel_posts_processed(channel_id, message_id);
