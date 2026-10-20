"""Flashcard API extension; no network calls or filesystem writes from user paths."""
from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
import zipfile

from kitty.core import flashcards as flash


def _error(message, status=400):
    from kitty.web_server import APIError
    return APIError(status, message)


def _id(value, optional=False):
    if optional and value in {None, "", 0}: return None
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,17}", str(value)):
        raise ValueError("Expected a positive item ID")
    return int(value)


def _decode(value):
    if not isinstance(value, str): raise ValueError("Expected base64 file content")
    try: return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error): raise ValueError("Invalid base64 file content") from None


def get(app, conn, path, query):
    if not path.startswith("/api/flashcards"): return None
    try:
        flash.bootstrap(conn)
        if path == "/api/flashcards":
            if query: raise ValueError("Overview takes no parameters")
            return flash.overview(conn)
        if path == "/api/flashcards/browser":
            if set(query) - {"deck_id", "search", "limit", "offset"}: raise ValueError("Unknown browser parameter")
            limit, offset = int(query.get("limit", "100")), int(query.get("offset", "0"))
            if not 1 <= limit <= 500 or not 0 <= offset <= 100000: raise ValueError("Invalid browser page")
            return flash.browser(conn, _id(query.get("deck_id"), True), query.get("search", ""), limit, offset)
        if path == "/api/flashcards/note":
            if set(query) != {"id"}: raise ValueError("An exact note ID is required")
            ident = _id(query["id"])
            row = conn.execute("SELECT * FROM flash_notes WHERE id=?", (ident,)).fetchone()
            if row is None: raise _error("That note no longer exists", 404)
            note = dict(row)
            note["fields"], note["tags"] = json.loads(note["fields"]), json.loads(note["tags"])
            note["cards"] = [dict(card) for card in conn.execute(
                """SELECT id,ordinal,state,due,suspended,buried_until,leech,lapses,reviews,revision
                FROM flash_cards WHERE note_id=? ORDER BY ordinal,id""", (ident,))]
            return {"note": note}
        if path == "/api/flashcards/history":
            if set(query) - {"card_id"}: raise ValueError("Unknown history parameter")
            return flash.history(conn, _id(query.get("card_id"), True))
        return None
    except (ValueError, TypeError, sqlite3.IntegrityError) as error:
        raise _error(str(error)) from None


def post(app, conn, path, body):
    if not path.startswith("/api/flashcards/"): return None
    routes = {"deck", "type", "note", "action", "review", "reveal", "grade", "undo", "media", "import", "export"}
    operation = path.removeprefix("/api/flashcards/")
    if operation not in routes: return None
    try:
        flash.bootstrap(conn)
        if not isinstance(body, dict): raise ValueError("Expected JSON object")
        if operation == "deck": return {"deck": flash.save_deck(conn, body)}
        if operation == "type": return {"type": flash.save_type(conn, body)}
        if operation == "note": return {"note": flash.save_note(conn, body)}
        if operation == "action": return flash.action(conn, body)
        if operation == "review":
            study_id = body.get("study_id")
            if study_id is not None and (not isinstance(study_id, str) or not re.fullmatch(r"[a-f0-9]{48}", study_id)):
                raise ValueError("Invalid practice session ID")
            return flash.start_review(conn, _id(body.get("deck_id"), True), str(body.get("search", "")), body.get("mode", "regular"), study_id)
        if operation == "reveal": return flash.reveal(conn, str(body.get("token", "")))
        if operation == "grade": return flash.grade(conn, str(body.get("token", "")), body.get("rating"), body.get("duration_ms", 0))
        if operation == "undo":
            if body: raise ValueError("Undo expects an empty object")
            return flash.undo(conn)
        if operation == "media": return flash.upload_media(conn, body.get("name"), _decode(body.get("data")))
        if operation == "import":
            kind = body.get("format")
            if kind == "apkg": return flash.import_apkg(conn, _decode(body.get("data")))
            if kind in {"csv", "tsv"}:
                text = body.get("text")
                if not isinstance(text, str): raise ValueError("Expected CSV/TSV text")
                return flash.import_text(conn, text, _id(body.get("deck_id")), _id(body.get("type_id")), "\t" if kind == "tsv" else ",")
            raise ValueError("Supported formats: apkg, csv, tsv")
        if operation == "export":
            kind = body.get("format")
            deck = _id(body.get("deck_id"), True)
            if kind == "apkg": data, mime = flash.export_apkg(conn, deck), "application/octet-stream"
            elif kind in {"csv", "tsv"}: data, mime = flash.export_text(conn, deck, "\t" if kind == "tsv" else ","), "text/plain;charset=utf-8"
            else: raise ValueError("Supported formats: apkg, csv, tsv")
            return {"data": base64.b64encode(data).decode(), "mime": mime, "filename": "kitty-flashcards." + kind}
    except ImportError:
        raise _error("Install fsrs>=6.3,<7 to review/import scheduled flashcards") from None
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, sqlite3.DatabaseError, zipfile.BadZipFile, binascii.Error) as error:
        raise _error(str(error) or "Invalid flashcard data") from None
