"""Local Anki-compatible notes and deterministic FSRS-6 review scheduling.

Only allowlisted formatting and stored media reach the review surface. Fronts are
served separately from answers; practice reviews never mutate regular schedules.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
import json
import re
import secrets
import sqlite3
import tempfile
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from kitty.db.connection import transaction

DEFAULT_SETTINGS = {"new_limit": 20, "review_limit": 200, "retention": 0.9,
                    "learning_steps": [60, 600], "relearning_steps": [600],
                    "maximum_interval": 36500, "bury_siblings": True,
                    "leech_threshold": 8, "timezone": "UTC"}
CLOZE = re.compile(r"\{\{c([1-9][0-9]*)::(.*?)(?:::(.*?))?\}\}", re.S)
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_MEDIA = 8 * 1024 * 1024


def now():
    return datetime.now(timezone.utc)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def require(conn, table, ident):
    if table not in {"flash_decks", "flash_types", "flash_notes", "flash_cards"}:
        raise ValueError("Invalid entity")
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise ValueError("Flashcard item not found")
    return dict(row)


def settings(value):
    if not isinstance(value, dict) or set(value) - DEFAULT_SETTINGS.keys():
        raise ValueError("Unknown deck setting")
    result = DEFAULT_SETTINGS | value
    for key, maximum in (("new_limit", 10000), ("review_limit", 10000),
                         ("leech_threshold", 100), ("maximum_interval", 36500)):
        if type(result[key]) is not int or not 0 <= result[key] <= maximum or (key in {"leech_threshold", "maximum_interval"} and result[key] == 0):
            raise ValueError(f"Invalid {key}")
    if not isinstance(result["retention"], (int, float)) or not 0.7 <= result["retention"] <= 0.99:
        raise ValueError("Retention must be between 0.70 and 0.99")
    for key in ("learning_steps", "relearning_steps"):
        steps = result[key]
        if not isinstance(steps, list) or len(steps) > 10 or any(type(x) is not int or not 1 <= x <= 86400 for x in steps) or steps != sorted(steps):
            raise ValueError("Learning steps must be increasing seconds (1–86400)")
    if type(result["bury_siblings"]) is not bool:
        raise ValueError("Sibling bury must be boolean")
    try:
        ZoneInfo(result["timezone"])
    except (ValueError, KeyError, TypeError):
        raise ValueError("Unknown IANA timezone") from None
    return result


def bootstrap(conn):
    conn.execute("INSERT OR IGNORE INTO flash_decks(name,settings,created_at) VALUES(?,?,?)",
                 ("Default", encode(DEFAULT_SETTINGS), now().isoformat()))
    for name, kind, fields, templates in (
        ("Basic", "basic", ["Front", "Back"], [{"name": "Card 1", "front": "{{Front}}", "back": "{{FrontSide}}<hr>{{Back}}"}]),
        ("Basic (reversed)", "reversed", ["Front", "Back"], [{"name": "Forward", "front": "{{Front}}", "back": "{{Back}}"}, {"name": "Reverse", "front": "{{Back}}", "back": "{{Front}}"}]),
        ("Cloze", "cloze", ["Text", "Extra"], [{"name": "Cloze", "front": "{{cloze:Text}}", "back": "{{cloze:Text}}<hr>{{Extra}}"}]),
    ):
        conn.execute("INSERT OR IGNORE INTO flash_types(name,kind,fields,templates) VALUES(?,?,?,?)", (name, kind, encode(fields), encode(templates)))


def save_deck(conn, body):
    name = str(body.get("name", "")).strip()
    if not name or len(name) > 200 or any(not p.strip() for p in name.split("::")):
        raise ValueError("Deck name required; use Parent::Child for subdecks")
    cfg = settings(body.get("settings", {}))
    ident = body.get("id")
    with transaction(conn):
        parent = None
        parts = name.split("::")
        for index in range(1, len(parts)):
            ancestor = "::".join(parts[:index])
            conn.execute("INSERT OR IGNORE INTO flash_decks(name,parent_id,settings,created_at) VALUES(?,?,?,?)", (ancestor, parent, encode(DEFAULT_SETTINGS), now().isoformat()))
            parent = conn.execute("SELECT id FROM flash_decks WHERE name=?", (ancestor,)).fetchone()[0]
        if ident:
            old = require(conn, "flash_decks", ident)
            if old["revision"] != body.get("revision"):
                raise ValueError("Deck changed; reload before saving")
            if name != old["name"]:
                descendants = conn.execute("SELECT id,name FROM flash_decks WHERE substr(name,1,?)=?", (len(old["name"]) + 2, old["name"] + "::")).fetchall()
                if name.startswith(old["name"] + "::"):
                    raise ValueError("Cannot move a deck inside itself")
                for child in descendants:
                    conn.execute("UPDATE flash_decks SET name=?,revision=revision+1 WHERE id=?", (name + child["name"][len(old["name"]):], child["id"]))
            conn.execute("UPDATE flash_decks SET name=?,parent_id=?,settings=?,archived=?,revision=revision+1 WHERE id=?", (name, parent, encode(cfg), int(bool(body.get("archived", False))), ident))
        else:
            ident = conn.execute("INSERT INTO flash_decks(name,parent_id,settings,created_at) VALUES(?,?,?,?)", (name, parent, encode(cfg), now().isoformat())).lastrowid
    return require(conn, "flash_decks", ident)


def _validate_type(name, kind, fields, templates):
    if not name or len(name) > 200 or kind not in {"basic", "reversed", "cloze", "template"}:
        raise ValueError("Invalid note type")
    if not isinstance(fields, list) or not 1 <= len(fields) <= 30 or any(not isinstance(f, str) or not f.strip() or len(f) > 80 or any(c in "{}:" or ord(c) < 32 for c in f) for f in fields) or len(set(fields)) != len(fields):
        raise ValueError("Provide unique field names")
    if not isinstance(templates, list) or not 1 <= len(templates) <= 20:
        raise ValueError("Provide 1–20 card templates")
    for template in templates:
        if not isinstance(template, dict) or any(not isinstance(template.get(k), str) or len(template[k]) > 20000 for k in ("name", "front", "back")):
            raise ValueError("Each template needs name, front and back")
        for side in ("front", "back"):
            _conditional_template(template[side], dict.fromkeys(fields, "present"))
            for token in re.findall(r"\{\{([^{}]+)\}\}", template[side]):
                if ":" in token and token.rsplit(":", 1)[0] not in {"cloze", "type", "text", "type:cloze", "type:text"}:
                    raise ValueError("Unsupported Anki template field filter; supported filters are cloze, type and text")


def save_type(conn, body):
    name, kind = str(body.get("name", "")).strip(), body.get("kind", "template")
    fields, templates = body.get("fields"), body.get("templates")
    _validate_type(name, kind, fields, templates)
    with transaction(conn):
        ident = body.get("id")
        if ident:
            old = require(conn, "flash_types", ident)
            if old["revision"] != body.get("revision"):
                raise ValueError("Note type changed; reload")
            if json.loads(old["fields"]) != fields and conn.execute("SELECT 1 FROM flash_notes WHERE type_id=? LIMIT 1", (ident,)).fetchone():
                raise ValueError("Fields of an in-use type cannot be renamed; create a new type and edit notes")
            conn.execute("UPDATE flash_types SET name=?,kind=?,fields=?,templates=?,revision=revision+1 WHERE id=?", (name, kind, encode(fields), encode(templates), ident))
            for note in conn.execute("SELECT * FROM flash_notes WHERE type_id=?", (ident,)).fetchall():
                _sync_cards(conn, dict(note), kind, templates)
        else:
            ident = conn.execute("INSERT INTO flash_types(name,kind,fields,templates) VALUES(?,?,?,?)", (name, kind, encode(fields), encode(templates))).lastrowid
    return require(conn, "flash_types", ident)


def _sync_cards(conn, note, kind, templates):
    fields = json.loads(note["fields"])
    if kind == "cloze":
        cloze_fields = set(re.findall(r"\{\{(?:type:)?cloze:([^{}]+)\}\}", templates[0]["front"]))
        ordinals = sorted({int(m.group(1)) - 1 for name in cloze_fields for m in CLOZE.finditer(fields.get(name, ""))})
        if not ordinals: raise ValueError("Cloze front needs {{cloze:Field}} and text with {{c1::answer::optional hint}}")
    else:
        ordinals = []
        for ordinal in range(len(templates)):
            front = render(conn, {"note_id": note["id"], "ordinal": ordinal})
            text = _PlainText()
            text.feed(front)
            if "".join(text.parts).strip() or re.search(r"<(?:img|audio)\b", front):
                ordinals.append(ordinal)
        if not ordinals: raise ValueError("A note needs at least one nonempty card front")
    existing = {row[0] for row in conn.execute("SELECT ordinal FROM flash_cards WHERE note_id=?", (note["id"],))}
    for ordinal in set(ordinals) - existing:
        conn.execute("INSERT INTO flash_cards(note_id,ordinal,due) VALUES(?,?,?)", (note["id"], ordinal, now().isoformat()))
    for ordinal in existing - set(ordinals):
        conn.execute("DELETE FROM flash_cards WHERE note_id=? AND ordinal=?", (note["id"], ordinal))
    conn.execute("UPDATE flash_cards SET revision=revision+1 WHERE note_id=?", (note["id"],))


def _save_note(conn, body):
    typ = require(conn, "flash_types", body.get("type_id"))
    require(conn, "flash_decks", body.get("deck_id"))
    fields, tags = body.get("fields"), body.get("tags", [])
    if not isinstance(fields, dict) or set(fields) != set(json.loads(typ["fields"])) or any(not isinstance(v, str) or len(v) > 100000 for v in fields.values()):
        raise ValueError("Fields must match note type (up to 100000 characters each)")
    if not isinstance(tags, list) or len(tags) > 100 or any(not isinstance(t, str) or not re.fullmatch(r"[^\s]{1,80}", t) for t in tags):
        raise ValueError("Tags must be short words without spaces")
    ident = body.get("id")
    timestamp = now().isoformat()
    if ident:
        old = require(conn, "flash_notes", ident)
        if old["revision"] != body.get("revision"):
            raise ValueError("Note changed; reload before editing")
        conn.execute("UPDATE flash_notes SET deck_id=?,type_id=?,fields=?,tags=?,archived=?,updated_at=?,revision=revision+1 WHERE id=?", (body["deck_id"], body["type_id"], encode(fields), encode(sorted(set(tags))), int(bool(body.get("archived", False))), timestamp, ident))
    else:
        ident = conn.execute("INSERT INTO flash_notes(guid,deck_id,type_id,fields,tags,archived,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (body.get("guid") or uuid.uuid4().hex, body["deck_id"], body["type_id"], encode(fields), encode(sorted(set(tags))), int(bool(body.get("archived", False))), timestamp, timestamp)).lastrowid
    note = require(conn, "flash_notes", ident)
    _sync_cards(conn, note, typ["kind"], json.loads(typ["templates"]))
    return note


def save_note(conn, body):
    with transaction(conn):
        return _save_note(conn, body)


def media_mime(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if data.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")): return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return "image/webp"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE": return "audio/wav"
    if data.startswith(b"OggS"): return "audio/ogg"
    if data.startswith(b"ID3") or (len(data) > 2 and data[0] == 255 and data[1] & 224 == 224): return "audio/mpeg"
    raise ValueError("Media must be PNG/JPEG/GIF/WebP or MP3/Ogg/WAV; HTML/SVG are not allowed")


def _store_media(conn, name, data):
    if not isinstance(name, str) or not name or len(name) > 200 or "/" in name or "\\" in name or name in {".", ".."} or any(ord(c) < 32 for c in name):
        raise ValueError("Media filename must be a safe basename")
    if len(data) > MAX_MEDIA:
        raise ValueError("Media exceeds 8 MiB")
    mime = media_mime(data)
    digest = hashlib.sha256(data).hexdigest()
    old = conn.execute("SELECT sha256 FROM flash_media WHERE name=?", (name,)).fetchone()
    if old and old[0] != digest:
        raise ValueError(f"Media name {name} already contains different content; rename before importing")
    conn.execute("INSERT OR IGNORE INTO flash_media(name,mime,content,sha256) VALUES(?,?,?,?)", (name, mime, data, digest))
    return {"name": name, "mime": mime}


def upload_media(conn, name, data):
    with transaction(conn):
        return _store_media(conn, name, data)


class SafeHTML(HTMLParser):
    tags = {"b", "strong", "i", "em", "u", "s", "br", "p", "div", "span", "ul", "ol", "li", "blockquote", "pre", "code", "hr", "sub", "sup", "h1", "h2", "h3", "table", "thead", "tbody", "tr", "td", "th"}

    def __init__(self, conn):
        super().__init__(convert_charrefs=True)
        self.conn, self.parts, self.hidden = conn, [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object"}:
            self.hidden += 1
            return
        if self.hidden: return
        if tag in self.tags:
            self.parts.append(f"<{tag}>")
        elif tag == "img":
            values = dict(attrs)
            name = values.get("src", "")
            self.parts.append(self.attachment(name, image=True))

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in self.tags and tag not in {"br", "hr"}:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(html.escape(data))

    def attachment(self, name, image=False):
        row = self.conn.execute("SELECT mime,content FROM flash_media WHERE name=?", (name,)).fetchone()
        if row is None:
            row = self.conn.execute("SELECT mime,content FROM flash_media WHERE name=?", (unquote(name),)).fetchone()
        if row is None: return "[media unavailable]"
        uri = "data:" + row["mime"] + ";base64," + base64.b64encode(row["content"]).decode()
        if image and row["mime"].startswith("image/"):
            return f'<img src="{uri}" alt="{html.escape(name, quote=True)}" loading="lazy">'
        if not image and row["mime"].startswith("audio/"):
            return f'<audio controls preload="none" src="{uri}"></audio>'
        return "[unsupported media reference]"


def safe_html(conn, text):
    parser = SafeHTML(conn)
    # Audio placeholders cannot contain markup; substitute only after sanitization.
    audio = []
    def sound(match):
        audio.append(parser.attachment(match.group(1)))
        return f"KITTYAUDIOPLACEHOLDER{len(audio)-1}END"
    text = re.sub(r"\[sound:([^\]\n]+)\]", sound, text)
    # HTML remains allowlisted; markdown transformations don't introduce attributes.
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    parser.feed(text.replace("\n", "<br>"))
    result = "".join(parser.parts)
    for index, fragment in enumerate(audio):
        result = result.replace(f"KITTYAUDIOPLACEHOLDER{index}END", fragment)
    return result


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _conditional_template(source, fields):
    parts, stack, visible, position = [], [], True, 0
    for match in re.finditer(r"\{\{([#^/])([^{}]+)\}\}", source):
        if visible: parts.append(source[position:match.start()])
        operation, name = match.group(1), match.group(2)
        if operation == "/":
            if not stack or stack[-1][0] != name:
                raise ValueError("Template conditional sections must be correctly nested")
            _, visible = stack.pop()
        else:
            stack.append((name, visible))
            visible = visible and (bool(fields.get(name, "")) == (operation == "#"))
        position = match.end()
    if stack: raise ValueError("Template conditional section is not closed")
    if visible: parts.append(source[position:])
    return "".join(parts)


def _render_source(conn, card, answer=False):
    note = require(conn, "flash_notes", card["note_id"])
    typ = require(conn, "flash_types", note["type_id"])
    fields, templates = json.loads(note["fields"]), json.loads(typ["templates"])
    template = templates[0] if typ["kind"] == "cloze" else templates[card["ordinal"]]
    deck = require(conn, "flash_decks", note["deck_id"])
    for key, value in {"Tags": " ".join(json.loads(note["tags"])), "Type": typ["name"], "Deck": deck["name"], "Subdeck": deck["name"].split("::")[-1], "Card": template["name"]}.items():
        fields.setdefault(key, value)
    def fill(source, reveal, allow_front_side=True):
        source = _conditional_template(source, fields)
        def token(match):
            key = match.group(1)
            if key == "FrontSide": return fill(template["front"], False, False) if allow_front_side else ""
            if key.startswith("type:"):
                if not reveal: return "[type your answer]"
                key = key[5:]
            if key.startswith("cloze:"):
                text = fields.get(key[6:], "")
                return CLOZE.sub(lambda m: m.group(2) if reveal or int(m.group(1)) - 1 != card["ordinal"] else "[" + (m.group(3) or "…") + "]", text)
            if key.startswith("text:"):
                collector = _PlainText()
                collector.feed(fields.get(key[5:], ""))
                return html.escape("".join(collector.parts))
            return fields.get(key, "")
        return re.sub(r"\{\{([^{}]+)\}\}", token, source)
    return fill(template["back" if answer else "front"], answer)


def render(conn, card, answer=False):
    return safe_html(conn, _render_source(conn, card, answer))


class _MediaReferences(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.names = set()

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            source = dict(attrs).get("src")
            if source: self.names.update((source, unquote(source)))

    def collect(self, source):
        self.feed(source)
        self.close()
        for name in re.findall(r"\[sound:([^\]\n]+)\]", source):
            self.names.update((html.unescape(name), unquote(html.unescape(name))))
        self.reset()


def _rows(conn, deck_id=None, search=""):
    rows = [dict(row) for row in conn.execute("SELECT c.*,n.deck_id,n.type_id,n.fields,n.tags,n.archived AS note_archived,d.name AS deck_name,d.settings,d.archived AS deck_archived,n.revision AS note_revision FROM flash_cards c JOIN flash_notes n ON n.id=c.note_id JOIN flash_decks d ON d.id=n.deck_id ORDER BY c.due,c.id")]
    if deck_id:
        deck = require(conn, "flash_decks", deck_id)
        rows = [r for r in rows if r["deck_id"] == deck_id or r["deck_name"].startswith(deck["name"] + "::")]
    terms = search.lower().split()
    for term in terms:
        if term.startswith("tag:"):
            rows = [r for r in rows if term[4:] in [t.lower() for t in json.loads(r["tags"])]]
        elif term.startswith("nid:"):
            rows = [r for r in rows if str(r["note_id"]) == term[4:]]
        elif term.startswith("is:"):
            value = term[3:]
            rows = [r for r in rows if (r["state"] == value or value == "suspended" and r["suspended"] or value == "leech" and r["leech"] or value == "archived" and (r["note_archived"] or r["deck_archived"]) or value == "due" and r["state"] != "new" and r["due"] <= now().isoformat())]
        elif term.startswith("deck:"):
            rows = [r for r in rows if term[5:] in r["deck_name"].lower()]
        else:
            rows = [r for r in rows if term in (r["fields"] + r["tags"]).lower()]
    return rows


def _eligible(conn, rows, moment):
    result, usage = [], {}
    decks = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM flash_decks")}
    for row in rows:
        if row["suspended"] or row["note_archived"] or row["buried_until"] and row["buried_until"] > moment.isoformat():
            continue
        parts = row["deck_name"].split("::")
        ancestors = [decks["::".join(parts[:i])] for i in range(1, len(parts)+1)]
        if any(d["archived"] for d in ancestors):
            continue
        if row["state"] != "new" and row["due"] > moment.isoformat():
            continue
        blocked = False
        for deck in ancestors:
            cfg = json.loads(deck["settings"])
            local = moment.astimezone(ZoneInfo(cfg["timezone"]))
            midnight = datetime.combine(local.date(), datetime.min.time(), tzinfo=local.tzinfo).astimezone(timezone.utc)
            tomorrow = datetime.combine(local.date()+timedelta(days=1), datetime.min.time(), tzinfo=local.tzinfo).astimezone(timezone.utc)
            if deck["id"] not in usage:
                counts = conn.execute("SELECT COALESCE(SUM(r.was_new),0),COALESCE(SUM(r.was_review),0) FROM flash_reviews r JOIN flash_decks d ON d.id=r.deck_id WHERE (d.id=? OR substr(d.name,1,?)=?) AND r.reviewed_at>=? AND r.reviewed_at<? AND r.undone=0 AND r.mode='regular'",
                                      (deck["id"], len(deck["name"])+2, deck["name"]+"::", midnight.isoformat(), tomorrow.isoformat())).fetchone()
                usage[deck["id"]] = list(counts)
            if row["state"] == "new" and usage[deck["id"]][0] >= cfg["new_limit"] or row["state"] == "review" and usage[deck["id"]][1] >= cfg["review_limit"]:
                blocked = True
                break
        if blocked:
            continue
        result.append(row)
        # Reserve the remaining daily allowance for the queue preview as well.
        for deck in ancestors:
            if row["state"] == "new": usage[deck["id"]][0] += 1
            elif row["state"] == "review": usage[deck["id"]][1] += 1
    # Intraday learning/relearning always bypass review/new limits.
    return sorted(result, key=lambda r: ({"learning": 0, "relearning": 0, "review": 1, "new": 2}[r["state"]], r["due"], r["id"]))


def overview(conn):
    bootstrap(conn)
    decks = [dict(r) for r in conn.execute("SELECT * FROM flash_decks ORDER BY name")]
    types = [dict(r) for r in conn.execute("SELECT * FROM flash_types ORDER BY id")]
    for d in decks: d["settings"] = json.loads(d["settings"])
    for t in types:
        t["fields"], t["templates"] = json.loads(t["fields"]), json.loads(t["templates"])
    all_rows = _rows(conn)
    eligible = _eligible(conn, all_rows, now())
    reviewed_days = {r[0] for r in conn.execute("SELECT DISTINCT local_day FROM flash_reviews WHERE undone=0 AND mode='regular'")}
    stats = {"total": len(all_rows), "new": sum(r["state"] == "new" for r in eligible), "learning": sum(r["state"] in {"learning", "relearning"} for r in eligible), "review": sum(r["state"] == "review" for r in eligible), "due": sum(r["state"] != "new" for r in eligible), "streak": _streak(reviewed_days, now().date()),
             "suspended": sum(bool(r["suspended"]) for r in all_rows), "leeches": sum(bool(r["leech"]) for r in all_rows)}
    # Measured recall: the share of due reviews (not learning steps) remembered in the last 90 days.
    since = (now() - timedelta(days=90)).isoformat()
    names = {d["id"]: d["name"] for d in decks}
    recalled = [(names.get(r[0], ""), r[1], r[2]) for r in conn.execute(
        "SELECT deck_id,SUM(rating>1),COUNT(*) FROM flash_reviews WHERE undone=0 AND mode='regular' AND was_review=1 AND reviewed_at>=? GROUP BY deck_id", (since,))]
    for deck in decks:
        prefix = deck["name"] + "::"
        members = [r for r in eligible if r["deck_name"] == deck["name"] or r["deck_name"].startswith(prefix)]
        deck["due"] = sum(r["state"] != "new" for r in members)
        deck["new"] = sum(r["state"] == "new" for r in members)
        deck["learning"] = sum(r["state"] in {"learning", "relearning"} for r in members)
        deck["review"] = sum(r["state"] == "review" for r in members)
        inner = [(passed, total) for name, passed, total in recalled if name == deck["name"] or name.startswith(prefix)]
        total = sum(count for _, count in inner)
        deck["recall"] = round(sum(passed for passed, _ in inner) / total, 4) if total else None
    return {"decks": decks, "types": types, "stats": stats, "media": [dict(r) for r in conn.execute("SELECT name,mime,length(content) AS size FROM flash_media ORDER BY name")]}


def _streak(days, today):
    """Consecutive reviewed days ending today, or yesterday while today is still open."""
    current, streak = today, 0
    if current.isoformat() not in days: current -= timedelta(days=1)
    while current.isoformat() in days:
        streak += 1
        current -= timedelta(days=1)
    return streak


def _quantile(values, q):
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


MEASURED_BUCKETS = ((1, 1), (3, 3), (7, 7), (14, 14), (30, 30), (60, 60), (36500, 120))


def stats(conn, deck_id=None, tz="UTC"):
    """Read-only memory statistics for a deck (with subdecks) or the collection.

    forecast: review/learning cards due on each of the next 30 local days, overdue in
    today; retention: FSRS decay, target, stability quartiles and measured recall by
    elapsed days; maturity: new/learning/young (<21 d interval)/mature; heatmap: 182
    days of review counts; queue: today's eligible split plus suspended/leech/buried.
    """
    zone = ZoneInfo(tz)
    moment = now()
    today = moment.astimezone(zone).date()
    rows = _rows(conn, deck_id)
    decks = {r["id"]: dict(r) for r in conn.execute("SELECT id,name,archived,settings FROM flash_decks")}
    scope = None
    if deck_id:
        name = decks[deck_id]["name"]
        scope = {ident for ident, d in decks.items() if d["name"] == name or d["name"].startswith(name + "::")}
    archived = [d["name"] for d in decks.values() if d["archived"]]
    active = [r for r in rows if not r["suspended"] and not r["note_archived"]
              and not any(r["deck_name"] == a or r["deck_name"].startswith(a + "::") for a in archived)]
    forecast = [{"day": (today + timedelta(days=offset)).isoformat(), "review": 0, "learning": 0} for offset in range(30)]
    for row in active:
        if row["state"] == "new": continue
        offset = max(0, (datetime.fromisoformat(row["due"]).astimezone(zone).date() - today).days)
        if offset < 30: forecast[offset]["review" if row["state"] == "review" else "learning"] += 1
    maturity = {"new": 0, "learning": 0, "young": 0, "mature": 0}
    stabilities = []
    for row in rows:
        if row["state"] == "new": maturity["new"] += 1
        elif row["state"] != "review": maturity["learning"] += 1
        else:
            memory = json.loads(row["fsrs"]) if row["fsrs"] else {}
            last = memory.get("last_review")
            interval = (datetime.fromisoformat(row["due"]) - datetime.fromisoformat(last)).days if last else 0
            maturity["mature" if interval >= 21 else "young"] += 1
            if memory.get("stability"): stabilities.append(memory["stability"])
    where, args = "", []
    if scope is not None:
        where = f" AND deck_id IN ({','.join('?' * len(scope))})"
        args = sorted(scope)
    # Days in the viewer's timezone (stored local_day follows each deck's timezone).
    counts = Counter(datetime.fromisoformat(r[0]).astimezone(zone).date().isoformat() for r in conn.execute(
        "SELECT reviewed_at FROM flash_reviews WHERE undone=0 AND mode='regular'" + where, args))
    heatmap = [{"day": (today - timedelta(days=back)).isoformat(), "reviews": counts.get((today - timedelta(days=back)).isoformat(), 0)} for back in range(181, -1, -1)]
    reviewed_days = set(counts)
    buckets = [[0, 0] for _ in MEASURED_BUCKETS]
    for reviewed_at, rating, before in conn.execute("SELECT reviewed_at,rating,before_state FROM flash_reviews WHERE undone=0 AND mode='regular' AND was_review=1 AND reviewed_at>=?" + where, ((moment - timedelta(days=365)).isoformat(), *args)):
        last = json.loads(json.loads(before).get("fsrs") or "{}").get("last_review")
        if not last: continue
        elapsed = (datetime.fromisoformat(reviewed_at) - datetime.fromisoformat(last)).total_seconds() / 86400
        index = next(i for i, (limit, _) in enumerate(MEASURED_BUCKETS) if elapsed <= limit)
        buckets[index][0] += rating > 1
        buckets[index][1] += 1
    measured = [{"t": shown, "r": round(passed / total, 4), "n": total} for (_, shown), (passed, total) in zip(MEASURED_BUCKETS, buckets) if total]
    eligible = _eligible(conn, rows, moment)
    durations = conn.execute("SELECT AVG(duration_ms) FROM flash_reviews WHERE undone=0 AND mode='regular' AND reviewed_at>=?" + where, ((moment - timedelta(days=30)).isoformat(), *args)).fetchone()[0]
    target_deck = decks[deck_id] if deck_id else next((d for d in decks.values() if d["name"] == "Default"), None)
    from fsrs import Scheduler
    return {
        "forecast": forecast,
        "retention": {
            "decay": Scheduler().parameters[20],
            "target": json.loads(target_deck["settings"])["retention"] if target_deck else DEFAULT_SETTINGS["retention"],
            "stability": {"weak": _quantile(stabilities, 0.25), "medium": _quantile(stabilities, 0.5), "strong": _quantile(stabilities, 0.75)} if stabilities else None,
            "measured": measured,
        },
        "maturity": maturity,
        "heatmap": heatmap,
        "streak": _streak(reviewed_days, today),
        "queue": {
            "learning": sum(r["state"] in {"learning", "relearning"} for r in eligible),
            "review": sum(r["state"] == "review" for r in eligible),
            "new": sum(r["state"] == "new" for r in eligible),
            "suspended": sum(bool(r["suspended"]) for r in rows),
            "leeches": sum(bool(r["leech"]) for r in rows),
            "buried": sum(bool(r["buried_until"]) and r["buried_until"] > moment.isoformat() for r in rows),
            "seconds_per_card": round(durations / 1000, 3) if durations is not None else None,
        },
    }


def browser(conn, deck_id=None, search="", limit=100, offset=0):
    rows = _rows(conn, deck_id, search)
    notes = {}
    for row in rows:
        ident = row["note_id"]
        if ident not in notes:
            note = require(conn, "flash_notes", ident)
            note["fields"], note["tags"] = json.loads(note["fields"]), json.loads(note["tags"])
            note["cards"] = []
            notes[ident] = note
        notes[ident]["cards"].append({key: row[key] for key in ("id", "ordinal", "state", "due", "suspended", "buried_until", "leech", "lapses", "reviews", "revision")})
    return {"notes": list(notes.values())[offset:offset+limit], "total": len(notes)}


def _scheduler(cfg):
    try:
        from fsrs import Scheduler
    except ImportError:
        raise ValueError("Install fsrs>=6.3,<7 to enable spaced repetition") from None
    return Scheduler(desired_retention=cfg["retention"], learning_steps=tuple(timedelta(seconds=s) for s in cfg["learning_steps"]), relearning_steps=tuple(timedelta(seconds=s) for s in cfg["relearning_steps"]), maximum_interval=cfg["maximum_interval"], enable_fuzzing=False)


def _fsrs_card(card):
    from fsrs import Card
    return Card.from_json(card["fsrs"]) if card["fsrs"] else Card(card_id=card["id"], due=datetime.fromisoformat(card["due"]))


def start_review(conn, deck_id=None, search="", mode="regular", study_id=None):
    if mode not in {"regular", "cram", "filtered"}: raise ValueError("Unknown study mode")
    rows = _rows(conn, deck_id, search)
    moment = now()
    conn.execute("DELETE FROM flash_studies WHERE created_at<?", ((moment-timedelta(days=7)).isoformat(),))
    if mode == "regular":
        rows = _eligible(conn, rows, moment)
        study_id = None
    else:
        archived = {r[0] for r in conn.execute("SELECT name FROM flash_decks WHERE archived=1")}
        rows = [r for r in rows if not r["suspended"] and not r["note_archived"] and not any(r["deck_name"] == name or r["deck_name"].startswith(name+"::") for name in archived)]
        if study_id:
            study = conn.execute("SELECT * FROM flash_studies WHERE id=?", (study_id,)).fetchone()
            if study is None or study["mode"] != mode or study["deck_id"] != deck_id or study["search"] != search:
                raise ValueError("Practice session expired or filter changed; start a new session")
            order = json.loads(study["card_ids"])
        else:
            study_id = secrets.token_hex(24)
            order = [r["id"] for r in rows]
            secrets.SystemRandom().shuffle(order)
            conn.execute("INSERT INTO flash_studies(id,mode,deck_id,search,card_ids,created_at) VALUES(?,?,?,?,?,?)", (study_id, mode, deck_id, search, encode(order), moment.isoformat()))
        completed = {r[0] for r in conn.execute("SELECT card_id FROM flash_sessions WHERE study_id=? AND consumed=1", (study_id,))}
        lookup = {r["id"]: r for r in rows}
        rows = [lookup[ident] for ident in order if ident in lookup and ident not in completed]
    if not rows: return {"card": None, "remaining": 0, "study_id": study_id}
    card = rows[0]
    token = secrets.token_hex(24)
    conn.execute("DELETE FROM flash_sessions WHERE study_id IS NULL AND created_at<?", ((moment - timedelta(days=1)).isoformat(),))
    conn.execute("INSERT INTO flash_sessions(token,card_id,revision,mode,created_at,study_id) VALUES(?,?,?,?,?,?)", (token, card["id"], card["revision"], mode, moment.isoformat(), study_id))
    note_type = require(conn, "flash_types", card["type_id"])
    templates = json.loads(note_type["templates"])
    template = templates[0] if note_type["kind"] == "cloze" else templates[card["ordinal"]]
    typed = bool(re.search(r"\{\{type:", template["front"]))
    return {"card": {"id": card["id"], "note_id": card["note_id"], "token": token, "front": render(conn, card), "state": card["state"], "mode": mode, "typed": typed}, "remaining": len(rows), "study_id": study_id}


def _session(conn, token):
    row = conn.execute("SELECT * FROM flash_sessions WHERE token=? AND consumed=0", (token,)).fetchone()
    if row is None or row["created_at"] < (now() - timedelta(days=1)).isoformat(): raise ValueError("Review session expired")
    session = dict(row)
    if session["study_id"] and conn.execute("SELECT 1 FROM flash_sessions WHERE study_id=? AND card_id=? AND consumed=1", (session["study_id"], session["card_id"])).fetchone():
        raise ValueError("This card has already been graded in the practice session")
    card = require(conn, "flash_cards", session["card_id"])
    if card["revision"] != session["revision"]: raise ValueError("Card changed; begin a new review")
    return session, card


def reveal(conn, token):
    session, card = _session(conn, token)
    note = require(conn, "flash_notes", card["note_id"])
    cfg = json.loads(require(conn, "flash_decks", note["deck_id"])["settings"])
    previews = []
    if session["mode"] == "regular":
        from fsrs import Rating
        scheduler, fsrs_card, moment = _scheduler(cfg), _fsrs_card(card), now()
        for rating in range(1, 5):
            updated, _ = scheduler.review_card(fsrs_card, Rating(rating), review_datetime=moment)
            previews.append({"rating": rating, "due": updated.due.isoformat(), "seconds": int((updated.due-moment).total_seconds())})
    conn.execute("UPDATE flash_sessions SET revealed=1 WHERE token=?", (token,))
    return {"back": render(conn, card, True), "intervals": previews}


def grade(conn, token, rating, duration_ms=0):
    if type(rating) is not int or rating not in {1, 2, 3, 4}: raise ValueError("Rating must be 1–4")
    if type(duration_ms) is not int or not 0 <= duration_ms <= 3600000: raise ValueError("Invalid review duration")
    with transaction(conn):
        session, card = _session(conn, token)
        if not session["revealed"]: raise ValueError("Reveal the answer before grading")
        note = require(conn, "flash_notes", card["note_id"])
        cfg = json.loads(require(conn, "flash_decks", note["deck_id"])["settings"])
        moment = now()
        siblings = []
        after_revision = card["revision"]
        if session["mode"] == "regular":
            joined = next(row for row in _rows(conn) if row["id"] == card["id"])
            if not _eligible(conn, [joined], moment):
                raise ValueError("Card is no longer eligible (archived, buried, suspended, not due, or daily limit reached)")
            from fsrs import Rating, State
            updated, _ = _scheduler(cfg).review_card(_fsrs_card(card), Rating(rating), review_datetime=moment, review_duration=duration_ms)
            state = {State.Learning: "learning", State.Review: "review", State.Relearning: "relearning"}[updated.state]
            lapses = card["lapses"] + int(card["state"] == "review" and rating == 1)
            leech = int(card["leech"] or lapses >= cfg["leech_threshold"])
            leech_event = card["state"] == "review" and rating == 1 and lapses >= cfg["leech_threshold"] and (lapses - cfg["leech_threshold"]) % max(1, cfg["leech_threshold"] // 2) == 0
            conn.execute("UPDATE flash_cards SET state=?,due=?,fsrs=?,lapses=?,leech=?,suspended=?,reviews=reviews+1,revision=revision+1 WHERE id=?", (state, updated.due.isoformat(), updated.to_json(), lapses, leech, card["suspended"] or leech_event, card["id"]))
            after_revision += 1
            if cfg["bury_siblings"]:
                local = moment.astimezone(ZoneInfo(cfg["timezone"]))
                midnight = datetime.combine(local.date()+timedelta(days=1), datetime.min.time(), tzinfo=local.tzinfo).astimezone(timezone.utc).isoformat()
                for row in conn.execute("SELECT * FROM flash_cards WHERE note_id=? AND id<>?", (card["note_id"], card["id"])).fetchall():
                    sibling = dict(row)
                    siblings.append({"id": sibling["id"], "buried_until": sibling["buried_until"], "before_revision": sibling["revision"], "revision": sibling["revision"] + 1})
                    conn.execute("UPDATE flash_cards SET buried_until=?,revision=revision+1 WHERE id=?", (midnight, sibling["id"]))
        ident = conn.execute("INSERT INTO flash_reviews(card_id,deck_id,rating,reviewed_at,local_day,duration_ms,mode,before_state,after_revision,sibling_state,was_new,was_review,session_token) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (card["id"], note["deck_id"], rating, moment.isoformat(), moment.astimezone(ZoneInfo(cfg["timezone"])).date().isoformat(), duration_ms, session["mode"], encode(card), after_revision, encode(siblings), int(card["state"] == "new"), int(card["state"] == "review"), token)).lastrowid
        conn.execute("UPDATE flash_sessions SET consumed=1 WHERE token=?", (token,))
    return {"review_id": ident, "due": require(conn, "flash_cards", card["id"])["due"]}


def undo(conn):
    with transaction(conn):
        row = conn.execute("SELECT * FROM flash_reviews WHERE undone=0 ORDER BY id DESC LIMIT 1").fetchone()
        if row is None: raise ValueError("No review to undo")
        if row["mode"] == "regular":
            old = json.loads(row["before_state"])
            current = require(conn, "flash_cards", row["card_id"])
            if current["revision"] != row["after_revision"]: raise ValueError("Card edited since review; cannot undo")
            siblings = json.loads(row["sibling_state"])
            for sibling in siblings:
                if require(conn, "flash_cards", sibling["id"])["revision"] != sibling["revision"]: raise ValueError("Sibling changed since review; cannot undo")
            columns = ("state", "due", "fsrs", "suspended", "buried_until", "lapses", "reviews", "leech")
            conn.execute("UPDATE flash_cards SET " + ",".join(f"{c}=?" for c in columns) + ",revision=revision+1 WHERE id=?", (*[old[c] for c in columns], old["id"]))
            restored_revisions = {old["id"]: (old["revision"], current["revision"] + 1)}
            for sibling in siblings:
                conn.execute("UPDATE flash_cards SET buried_until=?,revision=revision+1 WHERE id=?", (sibling["buried_until"], sibling["id"]))
                restored_revisions[sibling["id"]] = (sibling.get("before_revision", sibling["revision"] - 1), sibling["revision"] + 1)
            # Restoring an exact prior state must not make the preceding undo
            # look like an intervening edit. Keep revision counters monotonic.
            for earlier in conn.execute("SELECT id,card_id,after_revision,sibling_state FROM flash_reviews WHERE id<? AND undone=0 AND mode='regular'", (row["id"],)).fetchall():
                expected = earlier["after_revision"]
                change = restored_revisions.get(earlier["card_id"])
                if change and expected == change[0]: expected = change[1]
                previous_siblings = json.loads(earlier["sibling_state"])
                for sibling in previous_siblings:
                    sibling.setdefault("before_revision", sibling["revision"] - 1)
                    change = restored_revisions.get(sibling["id"])
                    if change and sibling["revision"] == change[0]: sibling["revision"] = change[1]
                conn.execute("UPDATE flash_reviews SET after_revision=?,sibling_state=? WHERE id=?", (expected, encode(previous_siblings), earlier["id"]))
        conn.execute("UPDATE flash_reviews SET undone=1 WHERE id=?", (row["id"],))
        conn.execute("UPDATE flash_sessions SET consumed=0,revealed=0,revision=(SELECT revision FROM flash_cards WHERE id=flash_sessions.card_id) WHERE token=?", (row["session_token"],))
    return {"undone": row["id"]}


def action(conn, body):
    ident, operation = body.get("id"), body.get("action")
    table = "flash_notes" if operation in {"delete", "archive"} else "flash_cards"
    with transaction(conn):
        old = require(conn, table, ident)
        if old["revision"] != body.get("revision"): raise ValueError("Item changed; reload before editing")
        if operation == "delete": conn.execute("DELETE FROM flash_notes WHERE id=?", (ident,))
        elif operation == "archive": conn.execute("UPDATE flash_notes SET archived=?,revision=revision+1 WHERE id=?", (int(bool(body.get("value", True))), ident))
        elif operation == "suspend": conn.execute("UPDATE flash_cards SET suspended=?,revision=revision+1 WHERE id=?", (int(bool(body.get("value", True))), ident))
        elif operation == "bury":
            note = require(conn, "flash_notes", old["note_id"])
            cfg = json.loads(require(conn, "flash_decks", note["deck_id"])["settings"])
            local = now().astimezone(ZoneInfo(cfg["timezone"]))
            until = datetime.combine(local.date()+timedelta(days=1), datetime.min.time(), tzinfo=local.tzinfo).astimezone(timezone.utc).isoformat() if body.get("value", True) else None
            conn.execute("UPDATE flash_cards SET buried_until=?,revision=revision+1 WHERE id=?", (until, ident))
        else: raise ValueError("Unknown card action")
    return {"ok": True}


def history(conn, card_id=None):
    where, args = ("WHERE card_id=?", (card_id,)) if card_id else ("", ())
    return {"reviews": [dict(r) for r in conn.execute("SELECT id,card_id,rating,reviewed_at,local_day,duration_ms,mode,undone FROM flash_reviews " + where + " ORDER BY id DESC LIMIT 200", args)]}


def import_text(conn, text, deck_id, type_id, delimiter=","):
    if delimiter not in {",", "\t"}: raise ValueError("Use CSV or TSV")
    typ = require(conn, "flash_types", type_id)
    names = json.loads(typ["fields"])
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if rows and rows[0][:len(names)] == names: rows.pop(0)
    if len(rows) > 10000: raise ValueError("At most 10000 notes per import")
    count = 0
    with transaction(conn):
        for row in rows:
            if not row: continue
            if len(row) not in {len(names), len(names)+1}: raise ValueError("Each row must have note fields and optional final space-separated tags")
            _save_note(conn, {"deck_id": deck_id, "type_id": type_id, "fields": dict(zip(names, row)), "tags": row[len(names)].split() if len(row) > len(names) else []})
            count += 1
    return {"imported": count}


def export_text(conn, deck_id=None, delimiter=","):
    if delimiter not in {",", "\t"}: raise ValueError("Use CSV or TSV")
    notes = browser(conn, deck_id, limit=100000)["notes"]
    types = {n["type_id"] for n in notes}
    if len(types) > 1: raise ValueError("CSV export requires one note type; use APKG for mixed types")
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter)
    if notes:
        names = json.loads(require(conn, "flash_types", notes[0]["type_id"])["fields"])
        writer.writerow(names + ["Tags"])
        for note in notes: writer.writerow([note["fields"][name] for name in names] + [" ".join(note["tags"])])
    return output.getvalue().encode()


def _decompress(data):
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        try:
            import zstandard
        except ImportError:
            raise ValueError("Install zstandard>=0.23.0 for compressed Anki archives") from None
        try:
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as stream:
                data = stream.read(MAX_ARCHIVE+1)
        except zstandard.ZstdError:
            raise ValueError("Invalid Zstandard-compressed Anki archive member") from None
    if len(data) > MAX_ARCHIVE: raise ValueError("Expanded archive exceeds 128 MiB")
    return data


def _protobuf(data):
    """Read Anki's public protobuf wire fields without executing archive code.

    Only scalar/blob fields are needed for notetype configs and media entries.
    Unknown fields remain skippable, as required by the protobuf format.
    """
    position, fields = 0, {}
    def varint():
        nonlocal position
        value = 0
        for shift in range(0, 70, 7):
            if position >= len(data): break
            byte = data[position]
            position += 1
            value |= (byte & 127) << shift
            if byte < 128: return value
        raise ValueError("Malformed Anki protobuf integer")
    while position < len(data):
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not number: raise ValueError("Malformed Anki protobuf field")
        if wire == 0:
            value = varint()
        elif wire in {1, 2, 5}:
            size = varint() if wire == 2 else 8 if wire == 1 else 4
            if size > len(data) - position: raise ValueError("Truncated Anki protobuf field")
            value = data[position:position+size]
            position += size
        else:
            raise ValueError("Unsupported Anki protobuf wire type")
        fields.setdefault(number, []).append(value)
    return fields


def _anki_models_decks(source, col):
    tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "notetypes" not in tables:
        models, decks = json.loads(col["models"]), json.loads(col["decks"])
        if not isinstance(models, dict) or not isinstance(decks, dict) or any(not isinstance(model, dict) for model in models.values()) or any(not isinstance(deck, dict) or not isinstance(deck.get("name"), str) for deck in decks.values()):
            raise ValueError("Invalid Anki note types or decks")
        return models, decks
    models = {}
    # Schema 15+ stores Notetype.Config and Template.Config protobuf blobs.
    # https://github.com/ankitects/anki/blob/main/proto/anki/notetypes.proto
    for row in source.execute("SELECT id,name,config FROM notetypes"):
        config = _protobuf(row["config"])
        kind = config.get(1, [0])[-1]
        if type(kind) is not int or kind not in {0, 1}: raise ValueError("Unknown Anki note type kind")
        fields = [{"name": f["name"], "ord": f["ord"]} for f in source.execute("SELECT ord,name FROM fields WHERE ntid=? ORDER BY ord", (row["id"],))]
        templates = []
        for template in source.execute("SELECT ord,name,config FROM templates WHERE ntid=? ORDER BY ord", (row["id"],)):
            formats = _protobuf(template["config"])
            front, back = formats.get(1, [b""])[-1], formats.get(2, [b""])[-1]
            if not isinstance(front, bytes) or not isinstance(back, bytes): raise ValueError("Malformed Anki template format")
            templates.append({"name": template["name"], "ord": template["ord"], "qfmt": front.decode("utf-8"), "afmt": back.decode("utf-8")})
        models[str(row["id"])] = {"name": row["name"], "type": kind, "flds": fields, "tmpls": templates}
    decks = {}
    for row in source.execute("SELECT id,name FROM decks"):
        if not isinstance(row["name"], str): raise ValueError("Invalid Anki deck name")
        decks[str(row["id"])] = {"name": row["name"].replace("\x1f", "::")}
    return models, decks


def _anki_media(data, modern):
    if not modern:
        media = json.loads(data)
        if not isinstance(media, dict) or any(not isinstance(k, str) or not k.isdecimal() or not isinstance(v, str) for k, v in media.items()):
            raise ValueError("Anki media manifest must map numbered members to filenames")
        return [(key, name, None, None) for key, name in media.items()]
    # https://github.com/ankitects/anki/blob/main/proto/anki/import_export.proto
    entries = []
    for index, raw in enumerate(_protobuf(data).get(1, [])):
        entry = _protobuf(raw)
        name = entry.get(1, [b""])[-1]
        if not isinstance(name, bytes): raise ValueError("Malformed Anki media filename")
        name = name.decode("utf-8")
        size, checksum = entry.get(2, [0])[-1], entry.get(3, [b""])[-1]
        if type(size) is not int or len(checksum) != 20:
            raise ValueError("Invalid Anki media size or SHA-1 checksum")
        entries.append((str(entry.get(255, [index])[-1]), name, size, checksum))
    return entries


def _validate_import_state(value):
    if value["state"] not in {"new", "learning", "review", "relearning"}:
        raise ValueError("Imported card has an unknown scheduling state")
    for key in ("due", "buried_until"):
        if value[key] is None and key == "buried_until": continue
        timestamp = datetime.fromisoformat(value[key])
        if timestamp.tzinfo is None: raise ValueError("Imported schedule timestamps must include a timezone")
        value[key] = timestamp.astimezone(timezone.utc).isoformat()
    for key in ("suspended", "leech"):
        if value[key] not in {0, 1}: raise ValueError("Imported flags must be 0 or 1")
    for key in ("lapses", "reviews"):
        if type(value[key]) is not int or not 0 <= value[key] <= 100000000:
            raise ValueError("Imported review counters are invalid")
    if value["fsrs"]:
        from fsrs import Card, State
        card = Card.from_json(value["fsrs"])
        expected = {"learning": State.Learning, "relearning": State.Relearning, "review": State.Review}
        if value["state"] not in expected or card.state != expected[value["state"]]:
            raise ValueError("Imported FSRS state disagrees with card state")
        if card.due.tzinfo is None or card.last_review is not None and card.last_review.tzinfo is None:
            raise ValueError("Imported FSRS timestamps must include a timezone")
        if card.stability is not None and not 0 < card.stability <= 1000000 or card.difficulty is not None and not 1 <= card.difficulty <= 10:
            raise ValueError("Imported FSRS stability/difficulty are invalid")
        if card.state == State.Review and (card.stability is None or card.difficulty is None):
            raise ValueError("A reviewed FSRS card needs stability and difficulty")
        if card.state != State.Review and (type(card.step) is not int or not 0 <= card.step <= 10):
            raise ValueError("Imported learning step is invalid")
        if card.due.astimezone(timezone.utc).isoformat() != value["due"]:
            raise ValueError("Imported FSRS due timestamp disagrees with card due")
        card.due = card.due.astimezone(timezone.utc)
        if card.last_review: card.last_review = card.last_review.astimezone(timezone.utc)
        value["fsrs"] = card.to_json()
    elif value["state"] != "new":
        raise ValueError("Scheduled cards need FSRS state")


def import_apkg(conn, payload):
    if len(payload) > MAX_ARCHIVE: raise ValueError("Archive exceeds 128 MiB")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if sum(m.file_size for m in archive.infolist()) > MAX_ARCHIVE: raise ValueError("Expanded archive exceeds 128 MiB")
        names = archive.namelist()
        collection = next((name for name in ("collection.anki21b", "collection.anki21", "collection.anki2") if name in names), None)
        if not collection: raise ValueError("Archive has no Anki collection")
        db_bytes = _decompress(archive.read(collection))
        if not db_bytes.startswith(b"SQLite format 3\x00"): raise ValueError("Anki collection is not SQLite")
        with tempfile.TemporaryDirectory(prefix="kitty-anki-") as folder:
            database = Path(folder) / "collection.sqlite"
            database.write_bytes(db_bytes)
            source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            source.row_factory = sqlite3.Row
            source.execute("PRAGMA trusted_schema=OFF")
            source.set_progress_handler(lambda: 1, 20000000)
            try:
                tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"col", "notes", "cards", "revlog"} <= tables:
                    raise ValueError("Anki collection is missing required tables")
                col = source.execute("SELECT * FROM col").fetchone()
                if col is None: raise ValueError("Anki collection metadata is missing")
                models, decks = _anki_models_decks(source, col)
                note_rows = source.execute("SELECT * FROM notes LIMIT 10001").fetchall()
                card_rows = source.execute("SELECT * FROM cards LIMIT 100001").fetchall()
                if len(note_rows) > 10000 or len(card_rows) > 100000: raise ValueError("Archive exceeds 10000 notes or 100000 cards")
                last_reviews = {row["cid"]: row["reviewed"] / 1000 for row in source.execute("SELECT cid,MAX(id) AS reviewed FROM revlog GROUP BY cid")}
                custom = {}
                if "kitty_card_state" in tables:
                    custom = {r["id"]: json.loads(r["state"]) for r in source.execute("SELECT * FROM kitty_card_state")}
                    if any(not isinstance(state, dict) for state in custom.values()): raise ValueError("Invalid kitty scheduling metadata")
                metadata = {}
                if "kitty_metadata" in tables:
                    metadata = {r["key"]: json.loads(r["value"]) for r in source.execute("SELECT * FROM kitty_metadata")}
                    for key in ("decks", "notes"):
                        values = metadata.get(key, {})
                        if not isinstance(values, dict) or any(not isinstance(value, dict) for value in values.values()):
                            raise ValueError("Invalid kitty deck/note metadata")
                with transaction(conn):
                    modern = collection == "collection.anki21b"
                    media = _anki_media(_decompress(archive.read("media")), modern) if "media" in names else []
                    skipped, expanded = [], len(db_bytes)
                    for key, filename, size, checksum in media:
                        if key not in names: raise ValueError("Missing archive media member")
                        data = _decompress(archive.read(key))
                        expanded += len(data)
                        if expanded > MAX_ARCHIVE: raise ValueError("Expanded archive exceeds 128 MiB")
                        if size is not None and (len(data) != size or hashlib.sha1(data).digest() != checksum):
                            raise ValueError("Anki media size or SHA-1 checksum does not match")
                        try: _store_media(conn, filename, data)
                        except ValueError as error:
                            # Executable media is deliberately never copied into the workspace.
                            if "HTML/SVG" in str(error): skipped.append(filename)
                            else: raise
                    deck_map = {}
                    for key, deck in sorted(decks.items(), key=lambda pair: pair[1]["name"]):
                        name = deck["name"]
                        parent = None
                        parts = name.split("::")
                        for index in range(1, len(parts)+1):
                            full = "::".join(parts[:index])
                            conn.execute("INSERT OR IGNORE INTO flash_decks(name,parent_id,settings,created_at) VALUES(?,?,?,?)", (full, parent, encode(DEFAULT_SETTINGS), now().isoformat()))
                            parent = conn.execute("SELECT id FROM flash_decks WHERE name=?", (full,)).fetchone()[0]
                        deck_map[int(key)] = parent
                        imported_settings = metadata.get("decks", {}).get(str(key))
                        if imported_settings and not conn.execute("SELECT 1 FROM flash_notes WHERE deck_id=? LIMIT 1", (parent,)).fetchone():
                            config = settings(imported_settings["settings"])
                            conn.execute("UPDATE flash_decks SET settings=?,archived=?,revision=revision+1 WHERE id=?", (encode(config), int(bool(imported_settings.get("archived"))), parent))
                    type_map = {}
                    for key, model in models.items():
                        name = model["name"]
                        fields = [f["name"] for f in sorted(model["flds"], key=lambda f: f["ord"])]
                        templates = [{"name": t["name"], "front": t["qfmt"], "back": t["afmt"]} for t in sorted(model["tmpls"], key=lambda t: t["ord"])]
                        _validate_type(name, "cloze" if model.get("type") == 1 else "template", fields, templates)
                        old = conn.execute("SELECT * FROM flash_types WHERE name=?", (name,)).fetchone()
                        if old and (json.loads(old["fields"]) != fields or json.loads(old["templates"]) != templates):
                            name += " (import " + str(key) + ")"
                        conn.execute("INSERT OR IGNORE INTO flash_types(name,kind,fields,templates) VALUES(?,?,?,?)", (name, "cloze" if model.get("type") == 1 else "template", encode(fields), encode(templates)))
                        type_map[int(key)] = (conn.execute("SELECT id FROM flash_types WHERE name=?", (name,)).fetchone()[0], fields)
                    by_note = {}
                    for card in card_rows: by_note.setdefault(card["nid"], []).append(card)
                    imported, duplicate = 0, 0
                    epoch = datetime.fromtimestamp(col["crt"], timezone.utc)
                    for original in note_rows:
                        if conn.execute("SELECT 1 FROM flash_notes WHERE guid=?", (original["guid"],)).fetchone():
                            duplicate += 1
                            continue
                        cards = by_note.get(original["id"], [])
                        if not cards: continue
                        if len({c["did"] for c in cards}) > 1: raise ValueError("Cards of one note are split across decks; reunite them before import")
                        type_id, field_names = type_map[original["mid"]]
                        values = original["flds"].split("\x1f")
                        if len(values) != len(field_names): raise ValueError("Anki note field count does not match model")
                        note = _save_note(conn, {"guid": original["guid"], "deck_id": deck_map[cards[0]["did"]], "type_id": type_id, "fields": dict(zip(field_names, values)), "tags": original["tags"].split(), "archived": bool(metadata.get("notes", {}).get(original["guid"], {}).get("archived", False))})
                        ordinals = {c["ordinal"] for c in conn.execute("SELECT ordinal FROM flash_cards WHERE note_id=?", (note["id"],))}
                        if {c["ord"] for c in cards} != ordinals: raise ValueError("Anki cards differ from generated templates; remove empty/unsupported cards before import")
                        for card in cards:
                            if card["type"] not in {0, 1, 2, 3}: raise ValueError("Unknown Anki scheduling state")
                            state = {0: "new", 1: "learning", 2: "review", 3: "relearning"}[card["type"]]
                            due = now() if state == "new" else (datetime.fromtimestamp(card["due"], timezone.utc) if state in {"learning", "relearning"} and card["due"] > 1000000000 else epoch + timedelta(days=max(0, card["due"])))
                            ident = conn.execute("SELECT id FROM flash_cards WHERE note_id=? AND ordinal=?", (note["id"], card["ord"])).fetchone()[0]
                            fsrs_json = None
                            if state != "new":
                                from fsrs import Card, State
                                try: card_data = json.loads(card["data"] or "{}")
                                except (TypeError, json.JSONDecodeError): card_data = {}
                                if not isinstance(card_data, dict): card_data = {}
                                stability, difficulty = card_data.get("s"), card_data.get("d")
                                if stability is None or difficulty is None:
                                    stability, difficulty = max(0.1, float(card["ivl"])), 5.0
                                last_review = card_data.get("lrt", last_reviews.get(card["id"]))
                                last_review = datetime.fromtimestamp(last_review, timezone.utc) if last_review is not None else due-timedelta(days=max(1, card["ivl"]))
                                cfg = json.loads(require(conn, "flash_decks", note["deck_id"])["settings"])
                                steps = cfg["relearning_steps" if state == "relearning" else "learning_steps"]
                                step = min(max(0, len(steps) - 1), max(0, len(steps) - card["left"] % 1000)) if state != "review" else None
                                fsrs_json = Card(card_id=ident, state={"learning": State.Learning, "review": State.Review, "relearning": State.Relearning}[state], step=step, stability=stability, difficulty=difficulty, due=due, last_review=last_review).to_json()
                            values = {"state": state, "due": due.isoformat(), "fsrs": fsrs_json, "suspended": int(card["queue"] == -1), "buried_until": (now()+timedelta(days=1)).isoformat() if card["queue"] in {-2, -3} else None, "lapses": card["lapses"], "reviews": card["reps"], "leech": 0}
                            if card["id"] in custom:
                                values.update({k: v for k, v in custom[card["id"]].items() if k in values})
                                if values["fsrs"]:
                                    parsed = json.loads(values["fsrs"])
                                    parsed["card_id"] = ident
                                    values["fsrs"] = encode(parsed)
                            _validate_import_state(values)
                            columns = list(values)
                            conn.execute("UPDATE flash_cards SET " + ",".join(k+"=?" for k in columns) + " WHERE id=?", (*[values[k] for k in columns], ident))
                        imported += 1
                return {"imported": imported, "duplicates": duplicate, "skipped_unsafe_media": skipped, "warning": "Anki FSRS memory is preserved when present; older schedules use interval-based stability estimates. kitty exports retain exact FSRS state."}
            finally:
                source.close()


def _anki_requirements(fields, templates):
    requirements = []
    for ordinal, template in enumerate(templates):
        def question(present):
            values = {name: "1" if name in present else "" for name in fields}
            source = _conditional_template(template["front"], values)
            return re.sub(r"\{\{([^{}]+)\}\}", lambda match: values.get(match.group(1).rsplit(":", 1)[-1], ""), source)
        empty = question(set())
        all_fields = set(fields)
        if question(all_fields) == empty:
            requirements.append([ordinal, "none", []])
            continue
        # Legacy clients only support disjunctions or conjunctions. Prefer
        # fields that can independently render a question, then required fields.
        any_fields = [i for i, name in enumerate(fields) if question({name}) != empty]
        all_required = [i for i, name in enumerate(fields) if question(all_fields - {name}) == empty]
        requirements.append([ordinal, "all", all_required] if all_required else [ordinal, "any", any_fields])
    return requirements


def export_apkg(conn, deck_id=None):
    notes = browser(conn, deck_id, limit=100000)["notes"]
    all_decks = [dict(r) for r in conn.execute("SELECT * FROM flash_decks")]
    selected_name = require(conn, "flash_decks", deck_id)["name"] if deck_id else None
    selected_decks = {d["id"] for d in all_decks if selected_name is None or d["name"] == selected_name or d["name"].startswith(selected_name+"::") or selected_name.startswith(d["name"]+"::")}
    selected_types = {n["type_id"] for n in notes} or {r[0] for r in conn.execute("SELECT id FROM flash_types")}
    models, decks = {}, {}
    stamp = int(now().timestamp())
    for ident in selected_decks:
        deck = require(conn, "flash_decks", ident)
        decks[str(ident)] = {"id": ident, "name": deck["name"], "mod": stamp, "usn": -1, "desc": "", "dyn": 0, "conf": ident, "extendNew": 0, "extendRev": 0, "collapsed": False, "newToday": [0,0], "revToday": [0,0], "lrnToday": [0,0], "timeToday": [0,0]}
    for ident in selected_types:
        typ = require(conn, "flash_types", ident)
        fields, templates = json.loads(typ["fields"]), json.loads(typ["templates"])
        models[str(ident)] = {"id": ident, "name": typ["name"], "type": int(typ["kind"] == "cloze"), "mod": stamp, "usn": -1, "sortf": 0, "did": next(iter(selected_decks), 1), "css": ".card {font-family: sans-serif; font-size: 20px;}", "latexPre": "", "latexPost": "", "req": _anki_requirements(fields, templates), "flds": [{"name": name, "ord": i, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []} for i, name in enumerate(fields)], "tmpls": [{"name": t["name"], "ord": i, "qfmt": t["front"], "afmt": t["back"], "bqfmt": "", "bafmt": "", "did": None} for i,t in enumerate(templates)], "tags": [], "vers": []}
    with tempfile.TemporaryDirectory(prefix="kitty-export-") as folder:
        path = Path(folder) / "collection.anki2"
        database = sqlite3.connect(path)
        database.executescript("CREATE TABLE col(id INTEGER PRIMARY KEY,crt INTEGER,mod INTEGER,scm INTEGER,ver INTEGER,dty INTEGER,usn INTEGER,ls INTEGER,conf TEXT,models TEXT,decks TEXT,dconf TEXT,tags TEXT); CREATE TABLE notes(id INTEGER PRIMARY KEY,guid TEXT,mid INTEGER,mod INTEGER,usn INTEGER,tags TEXT,flds TEXT,sfld TEXT,csum INTEGER,flags INTEGER,data TEXT); CREATE TABLE cards(id INTEGER PRIMARY KEY,nid INTEGER,did INTEGER,ord INTEGER,mod INTEGER,usn INTEGER,type INTEGER,queue INTEGER,due INTEGER,ivl INTEGER,factor INTEGER,reps INTEGER,lapses INTEGER,left INTEGER,odue INTEGER,odid INTEGER,flags INTEGER,data TEXT); CREATE TABLE revlog(id INTEGER PRIMARY KEY,cid INTEGER,usn INTEGER,ease INTEGER,ivl INTEGER,lastIvl INTEGER,factor INTEGER,time INTEGER,type INTEGER); CREATE TABLE graves(usn INTEGER,oid INTEGER,type INTEGER); CREATE TABLE kitty_card_state(id INTEGER PRIMARY KEY,state TEXT);")
        database.execute("CREATE TABLE kitty_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        metadata = {"decks": {str(d["id"]): {"settings": json.loads(d["settings"]), "archived": d["archived"]} for d in all_decks if d["id"] in selected_decks}, "notes": {n["guid"]: {"archived": n["archived"]} for n in notes}}
        database.executemany("INSERT INTO kitty_metadata VALUES(?,?)", [(key, encode(value)) for key, value in metadata.items()])
        epoch = datetime.combine(now().date(), datetime.min.time(), tzinfo=timezone.utc)
        dconf = {}
        for deck in all_decks:
            if deck["id"] not in selected_decks: continue
            cfg = json.loads(deck["settings"])
            dconf[str(deck["id"])] = {"id": deck["id"], "name": deck["name"], "mod": stamp, "usn": -1, "maxTaken": 60, "autoplay": True, "timer": 0, "replayq": True, "new": {"delays": [s / 60 for s in cfg["learning_steps"]], "ints": [1, 4], "initialFactor": 2500, "order": 1, "perDay": cfg["new_limit"], "bury": cfg["bury_siblings"]}, "rev": {"perDay": cfg["review_limit"], "ease4": 1.3, "fuzz": 0.05, "ivlFct": 1, "maxIvl": cfg["maximum_interval"], "bury": cfg["bury_siblings"], "minSpace": 1}, "lapse": {"delays": [s / 60 for s in cfg["relearning_steps"]], "mult": 0, "minInt": 1, "leechFails": cfg["leech_threshold"], "leechAction": 0}}
        database.execute("INSERT INTO col VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (1, int(epoch.timestamp()), stamp*1000, stamp*1000, 11, 0, -1, 0, encode({"nextPos": len(notes)+1, "activeDecks": list(selected_decks), "curDeck": next(iter(selected_decks), 1)}), encode(models), encode(decks), encode(dconf), "{}"))
        for note in notes:
            names = json.loads(require(conn, "flash_types", note["type_id"])["fields"])
            fields = [note["fields"][name] for name in names]
            checksum = int(hashlib.sha1(fields[0].encode()).hexdigest()[:8], 16)
            database.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?)", (note["id"], note["guid"], note["type_id"], stamp, -1, " " + " ".join(note["tags"]) + " ", "\x1f".join(fields), fields[0], checksum, 0, ""))
            for entry in note["cards"]:
                card = require(conn, "flash_cards", entry["id"])
                due_at = datetime.fromisoformat(card["due"])
                state = {"new": 0, "learning": 1, "review": 2, "relearning": 3}[card["state"]]
                queue = -1 if card["suspended"] else -2 if card["buried_until"] and card["buried_until"] > now().isoformat() else 1 if state in {1,3} else state
                due = card["id"] if state == 0 else int(due_at.timestamp()) if state in {1,3} else max(0, (due_at-epoch).days)
                fsrs = json.loads(card["fsrs"]) if card["fsrs"] else {}
                interval = max(1, int(fsrs.get("stability") or 1))
                cfg = json.loads(require(conn, "flash_decks", note["deck_id"])["settings"])
                steps = cfg["relearning_steps" if state == 3 else "learning_steps"]
                left = max(0, len(steps) - (fsrs.get("step") or 0)) * 1001 if state in {1, 3} else 0
                card_data = {}
                if fsrs.get("stability") is not None and fsrs.get("difficulty") is not None:
                    card_data = {"s": fsrs["stability"], "d": fsrs["difficulty"], "dr": cfg["retention"]}
                    if fsrs.get("last_review"): card_data["lrt"] = int(datetime.fromisoformat(fsrs["last_review"]).timestamp())
                database.execute("INSERT INTO cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (card["id"], note["id"], note["deck_id"], card["ordinal"], stamp, -1, state, queue, due, interval, 2500, card["reviews"], card["lapses"], left, 0, 0, 0, encode(card_data) if card_data else ""))
                database.execute("INSERT INTO kitty_card_state VALUES(?,?)", (card["id"], encode(card)))
        database.commit()
        database.close()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(path, "collection.anki2")
            manifest = {}
            references = _MediaReferences()
            for note in notes:
                for text in note["fields"].values(): references.collect(text)
                for card in note["cards"]:
                    source_card = {"note_id": note["id"], "ordinal": card["ordinal"]}
                    for answer in (False, True): references.collect(_render_source(conn, source_card, answer))
            for row in conn.execute("SELECT name,content FROM flash_media ORDER BY name"):
                if row["name"] not in references.names:
                    continue
                index = str(len(manifest))
                manifest[index] = row["name"]
                archive.writestr(index, row["content"])
            archive.writestr("media", encode(manifest))
        return output.getvalue()
