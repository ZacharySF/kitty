CREATE TABLE application_email_verification_requests (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    owner_token TEXT NOT NULL UNIQUE,
    origin TEXT NOT NULL,
    sender_email TEXT NOT NULL,
    recipient_binding TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'consuming', 'verified', 'blocked')),
    message_binding TEXT UNIQUE,
    updated_at TEXT NOT NULL
);
CREATE INDEX application_email_verification_job ON application_email_verification_requests(job_id, id);
