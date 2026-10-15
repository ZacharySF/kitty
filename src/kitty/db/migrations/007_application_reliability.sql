-- A past fill marker proves an attempt, not a complete application.
ALTER TABLE jobs ADD COLUMN fill_state TEXT NOT NULL DEFAULT 'unattempted'
    CHECK (fill_state IN ('unattempted', 'attempted', 'partial', 'blocked', 'ready'));
ALTER TABLE jobs ADD COLUMN fill_attempted_at TEXT;
ALTER TABLE jobs ADD COLUMN fill_ready_at TEXT;
ALTER TABLE jobs ADD COLUMN fill_blocker TEXT;
ALTER TABLE jobs ADD COLUMN fill_context_id TEXT;
ALTER TABLE jobs ADD COLUMN fill_url TEXT;

UPDATE jobs SET fill_state = 'attempted', fill_attempted_at = filled_at,
    fill_blocker = 'Legacy fill attempt; readiness has not been verified.'
WHERE filled_at IS NOT NULL;
ALTER TABLE jobs DROP COLUMN filled_at;

-- Discovery sightings and successful application-page verification are different facts.
ALTER TABLE jobs ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'unverified'
    CHECK (verification_status IN ('unverified', 'open', 'closed', 'blocked', 'error'));
ALTER TABLE jobs ADD COLUMN verified_at TEXT;
ALTER TABLE jobs ADD COLUMN verification_checked_at TEXT;
ALTER TABLE jobs ADD COLUMN verification_reason TEXT;
