-- How many runs in a row a source answered cleanly without this posting in it.
-- Reset to 0 whenever the source lists the posting again. A job closes once
-- every one of its sources has missed it for `close_after_missed_runs` runs
-- (config/sources.yaml). Sources that never run (manual, import) stay at 0,
-- so their jobs are never closed this way.
ALTER TABLE job_sources ADD COLUMN missed_runs INTEGER NOT NULL DEFAULT 0;

-- The rest of what the eligibility filter reads, kept on the job (filled from
-- every source that lists it) so every discover can re-judge it with the
-- current rules in tracks.yaml.
ALTER TABLE jobs ADD COLUMN country TEXT;    -- as a source states it, e.g. 'United States'
ALTER TABLE jobs ADD COLUMN degrees TEXT;    -- json array from Simplify, or NULL
