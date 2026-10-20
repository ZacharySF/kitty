"""Bounded local canvas documents; viewport and content have independent revisions."""
from __future__ import annotations

import base64
import binascii
import io
import json
import math
import os
import re
import sqlite3
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kitty.db.connection import transaction

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_ELEMENTS = 2000
MAX_POINTS = 100_000
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_STORAGE_BYTES = 128 * 1024 * 1024
MAX_IMPORT_BYTES = 12 * 1024 * 1024
MAX_PAGES = 500
MAX_PIXELS = 16_000_000
ID = re.compile(r"[a-zA-Z0-9_-]{1,80}\Z")
COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
KINDS = frozenset({"pen", "highlighter", "text", "sticky", "rect", "ellipse", "line", "arrow", "connector", "image"})


class NotesError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text(value: Any, maximum: int = 200, label: str = "Text") -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise NotesError(f"{label} must be text")
    try:
        if len(value.encode("utf-8")) > maximum:
            raise NotesError(f"{label} exceeds {maximum} bytes")
    except UnicodeError:
        raise NotesError(f"{label} must be valid Unicode") from None
    return value


def number(value: Any, low: float = -1_000_000, high: float = 1_000_000) -> float:
    if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high:
        raise NotesError(f"Canvas numbers must be finite and between {low} and {high}")
    return float(value)


def integer(value: Any, low: int = 1, high: int = 2**53 - 1) -> int:
    if type(value) is not int or not low <= value <= high:
        raise NotesError("Expected a valid integer")
    return value


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise NotesError("Invalid canvas identifier")
    return value


def keys(value: Any, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise NotesError("Unexpected or missing canvas fields")
    return value


def viewport(value: Any) -> dict:
    keys(value, {"x", "y", "zoom"}, {"x", "y", "zoom"})
    return {"x": number(value["x"]), "y": number(value["y"]), "zoom": number(value["zoom"], .05, 20)}


def empty_document() -> dict:
    return {"version": 1, "grid": True, "snap": False, "layers": [{"id": "default", "name": "Drawing", "visible": True, "locked": False}], "elements": []}


def normalize(value: Any, conn: sqlite3.Connection | None = None) -> dict:
    keys(value, {"version", "grid", "snap", "background", "layers", "elements"}, {"version", "layers", "elements"})
    if value["version"] != 1 or type(value["version"]) is not int:
        raise NotesError("Unsupported notebook document version")
    layers, elements = value["layers"], value["elements"]
    if not isinstance(layers, list) or not 1 <= len(layers) <= 32 or not isinstance(elements, list) or len(elements) > MAX_ELEMENTS:
        raise NotesError("Canvas is limited to 32 layers and 2000 elements")
    normalized_layers = []
    for layer in layers:
        keys(layer, {"id", "name", "visible", "locked"}, {"id", "name", "visible", "locked"})
        if type(layer["visible"]) is not bool or type(layer["locked"]) is not bool:
            raise NotesError("Layer flags must be booleans")
        normalized_layers.append({"id": identifier(layer["id"]), "name": text(layer["name"], 100), "visible": layer["visible"], "locked": layer["locked"]})
    layer_ids = {layer["id"] for layer in normalized_layers}
    if len(layer_ids) != len(layers):
        raise NotesError("Duplicate layer identifier")
    output, ids, points_total = [], set(), 0
    allowed = {"id", "kind", "layer", "x", "y", "w", "h", "color", "fill", "width", "points", "text", "fontSize", "group", "attachment", "from", "to", "tags", "checked"}
    for element in elements:
        keys(element, allowed, {"id", "kind", "layer", "x", "y", "w", "h", "color", "width"})
        eid = identifier(element["id"])
        if eid in ids:
            raise NotesError("Duplicate element identifier")
        ids.add(eid)
        kind = element["kind"]
        layer_id = identifier(element["layer"])
        if not isinstance(kind, str) or kind not in KINDS or layer_id not in layer_ids:
            raise NotesError("Unknown element type or layer")
        color, fill = element["color"], element.get("fill", "none")
        if not isinstance(color, str) or not COLOR.fullmatch(color) or not isinstance(fill, str) or (fill != "none" and not COLOR.fullmatch(fill)):
            raise NotesError("Use six-digit hex colors or no fill")
        item = {"id": eid, "kind": kind, "layer": element["layer"], "x": number(element["x"]), "y": number(element["y"]), "w": number(element["w"], 0), "h": number(element["h"], 0), "color": color, "fill": fill, "width": number(element["width"], .1, 100)}
        if element.get("group"):
            item["group"] = identifier(element["group"])
        if kind in {"pen", "highlighter"}:
            pts = element.get("points")
            if not isinstance(pts, list) or not 1 <= len(pts) <= 20_000:
                raise NotesError("A stroke needs 1–20000 pressure points")
            points_total += len(pts)
            if points_total > MAX_POINTS:
                raise NotesError("Page exceeds 100000 pressure points")
            item["points"] = []
            for point in pts:
                if not isinstance(point, list) or len(point) != 3:
                    raise NotesError("Stroke point must be x, y, pressure")
                item["points"].append([number(point[0]), number(point[1]), number(point[2], 0, 1)])
        if kind in {"text", "sticky"}:
            item["text"] = text(element.get("text", ""), 20_000)
            item["fontSize"] = number(element.get("fontSize", 20), 8, 120)
            tags = element.get("tags", [])
            if not isinstance(tags, list) or len(tags) > 4 or any(not isinstance(tag, str) or tag not in {"todo", "important", "question", "definition"} for tag in tags):
                raise NotesError("Unknown note tag")
            if tags:
                item["tags"] = list(dict.fromkeys(tags))
            if "checked" in element:
                if type(element["checked"]) is not bool:
                    raise NotesError("Checkbox state must be boolean")
                item["checked"] = element["checked"]
        if kind == "image":
            attachment = identifier(element.get("attachment"))
            if conn is not None and not conn.execute("SELECT 1 FROM note_attachments WHERE id=?", (attachment,)).fetchone():
                raise NotesError("Image references an unregistered local attachment")
            item["attachment"] = attachment
        if kind in {"line", "arrow", "connector"}:
            pts = element.get("points")
            if not isinstance(pts, list) or len(pts) != 2:
                raise NotesError("Line needs two endpoints")
            item["points"] = [[number(p[0]), number(p[1]), number(p[2], 0, 1)] if isinstance(p, list) and len(p) == 3 else _invalid_point() for p in pts]
            if kind == "connector":
                item["from"] = identifier(element.get("from"))
                item["to"] = identifier(element.get("to"))
        output.append(item)
    for item in output:
        if item["kind"] == "connector" and (item["from"] not in ids or item["to"] not in ids or item["from"] == item["to"] or item["id"] in {item["from"], item["to"]}):
            raise NotesError("Connector endpoints must be distinct existing elements")
    for flag in ("grid", "snap"):
        if type(value.get(flag, False)) is not bool:
            raise NotesError("Grid and snap flags must be boolean")
    result = {"version": 1, "grid": value.get("grid", False), "snap": value.get("snap", False), "layers": normalized_layers, "elements": output}
    if "background" in value:
        if not isinstance(value["background"], str) or value["background"] not in {"blank", "lined", "grid", "dotted", "cornell"}:
            raise NotesError("Unknown page background")
        result["background"] = value["background"]
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_DOCUMENT_BYTES:
        raise NotesError("Canvas document exceeds 2 MiB")
    return result


def _invalid_point():
    raise NotesError("Line point must be x, y, pressure")


def page(conn: sqlite3.Connection, page_id: int) -> dict:
    row = conn.execute("SELECT * FROM note_pages WHERE id=?", (integer(page_id),)).fetchone()
    if row is None:
        raise NotesError("Page not found", 404)
    result = dict(row)
    result["document"] = json.loads(result["document"])
    result["viewport"] = json.loads(result["viewport"])
    return result


def listing(conn: sqlite3.Connection, search: str = "") -> dict:
    search = text(search, 300)
    pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = conn.execute("SELECT p.id,p.notebook_id,p.title,p.revision,p.updated_at,p.favorite,p.last_opened,p.position FROM note_pages p JOIN note_notebooks n ON p.notebook_id=n.id WHERE p.title LIKE ? ESCAPE '\\' OR p.search_text LIKE ? ESCAPE '\\' OR n.title LIKE ? ESCAPE '\\' OR n.subject LIKE ? ESCAPE '\\' OR n.project LIKE ? ESCAPE '\\' ORDER BY p.position,p.updated_at DESC,p.id LIMIT 500", (pattern,) * 5)
    prefs = dict(conn.execute("SELECT allow_ai,retention_days,revision FROM note_preferences WHERE id=1").fetchone())
    prefs["allow_ai"] = bool(prefs["allow_ai"])
    return {"folders": [dict(row) for row in conn.execute("SELECT * FROM note_folders ORDER BY position,title,id")], "notebooks": [dict(row) for row in conn.execute("SELECT * FROM note_notebooks ORDER BY position,updated_at DESC LIMIT 200")], "pages": [dict(row) for row in rows], "preferences": prefs}


def _folder_id(conn: sqlite3.Connection, value: Any) -> int | None:
    if value is None:
        return None
    fid = integer(value)
    if not conn.execute("SELECT 1 FROM note_folders WHERE id=?", (fid,)).fetchone():
        raise NotesError("Folder not found", 404)
    return fid


def create_folder(conn: sqlite3.Connection, title: Any, parent_id: Any = None) -> dict:
    title = text(title, 200)
    if not title.strip():
        raise NotesError("Folder title is required")
    with transaction(conn):
        parent = _folder_id(conn, parent_id)
        stamp = now()
        fid = conn.execute("INSERT INTO note_folders(title,parent_id,created_at,updated_at) VALUES (?,?,?,?)", (title, parent, stamp, stamp)).lastrowid
    return dict(conn.execute("SELECT * FROM note_folders WHERE id=?", (fid,)).fetchone())


def save_folder(conn: sqlite3.Connection, folder_id: int, revision: Any, title: Any, parent_id: Any) -> dict:
    fid, rev, title = integer(folder_id), integer(revision), text(title, 200)
    if not title.strip():
        raise NotesError("Folder title is required")
    with transaction(conn):
        current = conn.execute("SELECT revision FROM note_folders WHERE id=?", (fid,)).fetchone()
        if current is None:
            raise NotesError("Folder not found", 404)
        if current["revision"] != rev:
            raise NotesError("Folder changed in another tab; reload before editing", 409)
        parent = _folder_id(conn, parent_id)
        ancestor = parent
        while ancestor is not None:
            if ancestor == fid:
                raise NotesError("A folder cannot contain itself or move into its descendants")
            ancestor = conn.execute("SELECT parent_id FROM note_folders WHERE id=?", (ancestor,)).fetchone()["parent_id"]
        conn.execute("UPDATE note_folders SET title=?,parent_id=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?", (title, parent, now(), fid, rev))
    return dict(conn.execute("SELECT * FROM note_folders WHERE id=?", (fid,)).fetchone())


def delete_folder(conn: sqlite3.Connection, folder_id: int, revision: Any) -> dict:
    fid, rev = integer(folder_id), integer(revision)
    with transaction(conn):
        current = conn.execute("SELECT revision FROM note_folders WHERE id=?", (fid,)).fetchone()
        if current is None:
            raise NotesError("Folder not found", 404)
        if current["revision"] != rev:
            raise NotesError("Folder changed; reload before deleting", 409)
        if conn.execute("SELECT 1 FROM note_folders WHERE parent_id=? UNION ALL SELECT 1 FROM note_notebooks WHERE folder_id=? LIMIT 1", (fid, fid)).fetchone():
            raise NotesError("Only empty folders can be deleted", 409)
        conn.execute("DELETE FROM note_folders WHERE id=? AND revision=?", (fid, rev))
    return {"ok": True}


def create_notebook(conn: sqlite3.Connection, title: Any, subject: Any = "", project: Any = "", folder_id: Any = None) -> dict:
    title, subject, project = text(title, 200), text(subject, 200), text(project, 200)
    if not title.strip():
        raise NotesError("Notebook title is required")
    with (nullcontext() if conn.in_transaction else transaction(conn)):
        folder = _folder_id(conn, folder_id)
        if conn.execute("SELECT COUNT(*) FROM note_notebooks").fetchone()[0] >= 200:
            raise NotesError("Notebook limit reached")
        stamp = now()
        nid = conn.execute("INSERT INTO note_notebooks(title,subject,project,folder_id,created_at,updated_at) VALUES (?,?,?,?,?,?)", (title, subject, project, folder, stamp, stamp)).lastrowid
    return dict(conn.execute("SELECT * FROM note_notebooks WHERE id=?", (nid,)).fetchone())


_UNSET_FOLDER = object()


def save_notebook(conn: sqlite3.Connection, notebook_id: int, revision: Any, title: Any, subject: Any, project: Any, folder_id: Any = _UNSET_FOLDER) -> dict:
    values = (text(title, 200), text(subject, 200), text(project, 200), now(), integer(notebook_id), integer(revision))
    if not values[0].strip():
        raise NotesError("Notebook title is required")
    with transaction(conn):
        if folder_id is _UNSET_FOLDER:
            cursor = conn.execute("UPDATE note_notebooks SET title=?,subject=?,project=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?", values)
        else:
            folder = _folder_id(conn, folder_id)
            cursor = conn.execute("UPDATE note_notebooks SET folder_id=?,title=?,subject=?,project=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?", (folder, *values))
        if cursor.rowcount != 1:
            raise NotesError("Notebook changed in another tab; reload before editing", 409)
    return dict(conn.execute("SELECT * FROM note_notebooks WHERE id=?", (notebook_id,)).fetchone())


def create_page(conn: sqlite3.Connection, notebook_id: Any, title: Any = "Untitled page") -> dict:
    nid, title = integer(notebook_id), text(title, 200)
    if not title.strip():
        raise NotesError("Page title is required")
    with (nullcontext() if conn.in_transaction else transaction(conn)):
        if not conn.execute("SELECT 1 FROM note_notebooks WHERE id=?", (nid,)).fetchone():
            raise NotesError("Notebook not found", 404)
        if conn.execute("SELECT COUNT(*) FROM note_pages").fetchone()[0] >= MAX_PAGES:
            raise NotesError("Page limit reached")
        stamp = now()
        pid = conn.execute("INSERT INTO note_pages(notebook_id,title,document,created_at,updated_at) VALUES (?,?,?,?,?)", (nid, title, json.dumps(empty_document()), stamp, stamp)).lastrowid
    return page(conn, pid)


def save_page(conn: sqlite3.Connection, page_id: int, revision: Any, document: Any, title: Any) -> dict:
    title, rev = text(title, 200), integer(revision)
    if not title.strip():
        raise NotesError("Page title is required")
    with transaction(conn):
        doc = normalize(document, conn)
        from kitty.core.note_blocks import search_text
        search = search_text(conn, page_id, doc)
        cursor = conn.execute("UPDATE note_pages SET title=?,document=?,search_text=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?", (title, json.dumps(doc, ensure_ascii=False), search, now(), integer(page_id), rev))
        if cursor.rowcount != 1:
            raise NotesError("Page changed in another tab. Export your local copy, then reload; stale edits were not saved.", 409)
    return {"revision": rev + 1}


def save_viewport(conn: sqlite3.Connection, page_id: int, revision: Any, value: Any) -> dict:
    view, rev = viewport(value), integer(revision)
    with transaction(conn):
        if conn.execute("UPDATE note_pages SET viewport=?,viewport_revision=viewport_revision+1 WHERE id=? AND viewport_revision=?", (json.dumps(view), integer(page_id), rev)).rowcount != 1:
            raise NotesError("Viewport changed in another tab; reload the page", 409)
    return {"viewport_revision": rev + 1}


def preferences(conn: sqlite3.Connection, revision: Any, allow_ai: Any, retention_days: Any) -> dict:
    if type(allow_ai) is not bool:
        raise NotesError("AI sharing preference must be boolean")
    rev, days = integer(revision), integer(retention_days, 0, 3650)
    with transaction(conn):
        if conn.execute("UPDATE note_preferences SET allow_ai=?,retention_days=?,revision=revision+1 WHERE id=1 AND revision=?", (allow_ai, days, rev)).rowcount != 1:
            raise NotesError("Privacy settings changed; reload first", 409)
    return {"allow_ai": allow_ai, "retention_days": days, "revision": rev + 1}


def delete_page(conn: sqlite3.Connection, page_id: int, revision: Any) -> dict:
    with transaction(conn):
        if conn.execute("DELETE FROM note_pages WHERE id=? AND revision=?", (integer(page_id), integer(revision))).rowcount != 1:
            raise NotesError("Page changed; reload before deleting", 409)
    return {"ok": True}


def resolve_page(conn, title, notebook_id=None):
    title = text(title, 200).strip()
    if not title:
        raise NotesError("Page title is required")
    with transaction(conn, immediate=True):
        row = conn.execute("SELECT id FROM note_pages WHERE title=? COLLATE NOCASE ORDER BY id LIMIT 1", (title,)).fetchone()
        if row:
            return page(conn, row["id"])
        if notebook_id is None:
            book = conn.execute("SELECT id FROM note_notebooks ORDER BY id LIMIT 1").fetchone()
            notebook_id = book["id"] if book else create_notebook(conn, "Notes")["id"]
        return create_page(conn, notebook_id, title)


def daily_page(conn, date=None):
    date = date if date is not None else datetime.now().date().isoformat()
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise NotesError("Date must be YYYY-MM-DD")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise NotesError("Invalid daily note date") from None
    with transaction(conn, immediate=True):
        row = conn.execute("SELECT page_id FROM note_daily WHERE date=?", (date,)).fetchone()
        if row:
            return page(conn, row["page_id"])
        book = conn.execute("SELECT id FROM note_notebooks WHERE title='Daily notes' ORDER BY id LIMIT 1").fetchone()
        nid = book["id"] if book else create_notebook(conn, "Daily notes")["id"]
        result = create_page(conn, nid, date)
        conn.execute("INSERT INTO note_daily(date,page_id) VALUES (?,?)", (date, result["id"]))
        return result


def organize_page(conn, page_id, body):
    keys(body, {"favorite", "visited"})
    for value in body.values():
        if type(value) is not bool:
            raise NotesError("Page flags must be boolean")
    with transaction(conn):
        page(conn, page_id)
        if "favorite" in body:
            conn.execute("UPDATE note_pages SET favorite=? WHERE id=?", (body["favorite"], page_id))
        if body.get("visited"):
            conn.execute("UPDATE note_pages SET last_opened=? WHERE id=?", (now(), page_id))
    return page(conn, page_id)


def reorder(conn, kind, ids):
    tables = {"pages": "note_pages", "notebooks": "note_notebooks", "folders": "note_folders"}
    if not isinstance(kind, str) or kind not in tables or not isinstance(ids, list) or len(ids) > 500:
        raise NotesError("Invalid note order")
    ids = [integer(value) for value in ids]
    if len(set(ids)) != len(ids):
        raise NotesError("Duplicate ordered item")
    table = tables[kind]
    with transaction(conn, immediate=True):
        for position, item_id in enumerate(ids):
            if conn.execute(f"UPDATE {table} SET position=? WHERE id=?", (position, item_id)).rowcount != 1:
                raise NotesError("Ordered item not found", 404)
    return {"ok": True}


def tags_summary(conn):
    result = []
    for row in conn.execute("SELECT id,title,document FROM note_pages ORDER BY updated_at DESC LIMIT 500"):
        for item in json.loads(row["document"]).get("elements", []):
            if item.get("kind") in {"text", "sticky"} and item.get("tags"):
                result.append({"page_id": row["id"], "title": row["title"], "element_id": item["id"], "text": item.get("text", ""), "tags": item["tags"], "checked": item.get("checked", False)})
    for row in conn.execute("SELECT b.id,b.page_id,b.text,p.title FROM note_blocks b JOIN note_pages p ON p.id=b.page_id WHERE EXISTS(SELECT 1 FROM note_block_topics t WHERE t.block_id=b.id AND t.kind='tag') ORDER BY p.updated_at DESC,b.ordering LIMIT 2000"):
        tags = [item["topic"] for item in conn.execute("SELECT topic FROM note_block_topics WHERE block_id=? AND kind='tag' ORDER BY topic", (row["id"],))]
        result.append({"page_id": row["page_id"], "title": row["title"], "block_id": row["id"], "text": row["text"], "tags": tags, "checked": False})
    return {"tags": result}


def attachment_root(data_dir: Path) -> Path:
    root = data_dir / "notes-attachments"
    if root.is_symlink():
        raise NotesError("Attachment storage cannot be a symbolic link", 409)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root.resolve()


def _storage_usage(conn: sqlite3.Connection, root: Path) -> tuple[int, int]:
    # Count crash-orphaned raster files too: repeated interrupted imports cannot
    # evade the quota just because their SQLite transaction never committed.
    disk_bytes, disk_count = 0, 0
    for path in root.iterdir():
        if re.fullmatch(r"[a-f0-9]{32}\.png", path.name) and path.is_file() and not path.is_symlink():
            disk_bytes += path.stat().st_size
            disk_count += 1
    row = conn.execute("SELECT COALESCE(SUM(byte_size),0),COUNT(*) FROM note_attachments").fetchone()
    return max(disk_bytes, row[0]), max(disk_count, row[1])


def _decode(value: Any) -> bytes:
    encoded = text(value, MAX_IMPORT_BYTES * 4 // 3 + 4, "Attachment")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise NotesError("Attachment must be base64") from None
    if not data or len(data) > MAX_IMPORT_BYTES:
        raise NotesError("Import must be between 1 byte and 12 MiB")
    return data


def rasterize(data: bytes) -> list[tuple[bytes, int, int]]:
    """Decode locally and re-encode pixels; no scripts, SVG, or original PDF served."""
    from PIL import Image, UnidentifiedImageError
    output = []
    try:
        if data.startswith(b"%PDF-"):
            import pymupdf
            with pymupdf.open(stream=data, filetype="pdf") as pdf:
                if pdf.is_encrypted or not 1 <= len(pdf) <= 20:
                    raise NotesError("PDF must be unencrypted with 1–20 pages")
                total_pixels = 0
                for pdf_page in pdf:
                    rect = pdf_page.rect
                    if not math.isfinite(rect.width) or not math.isfinite(rect.height) or rect.width <= 0 or rect.height <= 0 or rect.width > 20000 or rect.height > 20000:
                        raise NotesError("PDF dimensions exceed import limits")
                    scale = min(1.5, 2400 / max(rect.width, rect.height))
                    pix = pdf_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                    total_pixels += pix.width * pix.height
                    if total_pixels > 40_000_000:
                        raise NotesError("PDF exceeds the 40 million pixel import limit")
                    encoded = pix.tobytes("png")
                    if len(encoded) > MAX_ATTACHMENT_BYTES:
                        raise NotesError("PDF raster page exceeds 8 MiB")
                    output.append((encoded, pix.width, pix.height))
        else:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise NotesError("Only PNG, JPEG, WebP and PDF imports are allowed")
                if image.width * image.height > MAX_PIXELS or max(image.size) > 12000:
                    raise NotesError("Image exceeds 16 million pixels or 12000 pixels per side")
                image.load()
                image = image.convert("RGBA")
                stream = io.BytesIO()
                image.save(stream, format="PNG")
                encoded = stream.getvalue()
                if len(encoded) > MAX_ATTACHMENT_BYTES:
                    raise NotesError("Normalized image exceeds 8 MiB")
                output.append((encoded, image.width, image.height))
    except NotesError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError, RuntimeError):
        raise NotesError("Image or PDF is corrupt or unsupported") from None
    return output


def register(conn: sqlite3.Connection, data_dir: Path, data: Any, name: Any) -> list[dict]:
    name = text(name, 200, "Attachment name")
    rasters = rasterize(_decode(data))
    root, created = attachment_root(data_dir), []
    try:
        with transaction(conn):
            used, count = _storage_usage(conn, root)
            if used + sum(len(data) for data, _, _ in rasters) > MAX_STORAGE_BYTES or count + len(rasters) > 1000:
                raise NotesError("Local references exceed 128 MiB or 1000 images; remove unused references first")
            result = []
            for index, (pixels, width, height) in enumerate(rasters):
                aid = uuid.uuid4().hex
                path = root / f"{aid}.png"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created.append(path)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(pixels)
                label = name if len(rasters) == 1 else f"{name[:180]} · page {index + 1}"
                conn.execute("INSERT INTO note_attachments VALUES (?,?,?,?,?,?,?)", (aid, label, str(path), len(pixels), width, height, now()))
                result.append({"id": aid, "name": label, "width": width, "height": height, "url": f"/api/notes/attachments/{aid}"})
            return result
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def attachment_bytes(conn: sqlite3.Connection, data_dir: Path, attachment_id: str) -> bytes:
    row = conn.execute("SELECT * FROM note_attachments WHERE id=?", (identifier(attachment_id),)).fetchone()
    if row is None:
        raise NotesError("Registered reference not found", 404)
    root = attachment_root(data_dir)
    path = Path(row["filepath"])
    if path.is_symlink() or path.resolve().parent != root or path.name != f"{attachment_id}.png":
        raise NotesError("Registered reference path is invalid", 404)
    try:
        if path.stat().st_size != row["byte_size"] or row["byte_size"] > MAX_ATTACHMENT_BYTES:
            raise NotesError("Registered reference changed or is unavailable", 404)
        data = path.read_bytes()
    except OSError:
        raise NotesError("Registered reference unavailable", 404) from None
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise NotesError("Registered reference is not a normalized image", 404)
    return data


def export_page(conn: sqlite3.Connection, data_dir: Path, page_id: int) -> dict:
    result = page(conn, page_id)
    refs = {item["attachment"] for item in result["document"]["elements"] if item["kind"] == "image"}
    attachments = []
    size = 0
    for aid in sorted(refs):
        data = attachment_bytes(conn, data_dir, aid)
        size += len(data)
        if size > MAX_IMPORT_BYTES:
            raise NotesError("Portable page exceeds 12 MiB of references; export drawings or split references across pages")
        attachments.append({"id": aid, "data": base64.b64encode(data).decode(), "name": "Reference.png"})
    notebook = dict(conn.execute("SELECT title,subject,project FROM note_notebooks WHERE id=?", (result["notebook_id"],)).fetchone())
    from kitty.core.note_blocks import get_blocks
    return {"format": "kitty-notes", "version": 1, "title": result["title"], "notebook": notebook, "document": result["document"], "viewport": result["viewport"], "attachments": attachments, "blocks": get_blocks(conn, page_id)["blocks"]}


def restore(conn: sqlite3.Connection, data_dir: Path, notebook_id: Any, value: Any) -> dict:
    keys(value, {"format", "version", "title", "notebook", "document", "viewport", "attachments", "blocks"}, {"format", "version", "title", "document", "viewport", "attachments"})
    if value["format"] != "kitty-notes" or type(value["version"]) is not int or value["version"] != 1:
        raise NotesError("Not a kitty notebook export")
    title, view = text(value["title"], 200), viewport(value["viewport"])
    doc = normalize(value["document"])
    from kitty.core import note_blocks
    blocks = note_blocks.normalize(value.get("blocks", []))
    block_mapping = {block["id"]: str(uuid.uuid4()) for block in blocks}
    for block in blocks:
        block["id"] = block_mapping[block["id"]]
        block["parent_id"] = block_mapping.get(block["parent_id"])
        block["text"] = note_blocks.REF.sub(lambda match: match[0].replace(match[1] or match[2], block_mapping.get((match[1] or match[2]).lower(), match[1] or match[2])), block["text"])
    if not title.strip() or not isinstance(value["attachments"], list) or len(value["attachments"]) > 100:
        raise NotesError("Invalid portable page references or title")
    nid = integer(notebook_id, 0)
    notebook = value.get("notebook")
    if nid == 0:
        keys(notebook, {"title", "subject", "project"}, {"title", "subject", "project"})
        notebook = {key: text(notebook[key], 200) for key in ("title", "subject", "project")}
        if not notebook["title"].strip():
            raise NotesError("Exported notebook title is required")
    # Validate every raster before committing anything, then install atomically.
    prepared, total, ref_ids = [], 0, set()
    for item in value["attachments"]:
        keys(item, {"id", "name", "data"}, {"id", "name", "data"})
        old = identifier(item["id"])
        if old in ref_ids:
            raise NotesError("Duplicate imported attachment")
        ref_ids.add(old)
        raw = _decode(item["data"])
        total += len(raw)
        if total > MAX_IMPORT_BYTES:
            raise NotesError("Portable references exceed 12 MiB")
        pixels = rasterize(raw)
        if len(pixels) != 1:
            raise NotesError("Portable references must each contain one image")
        prepared.append((old, text(item["name"], 200), pixels[0]))
    wanted = {item["attachment"] for item in doc["elements"] if item["kind"] == "image"}
    if wanted != ref_ids:
        raise NotesError("Portable page must include exactly its referenced images")
    root, created = attachment_root(data_dir), []
    try:
        with transaction(conn):
            if nid == 0:
                if conn.execute("SELECT COUNT(*) FROM note_notebooks").fetchone()[0] >= 200:
                    raise NotesError("Notebook limit reached")
                stamp = now()
                nid = conn.execute("INSERT INTO note_notebooks(title,subject,project,created_at,updated_at) VALUES (?,?,?,?,?)", (notebook["title"], notebook["subject"], notebook["project"], stamp, stamp)).lastrowid
            if not conn.execute("SELECT 1 FROM note_notebooks WHERE id=?", (nid,)).fetchone():
                raise NotesError("Notebook not found", 404)
            if conn.execute("SELECT COUNT(*) FROM note_pages").fetchone()[0] >= MAX_PAGES:
                raise NotesError("Page limit reached")
            used, count = _storage_usage(conn, root)
            if used + sum(len(r[2][0]) for r in prepared) > MAX_STORAGE_BYTES or count + len(prepared) > 1000:
                raise NotesError("Local reference storage limit reached")
            mapping = {}
            for old, label, (pixels, width, height) in prepared:
                aid = uuid.uuid4().hex
                path = root / f"{aid}.png"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created.append(path)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(pixels)
                conn.execute("INSERT INTO note_attachments VALUES (?,?,?,?,?,?,?)", (aid, label, str(path), len(pixels), width, height, now()))
                mapping[old] = aid
            for item in doc["elements"]:
                if item["kind"] == "image":
                    item["attachment"] = mapping[item["attachment"]]
            stamp = now()
            search = "\n".join(item.get("text", "") for item in doc["elements"])
            pid = conn.execute("INSERT INTO note_pages(notebook_id,title,document,search_text,viewport,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (nid, title, json.dumps(doc), search, json.dumps(view), stamp, stamp)).lastrowid
            if blocks:
                note_blocks.save_blocks(conn, pid, 1, blocks)
        return page(conn, pid)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def cleanup(conn: sqlite3.Connection, data_dir: Path) -> dict:
    """Explicit retention cleanup only removes unreferenced rasters, never pages."""
    root, removed = attachment_root(data_dir), 0
    with transaction(conn):
        # Establish the SQLite writer lock before inspecting references or
        # touching files, so concurrent saves cannot race retention cleanup.
        conn.execute("UPDATE note_preferences SET revision=revision WHERE id=1")
        days = conn.execute("SELECT retention_days FROM note_preferences WHERE id=1").fetchone()[0]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        referenced = set()
        for row in conn.execute("SELECT document FROM note_pages"):
            referenced.update(item["attachment"] for item in json.loads(row[0])["elements"] if item["kind"] == "image")
        for row in conn.execute("SELECT * FROM note_attachments WHERE created_at <= ?", (cutoff,)).fetchall():
            if row["id"] in referenced:
                continue
            path = Path(row["filepath"])
            if not path.is_symlink() and path.resolve().parent == root and path.name == f"{row['id']}.png":
                path.unlink(missing_ok=True)
                conn.execute("DELETE FROM note_attachments WHERE id=?", (row["id"],))
                removed += 1
        registered = {row[0] for row in conn.execute("SELECT id FROM note_attachments")}
        cutoff_epoch = datetime.fromisoformat(cutoff).timestamp()
        for path in root.iterdir():
            if re.fullmatch(r"[a-f0-9]{32}\.png", path.name) and path.stem not in registered and not path.is_symlink() and path.is_file() and path.stat().st_mtime <= cutoff_epoch:
                path.unlink()
                removed += 1
    return {"removed": removed}
