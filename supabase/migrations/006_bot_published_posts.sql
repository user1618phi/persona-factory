-- Track bot-published posts so channel sync skips them
CREATE TABLE IF NOT EXISTS bot_published_posts (
    id BIGSERIAL PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    raw_input_id BIGINT REFERENCES journal_entries(id),
    published_text TEXT NOT NULL,
    published_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(channel_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_bot_published_channel ON bot_published_posts(channel_id, message_id);
