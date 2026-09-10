-- Memory Journal audit trail
CREATE TABLE IF NOT EXISTS journal_entries (
    id BIGSERIAL PRIMARY KEY,
    raw_text TEXT NOT NULL,
    extracted_json JSONB,
    applied BOOLEAN DEFAULT FALSE,
    ingest_report JSONB,
    source_type VARCHAR(20) DEFAULT 'note',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_journal_entries_created ON journal_entries(created_at DESC);
