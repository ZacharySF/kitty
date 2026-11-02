"""`kitty doctor`: check that the workspace is in a state I can work in.

Every failure line says how to fix it. Secrets are reported present/missing and
never printed.
"""

from __future__ import annotations

import sqlite3

from kitty.config import CONFIG_FILES, ConfigError, parse_file
from kitty.modules import BUILD_FILE, LEGACY_MODULES_FILE, load_modules
from kitty.core import keys
from kitty.core import resumes as resumes_core
from kitty.core import sources as sources_core
from kitty.db import connect, current_version, latest_version, pending_migrations
from kitty.models import Check
from kitty.settings import Settings

#: Keys the job tracker uses, with why. None is required to run.
JOB_KEYS: tuple[tuple[str, str], ...] = (
    ("ANTHROPIC", "needed for LLM triage"),
    ("USAJOBS", "needed only if the usajobs source is enabled"),
)


def check_config(settings: Settings) -> list[Check]:
    """Validate every config file, one Check per file."""
    results: list[Check] = []
    for filename, model, required, has_example in CONFIG_FILES:
        path = settings.config_path(filename)
        example = f"{filename}.example"
        if not path.exists():
            if required:
                results.append(Check(
                    name=f"config/{filename}", ok=False, detail="missing",
                    fix=f"run `kitty init`, or copy config/{example} to config/{filename}"
                    if has_example else f"restore config/{filename} from git",
                ))
            else:
                results.append(Check(
                    name=f"config/{filename}", ok=True, warning=True,
                    detail="missing - using built-in defaults",
                    fix=f"copy config/{example} to config/{filename} when you want to customise it",
                ))
            continue
        try:
            parse_file(path, model)
        except ConfigError as exc:
            results.extend(
                Check(
                    name=f"config/{filename}", ok=False,
                    detail=f"line {problem.line or '?'}: {problem.location} {problem.message}".strip(),
                    fix=problem.hint,
                )
                for problem in exc.problems
            )
        else:
            results.append(Check(name=f"config/{filename}", ok=True, detail="valid"))
    return results


def check_database(settings: Settings) -> list[Check]:
    """Check the database exists and is migrated to the newest schema."""
    if not settings.db_path.exists():
        return [Check(
            name="database", ok=False, detail=f"{settings.db_path} does not exist",
            fix="run `kitty init`",
        )]

    conn = connect(settings.db_path)
    try:
        version, newest = current_version(conn), latest_version()
        pending = pending_migrations(conn)
        if pending:
            return [Check(
                name="database", ok=False,
                detail=f"schema version {version}, newest is {newest} "
                       f"({len(pending)} migration(s) pending)",
                fix="run `kitty init` (it migrates in place and keeps your data)",
            )]
        return [Check(
            name="database", ok=True,
            detail=f"{settings.db_path.name} at schema version {version}",
        )]
    finally:
        conn.close()


def check_keys(settings: Settings) -> list[Check]:
    """Report which keys are set and where (kitty Keys). Values are never printed."""
    store = keys.for_settings(settings)
    results: list[Check] = []
    for name, note in JOB_KEYS:
        try:
            found = store.lookup(name)
        except keys.KeysError as exc:
            results.append(Check(name=f"key/{name}", ok=False, detail=str(exc),
                                 fix="fix the private key file, or unlock the OS keyring"))
            continue
        results.append(Check(
            name=f"key/{name}",
            ok=True,
            warning=found is None,
            detail=f"set ({found[1]})" if found else f"missing - {note}",
            fix=None if found else f"kitty keys set {name} (or {keys.env_name(name)}= in .env)",
        ))
    return results


def check_resumes(conn: sqlite3.Connection) -> list[Check]:
    """Check registered resume files still exist and still match their hash."""
    registered = resumes_core.list_resumes(conn)
    if not registered:
        return [Check(
            name="resumes", ok=True, warning=True, detail="none registered",
            fix="run `kitty resumes add <file>` so triage can pick between them",
        )]

    problems = dict(
        (resume.label, problem) for resume, problem in resumes_core.check_files(conn)
    )
    results: list[Check] = []
    for resume in registered:
        problem = problems.get(resume.label)
        if problem is None:
            results.append(Check(name=f"resume/{resume.label}", ok=True, detail=resume.file_path))
        elif problem == "file is missing":
            results.append(Check(
                name=f"resume/{resume.label}", ok=False, detail=f"{resume.file_path}: {problem}",
                fix="re-run `kitty resumes add <file>` with the new path",
            ))
        else:
            results.append(Check(
                name=f"resume/{resume.label}", ok=True, warning=True,
                detail=f"{resume.file_path}: {problem}",
                fix="re-run `kitty resumes add` to record the edit",
            ))
    return results


def check_sources(conn: sqlite3.Connection) -> list[Check]:
    """Report each source's last run. Stage 1 has no runs yet, which is fine."""
    results: list[Check] = []
    for source in sources_core.list_sources(conn):
        if not source.enabled:
            continue
        if source.last_status is None:
            results.append(Check(
                name=f"source/{source.name}", ok=True, detail="enabled, never run",
            ))
        elif source.last_status == "ok":
            results.append(Check(
                name=f"source/{source.name}", ok=True,
                detail=f"ok, {source.last_count} jobs at {source.last_run_at}",
            ))
        else:
            results.append(Check(
                name=f"source/{source.name}", ok=True, warning=True,
                detail=f"{source.last_status}: {sources_core.safe_error(source.last_error)}",
                fix=" ".join(sources_core.recovery_actions(source.last_status, source.last_error).values()),
            ))
    return results


def check_tracks(conn: sqlite3.Connection) -> list[Check]:
    """Warn when a configured or promoted track default is not registered."""
    labels = {resume.label for resume in resumes_core.list_resumes(conn)}
    results: list[Check] = []
    for row in conn.execute(
        """SELECT t.name, CASE WHEN o.track IS NULL THEN t.default_resume ELSE r.label END
                  AS default_resume
             FROM tracks t
             LEFT JOIN resume_default_overrides o ON o.track = t.name
             LEFT JOIN resumes r ON r.id = o.resume_id
            WHERE t.active = 1 ORDER BY t.sort_order"""
    ):
        wanted = row["default_resume"]
        if wanted and wanted not in labels:
            results.append(Check(
                name=f"track/{row['name']}", ok=True, warning=True,
                detail=f"default_resume '{wanted}' is not registered",
                fix=f"run `kitty resumes add <file> --label {wanted}`",
            ))
    return results


def check_modules(settings: Settings) -> tuple[list[Check], tuple[str, ...] | None]:
    """Validate the module selection in config/build.json; return the checks and the
    selection (None if unreadable)."""
    path = settings.config_path(BUILD_FILE)
    if not path.exists() and settings.config_path(LEGACY_MODULES_FILE).exists():
        path = settings.config_path(LEGACY_MODULES_FILE)
    try:
        selection = load_modules(settings.config_dir)
    except ConfigError as exc:
        return [
            Check(
                name=f"config/{path.name}", ok=False,
                detail=f"line {problem.line or '?'}: {problem.location} {problem.message}".strip(),
                fix=problem.hint,
            )
            for problem in exc.problems
        ], None
    if not path.exists():
        return [Check(
            name=f"config/{BUILD_FILE}", ok=True,
            detail="not present yet - every module is enabled (existing workspace)",
        )], selection
    enabled = ", ".join(selection) or "none (core only)"
    return [Check(name=f"config/{path.name}", ok=True, detail=f"modules: {enabled}")], selection


def run_all(settings: Settings, conn: sqlite3.Connection | None = None) -> list[Check]:
    """Run every check of the active modules. Opens its own connection when the database exists.

    The careers config, resumes, tracks and sources are only checked while the
    applyai module is on, so a core-only or workspace-only workspace stays healthy
    without any of those files.
    """
    module_checks, selection = check_modules(settings)
    careers = selection is not None and "applyai" in selection
    results = module_checks + (check_config(settings) if careers else [])
    results += check_database(settings) + (check_keys(settings) if careers else [])

    owned: sqlite3.Connection | None = None
    if careers and conn is None and settings.db_path.exists():
        owned = conn = connect(settings.db_path)
    try:
        if careers and conn is not None and not pending_migrations(conn):
            results += check_resumes(conn) + check_tracks(conn) + check_sources(conn)
    finally:
        if owned is not None:
            owned.close()
    return results


def failures(checks: list[Check]) -> list[Check]:
    """Only the checks that failed."""
    return [check for check in checks if not check.ok]
