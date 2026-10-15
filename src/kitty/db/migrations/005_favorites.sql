-- Favorites, the "more like these" ranking, and a record of filled forms.
--
-- favorited_at: when I starred the job (NULL = not a favorite). Independent of
--   queue_state: a favorite can be new, queued or later.
-- filled_at: when the form filler last filled this job's application form (the
--   f key, `applyai apply` or `applyai autofill`). Autofill skips jobs with it set,
--   so a form already filled and waiting for my submit is not filled again.
-- like_score: how much the job looks like my favorites minus how much it looks
--   like the jobs I skipped (core/likeness.py); NULL while I have no favorites.
-- like_terms: json array of the few terms it shares most with my favorites.
ALTER TABLE jobs ADD COLUMN favorited_at TEXT;
ALTER TABLE jobs ADD COLUMN filled_at TEXT;
ALTER TABLE jobs ADD COLUMN like_score REAL;
ALTER TABLE jobs ADD COLUMN like_terms TEXT;

CREATE INDEX jobs_favorited_idx ON jobs(favorited_at) WHERE favorited_at IS NOT NULL;
