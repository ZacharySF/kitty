"""Rika's email: drafts, held sends with Undo-send and per-kind auto-send (/api/rika/mail).

GET  /api/rika/mail  ->  {mailbox, kinds, drafts}  (kitty.core.rika.mail.overview)
POST /api/rika/mail/settings {kind, auto, confirm?} | {hold_seconds}  ->  GET shape
    Turning a kind's auto-send on needs ``confirm: true`` (409 otherwise).
POST /api/rika/mail/check {}  ->  {new: int, drafts: [Draft], notes: [str]}
POST /api/rika/mail/drafts/<id> {body, subject?}  ->  {draft}  (draft or cancelled only)
POST /api/rika/mail/drafts/<id>/send {}  ->  {draft}  held for hold_seconds, then sent
POST /api/rika/mail/drafts/<id>/undo {}  ->  {draft}  cancelled; 409 once sent
POST /api/rika/mail/drafts/<id>/discard {}  ->  {draft}
POST /api/rika/mail/connect {}  ->  {task_id}  Gmail consent for Rika's separate compose
    token, in a background task (409 in the demo).

Writes run under ``app.mutation_lock`` (the server's POST path holds it). ``start`` cancels
sends left held by the last run and starts the dispatcher that sends due ones.
"""
from __future__ import annotations

import logging
import re
import threading
from contextlib import closing

from kitty.core.gmail import auth
from kitty.core.rika import mail
from kitty.db import connect

log = logging.getLogger(__name__)

PREFIX = "/api/rika/mail"
_DRAFT = re.compile(re.escape(PREFIX) + r"/drafts/([0-9a-f]{32})(?:/(send|undo|discard))?")
_MAIL_STATUS = {"auth": 409, "rate_limited": 429, "not_found": 404}


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def _keys(body: dict, allowed: set[str], required: set[str] = frozenset()) -> None:
    if set(body) - allowed or required - set(body):
        raise _error(400, "Unexpected or missing email request fields")


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except mail.MailRefused as exc:
        raise _error(exc.status, str(exc)) from None
    except mail.MailError as exc:
        raise _error(_MAIL_STATUS.get(exc.kind, 502), str(exc)) from None


def start(app) -> None:
    with closing(connect(app.settings.db_path)) as conn:
        mail.recover(conn)
    halted = threading.Event()
    thread = threading.Thread(target=_dispatcher, args=(app, halted), name="kitty-rika-mail", daemon=True)
    app.rika_mail_dispatcher = (halted, thread)
    thread.start()


def stop(app) -> None:
    dispatcher = getattr(app, "rika_mail_dispatcher", None)
    if dispatcher:
        dispatcher[0].set()
        dispatcher[1].join()
        app.rika_mail_dispatcher = None


def _dispatcher(app, halted: threading.Event) -> None:
    while not halted.wait(mail.DISPATCH_INTERVAL):
        try:
            with closing(connect(app.settings.db_path)) as conn:
                mail.dispatch(app, conn)
        except Exception:  # noqa: BLE001 - the next tick tries again
            log.exception("Rika's mail dispatcher failed")


def get(app, conn, path, query):
    if path == PREFIX:
        app._no_query(query)
        return mail.overview(app, conn)
    return None


def post(app, conn, path, body):
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        return None
    if path == PREFIX + "/settings":
        _keys(body, {"kind", "auto", "confirm", "hold_seconds"})
        if not body or ("kind" in body) != ("auto" in body) or ("confirm" in body and "kind" not in body):
            raise _error(400, "Send {kind, auto, confirm?} or {hold_seconds}")
        if "hold_seconds" in body:
            _call(mail.set_hold, conn, body["hold_seconds"])
        if "kind" in body:
            _call(mail.set_auto, conn, body["kind"], body["auto"], confirm=body.get("confirm", False))
        return mail.overview(app, conn)
    if path == PREFIX + "/check":
        _keys(body, set())
        if not mail.connected(app):
            raise _error(409, "Connect Gmail first (Settings → Rika → Email)")
        found = _call(mail.check, app, conn)
        return {"new": len(found["new"]), "drafts": found["drafted"], "notes": found["notes"]}
    if path == PREFIX + "/connect":
        _keys(body, set())
        return _connect(app)
    match = _DRAFT.fullmatch(path)
    if match:
        ident, action = match[1], match[2]
        if action is None:
            _keys(body, {"body", "subject"}, {"body"})
            return {"draft": _call(mail.edit, app, conn, ident, body["body"], body.get("subject"))}
        _keys(body, set())
        return {"draft": _call({"send": mail.send, "undo": mail.undo, "discard": mail.discard}[action], app, conn, ident)}
    return None


def _connect(app) -> dict:
    if app.settings.demo:
        raise _error(409, "The demo uses a synthetic mailbox; there is no Gmail to connect")

    def consent():
        try:
            auth.run_consent_flow(app.settings, compose=True)
        except auth.GmailSetupError as exc:
            raise _error(409, str(exc)) from None
        try:
            app.rika_mail_account = mail.GmailMailbox.from_settings(app.settings).account()
        except mail.MailError:
            app.rika_mail_account = None
        return {"connected": True, "scope": "gmail.readonly gmail.compose"}

    return {"task_id": app.tasks.start("rika-mail-connect", consent)["id"]}
