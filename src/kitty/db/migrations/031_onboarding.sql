-- Local workspace identity only; no OAuth tokens and no API authorization claim.
CREATE TABLE workspace_onboarding (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    welcome_completed INTEGER NOT NULL DEFAULT 0 CHECK (welcome_completed IN (0, 1)),
    tutorial_completed INTEGER NOT NULL DEFAULT 0 CHECK (tutorial_completed IN (0, 1)),
    google_sub TEXT,
    google_email TEXT,
    google_name TEXT
);
INSERT INTO workspace_onboarding (id) VALUES (1);
