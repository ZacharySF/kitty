"""Flashcard notes as Rika's in-app tools write them (kitty.core.rika.workspace_tools).

The flashcard routes, review scheduling (FSRS-6), statistics and Anki import/export are
answered by rika-server (server/src/flashcards). What stays here is what Rika's tools call:
the built-in decks and note types, saving a deck or a note (with its generated cards, the
same rules as the route) and deleting a note; tests/test_flashcards.py checks both agree.
"""
from __future__ import annotations

import base64
import html
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from kitty.db.connection import transaction

DEFAULT_SETTINGS = {"new_limit": 20, "review_limit": 200, "retention": 0.9,
                    "learning_steps": [60, 600], "relearning_steps": [600],
                    "maximum_interval": 36500, "bury_siblings": True,
                    "leech_threshold": 8, "timezone": "UTC"}
CLOZE = re.compile(r"\{\{c([1-9][0-9]*)::(.*?)(?:::(.*?))?\}\}", re.S)


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


