-- External modules installed from GitHub releases (kitty.core.external_modules, docs/MODULES.md).
--
-- One row per installed module, pinned to the exact release file it was installed
-- from: its version and the SHA-256 that was checked against the release's published
-- checksum. The files live under data/modules/<id>/<version>/. A process reads these
-- rows once at start-up; an install, update or removal takes effect on the next start.

CREATE TABLE external_modules (
    id           TEXT PRIMARY KEY,                  -- manifest id, e.g. rika-learn
    repo         TEXT NOT NULL,                     -- GitHub owner/repo it is installed from
    version      TEXT NOT NULL,                     -- pinned release version
    sha256       TEXT NOT NULL CHECK (length(sha256) = 64),
    installed_at TEXT NOT NULL,
    manifest     TEXT NOT NULL                      -- rika-module.json as published (re-checked at start)
);

-- Per-module schema versions: a Python module's own migrations/NNN_name.sql files,
-- applied by the same runner (kitty.db.migrations.migrate(..., namespace=<id>)).
CREATE TABLE module_schema_version (
    module     TEXT NOT NULL,
    version    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (module, version)
);
