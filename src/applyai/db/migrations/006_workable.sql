-- foreign_keys: off
-- Workable becomes a board kind. SQLite cannot change a CHECK constraint in place,
-- so companies is rebuilt (SQLite's documented 12-step table rebuild): new table,
-- copy every row with its id, drop the old one, rename. jobs.company_id keeps
-- pointing at the same ids. The header line above makes the runner switch
-- foreign keys off around this script (dropping companies with them on would
-- fail) and run PRAGMA foreign_key_check before committing.

CREATE TABLE companies_new (
    id                   INTEGER PRIMARY KEY,
    norm_name            TEXT NOT NULL UNIQUE,   -- lowercased, legal suffixes stripped
    display_name         TEXT NOT NULL,
    website              TEXT,
    ats_kind             TEXT CHECK (ats_kind IN
                             ('greenhouse','lever','ashby','smartrecruiters','workday',
                              'workable','other')),
    ats_slug             TEXT,                   -- workday: "tenant/wdN/site"
    notes                TEXT,
    school_selective     INTEGER NOT NULL DEFAULT 0,
    from_watchlist       INTEGER NOT NULL DEFAULT 0,
    priority             INTEGER NOT NULL DEFAULT 0,  -- >0 nudges triage toward tier A
    expected_open_months TEXT,                   -- json array of month numbers, or NULL
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

INSERT INTO companies_new (id, norm_name, display_name, website, ats_kind, ats_slug, notes,
                           school_selective, from_watchlist, priority, expected_open_months,
                           created_at, updated_at)
SELECT id, norm_name, display_name, website, ats_kind, ats_slug, notes,
       school_selective, from_watchlist, priority, expected_open_months,
       created_at, updated_at
  FROM companies;

DROP TABLE companies;
ALTER TABLE companies_new RENAME TO companies;
