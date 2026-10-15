CREATE TABLE ai_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    model TEXT NOT NULL DEFAULT '',
    routing TEXT NOT NULL DEFAULT 'latency',
    max_output_tokens INTEGER NOT NULL DEFAULT 2048,
    daily_budget_usd TEXT NOT NULL DEFAULT '1.00',
    revision INTEGER NOT NULL DEFAULT 1
);
INSERT INTO ai_settings(id) VALUES (1);
CREATE TABLE ai_models (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    prompt_price TEXT NOT NULL,
    completion_price TEXT NOT NULL,
    context_length INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE ai_reviews (
    id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','running','completed','failed')),
    reserved_usd TEXT NOT NULL DEFAULT '0',
    actual_usd TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX ai_reviews_daily ON ai_reviews(created_at, state);
