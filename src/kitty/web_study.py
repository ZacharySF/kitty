"""Study settings (POST /api/study/settings): they edit the build file, so they stay here.

Every other study route is answered by rika-server (server/src/study);
:mod:`kitty.core.study` stays for Rika's tools and the build's projection.
"""
from __future__ import annotations

from kitty.core import study


def get(app, conn, path, query):
    return None


def post(app, conn, path, body):
    if path != '/api/study/settings':
        return None
    from kitty.web_server import APIError
    try:
        return study.save_settings(conn, body, app.settings.root)
    except study.StudyConflict as exc:
        raise APIError(409, str(exc)) from None
    except ValueError as exc:
        raise APIError(400, str(exc)) from None
