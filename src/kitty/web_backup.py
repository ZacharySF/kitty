"""Settings → Backup: one archive of the whole workspace, previewed and restored into a new folder.

Page routes (behind the normal page guard; mutations behind the mutation guard)::

    GET  /api/backup             {folder, root, last}   the default folder, this workspace, the last archive
    POST /api/backup/create      {folder}               write an archive there (kitty.core.backup.create)
    POST /api/backup/preview     {archive, into}        counts, warnings, problems; writes nothing
    POST /api/backup/restore     {archive, into}        restore into a new or empty folder, never this one

Paths are on this computer and must be full paths (``~`` is expanded). A refusal answers with
the core's status: 400 for a path or an archive it cannot use, 409 for a destination it must
not write or an archive this version cannot restore. The core is shared with ``kitty backup``.
"""
from __future__ import annotations

from pathlib import Path

from kitty.core import backup

PREFIX = "/api/backup"
_BODIES = {"create": {"folder"}, "preview": {"archive", "into"}, "restore": {"archive", "into"}}


def _path(value, what: str) -> Path:
    from kitty.web_server import APIError

    if not isinstance(value, str) or not value.strip():
        raise APIError(400, f"Enter the {what} as a full path")
    return Path(value.strip()).expanduser()


def get(app, conn, path, query):
    if path != PREFIX:
        return None
    app._no_query(query)
    return {"folder": str(backup.default_folder()), "root": str(app.settings.root), "last": backup.last(app.settings.root)}


def post(app, conn, path, body):
    from kitty.web_server import APIError

    action = path.removeprefix(PREFIX + "/")
    if not path.startswith(PREFIX + "/") or action not in _BODIES:
        return None
    if set(body) != _BODIES[action]:
        raise APIError(400, "Expected " + ", ".join(sorted(_BODIES[action])))
    try:
        if action == "create":
            return backup.create(app.settings, _path(body["folder"], "backup folder"))
        archive = _path(body["archive"], "backup file")
        if action == "preview":
            into = _path(body["into"], "restore folder") if body["into"] not in (None, "") else None
            return backup.preview(archive, into, live_root=app.settings.root)
        return backup.restore(archive, _path(body["into"], "restore folder"), live_root=app.settings.root)
    except backup.BackupError as error:
        raise APIError(error.status, str(error)) from None
