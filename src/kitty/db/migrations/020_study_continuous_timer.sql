-- Alternate focus/break phases on observation; keep a stable completion identity for alerts.
ALTER TABLE study_timer ADD COLUMN completed_phase_id TEXT;
ALTER TABLE study_timer ADD COLUMN completed_phase TEXT;
ALTER TABLE study_settings DROP COLUMN long_break_seconds;
ALTER TABLE study_settings DROP COLUMN cycle_count;
UPDATE study_settings SET sound=1, revision=revision+1;
-- Preserve an already running legacy break's remaining duration, but never schedule another long break.
UPDATE study_timer SET phase='short_break', revision=revision+1 WHERE phase='long_break';
-- An old unstarted phase is not permission to start studying during an upgrade.
UPDATE study_timer SET status='idle', phase='focus', phase_id=NULL, started_at=NULL,
    running_since=NULL, deadline_at=NULL, elapsed_seconds=0, segments_json='[]',
    duration_seconds=(SELECT focus_seconds FROM study_settings WHERE id=1), revision=revision+1
    WHERE status='awaiting_next';
