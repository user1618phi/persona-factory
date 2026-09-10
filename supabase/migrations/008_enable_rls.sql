-- Enable RLS on all application tables.
-- service_role / secret API keys bypass RLS; anon/publishable keys get no policies (deny all).

ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_identity_beliefs ENABLE ROW LEVEL SECURITY;
ALTER TABLE life_experience_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE style_patterns ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_posts_processed ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_published_posts ENABLE ROW LEVEL SECURITY;
