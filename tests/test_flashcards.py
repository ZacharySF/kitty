"""Consumer scheduling, archive round-trip and safe review invariants; no network."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from kitty.core import flashcards as f
from kitty.db import connect, migrate
from kitty import web_atlas, web_flashcards
from kitty.web_server import APIError


@pytest.fixture
def cards(tmp_path, monkeypatch):
    conn = connect(tmp_path / "cards.sqlite")
    migrate(conn)
    moment = [datetime(2026, 10, 14, 12, tzinfo=timezone.utc)]
    monkeypatch.setattr(f, "now", lambda: moment[0])
    state = f.overview(conn)
    yield conn, moment, state
    conn.close()


def add(cards, kind="basic", fields=None, deck=None, tags=None):
    conn, _, state = cards
    typ = next(t for t in state["types"] if t["kind"] == kind)
    fields = fields or ({"Text": "{{c1::hidden-one::hint}} + {{c2::hidden-two}} + {{c1::second-one}}", "Extra": "extra-only"} if kind == "cloze" else {"Front": "synthetic-question", "Back": "answer-only-secret"})
    return f.save_note(conn, {"deck_id": deck or state["decks"][0]["id"], "type_id": typ["id"], "fields": fields, "tags": tags or []})


def review_once(conn, rating, **kwargs):
    card = f.start_review(conn, **kwargs)["card"]
    assert card is not None
    f.reveal(conn, card["token"])
    result = f.grade(conn, card["token"], rating, 1234)
    return card, result


def test_front_does_not_leak_answer_and_grading_requires_reveal(cards):
    conn, _, _ = cards
    add(cards)
    response = f.start_review(conn)
    assert "answer-only-secret" not in json.dumps(response)
    token = response["card"]["token"]
    with pytest.raises(ValueError, match="Reveal"):
        f.grade(conn, token, 3)
    revealed = f.reveal(conn, token)
    assert "answer-only-secret" in revealed["back"]
    assert [x["rating"] for x in revealed["intervals"]] == [1, 2, 3, 4]
    f.grade(conn, token, 3)
    with pytest.raises(ValueError, match="expired"):
        f.grade(conn, token, 3)


@pytest.mark.parametrize("rating,seconds,state", [(1,60,"learning"),(2,330,"learning"),(3,600,"learning"),(4,None,"review")])
def test_first_ratings_have_precise_fsrs_learning_boundaries(cards, rating, seconds, state):
    conn, clock, _ = cards
    add(cards)
    card, _ = review_once(conn, rating)
    persisted = f.require(conn, "flash_cards", card["id"])
    assert persisted["state"] == state and persisted["reviews"] == 1
    due = datetime.fromisoformat(persisted["due"])
    assert due > clock[0]
    if seconds is not None:
        assert due - clock[0] == timedelta(seconds=seconds)
        clock[0] = due - timedelta(microseconds=1)
        assert f.start_review(conn)["card"] is None
        clock[0] = due
        assert f.start_review(conn)["card"]["id"] == card["id"]


def test_daily_limits_learning_bypass_local_midnight_and_parent_budget(cards):
    conn, clock, state = cards
    parent = f.save_deck(conn, {"name": "Parent", "settings": {"new_limit":1,"review_limit":0,"timezone":"America/New_York"}})
    child = f.save_deck(conn, {"name":"Parent::Child", "settings":{"new_limit":20,"timezone":"America/New_York"}})
    add(cards, deck=child["id"])
    add(cards, deck=child["id"])
    assert f.start_review(conn, parent["id"])["remaining"] == 1
    card, _ = review_once(conn, 1, deck_id=parent["id"])
    assert f.start_review(conn, parent["id"])["card"] is None
    clock[0] += timedelta(seconds=60)
    assert f.start_review(conn, parent["id"])["card"]["id"] == card["id"]
    review_once(conn, 4, deck_id=parent["id"])
    clock[0] = datetime(2026,10,15,3,59,tzinfo=timezone.utc)
    assert f.start_review(conn, parent["id"])["card"] is None
    clock[0] = datetime(2026,10,15,4,0,tzinfo=timezone.utc)
    assert f.start_review(conn, parent["id"])["card"]["state"] == "new"


def test_cloze_indices_hide_only_target_and_sibling_bury_undo(cards):
    conn, _, _ = cards
    note = add(cards, "cloze")
    response = f.start_review(conn)
    front = response["card"]["front"]
    assert "hidden-one" not in front and "second-one" not in front
    assert "hidden-two" in front and "hint" in front and "extra-only" not in front
    original = [dict(r) for r in conn.execute("SELECT * FROM flash_cards WHERE note_id=? ORDER BY ordinal", (note["id"],))]
    f.reveal(conn, response["card"]["token"])
    f.grade(conn, response["card"]["token"], 4)
    sibling = f.require(conn, "flash_cards", original[1]["id"])
    assert sibling["buried_until"].startswith("2026-10-15T00:00")
    assert f.start_review(conn)["card"] is None
    f.undo(conn)
    restored = [dict(r) for r in conn.execute("SELECT * FROM flash_cards WHERE note_id=? ORDER BY ordinal", (note["id"],))]
    for before, after in zip(original, restored):
        for key in ("state","due","fsrs","buried_until","reviews","lapses"):
            assert before[key] == after[key]
    assert f.history(conn)["reviews"][0]["undone"] == 1


def test_practice_does_not_change_schedule_limits_or_streak(cards):
    conn, _, _ = cards
    note = add(cards, tags=["custom"])
    before = dict(conn.execute("SELECT * FROM flash_cards WHERE note_id=?", (note["id"],)).fetchone())
    review_once(conn, 1, mode="filtered", search="tag:custom")
    review_once(conn, 4, mode="cram", search="tag:custom")
    after = f.require(conn, "flash_cards", before["id"])
    assert after == before
    assert f.overview(conn)["stats"]["streak"] == 0
    assert len(f.history(conn)["reviews"]) == 2
    f.undo(conn)
    assert f.require(conn, "flash_cards", before["id"]) == before


def test_leech_lapse_relearning_suspend_and_undo(cards):
    conn, clock, state = cards
    deck = state["decks"][0]
    f.save_deck(conn, {"id":deck["id"],"revision":deck["revision"],"name":deck["name"],"settings":{"leech_threshold":1}})
    add(cards)
    card, _ = review_once(conn, 4)
    clock[0] = datetime.fromisoformat(f.require(conn,"flash_cards",card["id"])["due"])
    review_once(conn, 1)
    failed = f.require(conn,"flash_cards",card["id"])
    assert failed["state"] == "relearning" and failed["lapses"] == 1
    assert failed["leech"] == failed["suspended"] == 1
    assert datetime.fromisoformat(failed["due"])-clock[0] == timedelta(seconds=600)
    assert f.start_review(conn)["card"] is None
    f.undo(conn)
    restored = f.require(conn,"flash_cards",card["id"])
    assert restored["state"] == "review" and not restored["leech"] and not restored["suspended"]


def test_optimistic_edits_and_archive_invalidate_review(cards):
    conn, _, _ = cards
    note = add(cards)
    card = f.start_review(conn)["card"]
    f.reveal(conn, card["token"])
    f.action(conn, {"id":note["id"],"revision":note["revision"],"action":"archive"})
    with pytest.raises(ValueError, match="eligible"):
        f.grade(conn,card["token"],3)
    with pytest.raises(ValueError, match="changed"):
        f.save_note(conn,{"id":note["id"],"revision":note["revision"],"deck_id":note["deck_id"],"type_id":note["type_id"],"fields":json.loads(note["fields"]),"tags":[]})
    assert f.browser(conn,search="is:archived")["total"] == 1


def test_review_state_survives_reopening(cards, tmp_path):
    conn, _, _ = cards
    add(cards)
    card, _ = review_once(conn, 3)
    expected = f.require(conn,"flash_cards",card["id"])
    reopened = connect(tmp_path/"cards.sqlite")
    try:
        assert f.require(reopened,"flash_cards",card["id"]) == expected
        assert f.overview(reopened)["stats"]["streak"] == 1
    finally:
        reopened.close()


def test_safe_html_local_media_and_remote_js_are_not_rendered(cards):
    conn, _, _ = cards
    png = b"\x89PNG\r\n\x1a\nsynthetic-local-image"
    f.upload_media(conn,"local.png",png)
    rendered = f.safe_html(conn, '<script>alert("private")</script><img src="https://attacker.invalid/x" onerror="alert(1)"><img src="local.png"><a href="javascript:alert(1)">safe text</a><b style="background:url(https://attacker.invalid)">bold</b>')
    assert "attacker" not in rendered and "javascript" not in rendered and "onerror" not in rendered and "private" not in rendered
    assert "data:image/png;base64," in rendered and "<b>bold</b>" in rendered
    for name, payload in (("../escape.png",png),("bad.svg",b'<svg onload="alert(1)"/>'),("bad.html",b'<html>')):
        with pytest.raises(ValueError): f.upload_media(conn,name,payload)


def test_apkg_roundtrip_preserves_cards_cloze_media_and_exact_fsrs(cards, tmp_path):
    conn, _, _ = cards
    add(cards,"cloze")
    add(cards,"reversed",fields={"Front":'<img src="local.png">synthetic',"Back":"roundtrip-answer"})
    f.upload_media(conn,"local.png",b"\x89PNG\r\n\x1a\nsynthetic-media")
    review_once(conn,3)
    payload=f.export_apkg(conn)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.read("collection.anki2").startswith(b"SQLite format 3\0")
        assert json.loads(archive.read("media")) == {"0":"local.png"}
    target=connect(tmp_path/"import.sqlite")
    try:
        migrate(target); f.bootstrap(target)
        result=f.import_apkg(target,payload)
        assert result["imported"] == 2
        originals=f.browser(conn,limit=100)["notes"]
        imported=f.browser(target,limit=100)["notes"]
        assert sum(len(n["cards"]) for n in imported) == 4
        by_guid={r["guid"]:dict(r) for r in target.execute("SELECT * FROM flash_notes")}
        for note in originals:
            original=f.require(conn,"flash_notes",note["id"])
            matching=by_guid[original["guid"]]
            assert json.loads(matching["fields"]) == note["fields"]
            for card in note["cards"]:
                before=f.require(conn,"flash_cards",card["id"])
                after=dict(target.execute("SELECT * FROM flash_cards WHERE note_id=? AND ordinal=?",(matching["id"],card["ordinal"])).fetchone())
                for key in ("state","due","buried_until","reviews","lapses","leech","suspended"):
                    assert before[key] == after[key]
                if before["fsrs"]:
                    a,b=json.loads(before["fsrs"]),json.loads(after["fsrs"])
                    a.pop("card_id");b.pop("card_id");assert a==b
        assert target.execute("SELECT content FROM flash_media WHERE name='local.png'").fetchone()[0] == conn.execute("SELECT content FROM flash_media WHERE name='local.png'").fetchone()[0]
        assert f.import_apkg(target,payload)["duplicates"] == 2
    finally: target.close()


def test_csv_tsv_unicode_quotes_and_atomic_invalid_import(cards):
    conn, _, state = cards
    deck, typ = state["decks"][0]["id"], state["types"][0]["id"]
    f.import_text(conn,'Front,Back,Tags\n"question, comma","answer\nline",one two\n',deck,typ)
    exported=f.export_text(conn,deck,delimiter="\t").decode()
    assert 'question, comma' in exported and 'answer\nline' in exported
    assert f.import_text(conn,exported,deck,typ,delimiter="\t")["imported"] == 1
    before=f.overview(conn)["stats"]["total"]
    with pytest.raises(ValueError): f.import_text(conn,"valid,first\ninvalid\n",deck,typ)
    assert f.overview(conn)["stats"]["total"] == before


def test_api_consumer_validation_and_no_answer_in_review(cards):
    conn, _, _ = cards
    add(cards)
    assert web_flashcards.get(None,conn,"/api/not-flashcards",{}) is None
    with pytest.raises(APIError): web_flashcards.post(None,conn,"/api/flashcards/import",{"format":"apkg","data":"not base64"})
    with pytest.raises(APIError): web_flashcards.get(None,conn,"/api/flashcards/browser",{"limit":"0"})
    response=web_flashcards.post(None,conn,"/api/flashcards/review",{})
    assert "answer-only-secret" not in json.dumps(response)
    with pytest.raises(APIError): web_flashcards.post(None,conn,"/api/flashcards/grade",{"token":response["card"]["token"],"rating":3})


def test_nested_optional_templates_skip_blank_cards_and_keep_schedules(cards):
    conn, _, state = cards
    typ = f.save_type(conn, {
        "name": "Optional reverse", "kind": "template",
        "fields": ["Front", "Back", "Reverse"],
        "templates": [
            {"name": "Forward", "front": "{{#Front}}{{#Front}}{{Front}}{{/Front}}{{/Front}}", "back": "{{Back}}"},
            {"name": "Reverse", "front": "{{#Reverse}}{{Back}}{{/Reverse}}", "back": "{{Front}}"},
        ],
    })
    note = f.save_note(conn, {"deck_id": state["decks"][0]["id"], "type_id": typ["id"],
                              "fields": {"Front": "question", "Back": "answer", "Reverse": ""}})
    assert [c["ordinal"] for c in f.browser(conn)["notes"][0]["cards"]] == [0]
    reviewed, _ = review_once(conn, 4)
    scheduled = f.require(conn, "flash_cards", reviewed["id"])
    f.save_note(conn, {"id": note["id"], "revision": note["revision"], "deck_id": note["deck_id"],
                       "type_id": typ["id"], "fields": {"Front": "question", "Back": "answer", "Reverse": "yes"}})
    cards_after = f.browser(conn)["notes"][0]["cards"]
    assert {c["ordinal"] for c in cards_after} == {0, 1}
    assert f.require(conn, "flash_cards", reviewed["id"])["fsrs"] == scheduled["fsrs"]
    with pytest.raises(ValueError, match="nested"):
        f.save_type(conn, {"name": "Broken", "kind": "template", "fields": ["Front"],
                           "templates": [{"name": "Card", "front": "{{#Front}}q{{/Wrong}}", "back": "a"}]})


def test_cloze_extra_field_does_not_generate_unanswerable_card(cards):
    conn, _, _ = cards
    add(cards, "cloze", fields={"Text": "{{c1::actual-answer}}", "Extra": "{{c9::not-on-the-front}}"})
    note = f.browser(conn)["notes"][0]
    assert [c["ordinal"] for c in note["cards"]] == [0]
    response = f.start_review(conn)
    assert "actual-answer" not in json.dumps(response)
    assert "actual-answer" in f.reveal(conn, response["card"]["token"])["back"]


def test_typed_template_prompts_input_without_sending_expected_answer(cards):
    conn, _, state = cards
    typ = f.save_type(conn, {"name": "Typing", "kind": "template", "fields": ["Front", "Back"],
                             "templates": [{"name": "Typed", "front": "{{Front}}<br>{{type:Back}}", "back": "{{Back}}"}]})
    f.save_note(conn, {"deck_id": state["decks"][0]["id"], "type_id": typ["id"],
                       "fields": {"Front": "question", "Back": "typed-answer-secret"}})
    response = web_flashcards.post(None, conn, "/api/flashcards/review", {})
    assert response["card"]["typed"] is True
    assert "typed-answer-secret" not in json.dumps(response)
    assert "typed-answer-secret" in web_flashcards.post(None, conn, "/api/flashcards/reveal", {"token": response["card"]["token"]})["back"]


def test_unsuspending_leech_allows_successful_relearning(cards):
    conn, clock, state = cards
    deck = state["decks"][0]
    f.save_deck(conn, {"id": deck["id"], "revision": deck["revision"], "name": deck["name"],
                       "settings": {"leech_threshold": 1}})
    add(cards)
    card, _ = review_once(conn, 4)
    clock[0] = datetime.fromisoformat(f.require(conn, "flash_cards", card["id"])["due"])
    review_once(conn, 1)
    leech = f.require(conn, "flash_cards", card["id"])
    f.action(conn, {"id": leech["id"], "revision": leech["revision"], "action": "suspend", "value": False})
    clock[0] = datetime.fromisoformat(leech["due"])
    review_once(conn, 3)
    recovered = f.require(conn, "flash_cards", card["id"])
    assert recovered["leech"] == 1 and recovered["suspended"] == 0
    assert recovered["state"] == "review"


def test_consecutive_undo_restores_learning_and_reusable_reveal_session(cards):
    conn, clock, _ = cards
    add(cards, "reversed")
    first, _ = review_once(conn, 1)
    learning = f.require(conn, "flash_cards", first["id"])
    clock[0] = datetime.fromisoformat(learning["due"])
    second, _ = review_once(conn, 3)
    f.undo(conn)
    restored = f.require(conn, "flash_cards", first["id"])
    assert restored["fsrs"] == learning["fsrs"]
    with pytest.raises(ValueError, match="Reveal"):
        f.grade(conn, second["token"], 3)
    assert "answer-only-secret" in f.reveal(conn, second["token"])["back"]
    f.undo(conn)
    assert f.require(conn, "flash_cards", first["id"])["state"] == "new"
    assert all(not c["buried_until"] for c in f.browser(conn)["notes"][0]["cards"])
    assert f.start_review(conn)["remaining"] == 2
    assert f.overview(conn)["stats"]["streak"] == 0


def _proto_field(number, value):
    """Encode public Anki protobuf scalars for real schema-18 APKG fixtures."""
    def varint(integer):
        result = bytearray()
        while integer > 127:
            result.append((integer & 127) | 128)
            integer >>= 7
        result.append(integer)
        return bytes(result)
    if isinstance(value, str): value = value.encode()
    if isinstance(value, bytes):
        return varint(number * 8 + 2) + varint(len(value)) + value
    return varint(number * 8) + varint(value)


def modern_apkg(payload, tmp_path, corrupt_media=False):
    """Produce Anki's normalized SQLite + zstd + protobuf package contract.

    Schema/field numbers follow Anki schema15_upgrade.sql, notetypes.proto,
    and import_export.proto; no importer internals are used by this fixture.
    """
    import zstandard
    source_zip = zipfile.ZipFile(io.BytesIO(payload))
    path = tmp_path / "normalized.sqlite"
    path.write_bytes(source_zip.read("collection.anki2"))
    database = sqlite3.connect(path)
    models_json, decks_json = database.execute("SELECT models,decks FROM col").fetchone()
    models, decks = json.loads(models_json), json.loads(decks_json)
    database.executescript("""
        CREATE TABLE notetypes(id INTEGER PRIMARY KEY,name TEXT,mtime_secs INTEGER,usn INTEGER,config BLOB);
        CREATE TABLE fields(ntid INTEGER,ord INTEGER,name TEXT,config BLOB,PRIMARY KEY(ntid,ord)) WITHOUT ROWID;
        CREATE TABLE templates(ntid INTEGER,ord INTEGER,name TEXT,mtime_secs INTEGER,usn INTEGER,config BLOB,PRIMARY KEY(ntid,ord)) WITHOUT ROWID;
        CREATE TABLE decks(id INTEGER PRIMARY KEY,name TEXT,mtime_secs INTEGER,usn INTEGER,common BLOB,kind BLOB);
        DROP TABLE kitty_card_state;
        DROP TABLE kitty_metadata;
        UPDATE col SET ver=18,models='',decks='';
    """)
    for ident, model in models.items():
        database.execute("INSERT INTO notetypes VALUES(?,?,0,0,?)", (int(ident), model["name"], _proto_field(1, model["type"])))
        for field in model["flds"]:
            database.execute("INSERT INTO fields VALUES(?,?,?,?)", (int(ident), field["ord"], field["name"], b""))
        for template in model["tmpls"]:
            config = _proto_field(1, template["qfmt"]) + _proto_field(2, template["afmt"])
            database.execute("INSERT INTO templates VALUES(?,?,?,0,0,?)", (int(ident), template["ord"], template["name"], config))
    for ident, deck in decks.items():
        database.execute("INSERT INTO decks VALUES(?,?,0,0,?,?)", (int(ident), deck["name"].replace("::", "\x1f"), b"", _proto_field(1, b"")))
    database.commit()
    database.close()
    manifest = b""
    output = io.BytesIO()
    compress = zstandard.ZstdCompressor().compress
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("meta", _proto_field(1, 3))
        archive.writestr("collection.anki21b", compress(path.read_bytes()))
        for index, filename in json.loads(source_zip.read("media")).items():
            media = source_zip.read(index)
            entry = _proto_field(1, filename) + _proto_field(2, len(media)) + _proto_field(3, hashlib.sha1(media).digest())
            manifest += _proto_field(1, entry)
            archive.writestr(index, compress(media + b"corrupt" if corrupt_media else media))
        archive.writestr("media", compress(manifest))
    source_zip.close()
    return output.getvalue()


def test_modern_normalized_apkg_import_media_cloze_templates_and_legacy_export(cards, tmp_path):
    conn, _, _ = cards
    deck = f.save_deck(conn, {"name": "Portable::Child"})
    add(cards, "cloze", deck=deck["id"])
    add(cards, "reversed", deck=deck["id"], fields={"Front": '<img src="local.png">question', "Back": "portable-answer"})
    f.upload_media(conn, "local.png", b"\x89PNG\r\n\x1a\nportable-media")
    modern = modern_apkg(f.export_apkg(conn, deck["id"]), tmp_path)
    target = connect(tmp_path / "modern-import.sqlite")
    try:
        migrate(target); f.bootstrap(target)
        response = web_flashcards.post(None, target, "/api/flashcards/import", {"format": "apkg", "data": base64.b64encode(modern).decode()})
        assert response["imported"] == 2
        assert f.overview(target)["stats"]["total"] == 4
        imported_deck = next(d for d in f.overview(target)["decks"] if d["name"] == "Portable::Child")
        front = f.start_review(target, imported_deck["id"])["card"]
        assert "hidden-one" not in front["front"]
        f.reveal(target, front["token"]); f.grade(target, front["token"], 4)
        next_card = f.start_review(target, imported_deck["id"])["card"]
        assert "data:image/png;base64," in next_card["front"]
        assert "portable-answer" not in next_card["front"]
        assert "portable-answer" in f.reveal(target, next_card["token"])["back"]
        assert f.import_apkg(target, modern)["duplicates"] == 2
        exported = f.export_apkg(target, imported_deck["id"])
        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            assert archive.read("collection.anki2").startswith(b"SQLite format 3\0")
            assert json.loads(archive.read("media")) == {"0": "local.png"}
    finally:
        target.close()


def test_corrupt_modern_media_fails_atomically_with_clear_error(cards, tmp_path):
    conn, _, _ = cards
    add(cards, fields={"Front": '<img src="local.png">question', "Back": "answer"})
    f.upload_media(conn, "local.png", b"\x89PNG\r\n\x1a\nportable-media")
    corrupt = modern_apkg(f.export_apkg(conn), tmp_path, corrupt_media=True)
    target = connect(tmp_path / "bad-import.sqlite")
    try:
        migrate(target); f.bootstrap(target)
        with pytest.raises(APIError, match="checksum"):
            web_flashcards.post(None, target, "/api/flashcards/import", {"format": "apkg", "data": base64.b64encode(corrupt).decode()})
        assert f.overview(target)["stats"]["total"] == 0
        assert f.overview(target)["media"] == []
    finally:
        target.close()


def test_apkg_exports_actual_dynamic_attachments_not_plain_filename_mentions(cards):
    conn, _, state = cards
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
    f.upload_media(conn, "local ü.png", image)
    f.upload_media(conn, "private.png", image)
    typ = f.save_type(conn, {"name": "Image field", "kind": "template", "fields": ["Prompt", "Image", "Answer"],
                             "templates": [{"name": "Image", "front": '{{Prompt}}<img src="{{Image}}">', "back": "{{Answer}}"}]})
    f.save_note(conn, {"deck_id": state["decks"][0]["id"], "type_id": typ["id"],
                       "fields": {"Prompt": "The text mentions private.png but does not attach it.",
                                  "Image": "local%20%C3%BC.png", "Answer": "answer"}})
    assert "data:image/png;base64," in f.start_review(conn)["card"]["front"]
    with zipfile.ZipFile(io.BytesIO(f.export_apkg(conn))) as archive:
        assert json.loads(archive.read("media")) == {"0": "local ü.png"}
        assert archive.read("0") == image


def test_undo_does_not_clobber_intervening_sibling_edits(cards):
    conn, _, _ = cards
    add(cards, "reversed")
    reviewed, _ = review_once(conn, 4)
    sibling = next(c for c in f.browser(conn)["notes"][0]["cards"] if c["id"] != reviewed["id"])
    f.action(conn, {"id": sibling["id"], "revision": sibling["revision"], "action": "suspend"})
    with pytest.raises(ValueError, match="Sibling changed"):
        f.undo(conn)
    assert f.require(conn, "flash_cards", sibling["id"])["suspended"] == 1
    assert f.require(conn, "flash_cards", reviewed["id"])["state"] == "review"


@pytest.mark.parametrize("modern,keep_memory", [(False, False), (False, True), (True, True)])
def test_external_anki_scheduled_cards_preserve_memory_and_can_be_reviewed(cards, tmp_path, modern, keep_memory):
    conn, clock, _ = cards
    add(cards)
    reviewed, _ = review_once(conn, 4)
    original = f.require(conn, "flash_cards", reviewed["id"])
    original_fsrs = json.loads(original["fsrs"])
    payload = f.export_apkg(conn)
    if modern:
        payload = modern_apkg(payload, tmp_path)
    else:
        path = tmp_path / "legacy.sqlite"
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            path.write_bytes(archive.read("collection.anki2"))
        database = sqlite3.connect(path)
        database.executescript("DROP TABLE kitty_card_state; DROP TABLE kitty_metadata;")
        if not keep_memory:
            database.execute("UPDATE cards SET data=''")
            database.execute("INSERT INTO revlog VALUES(?,?,0,4,1,0,2500,1000,0)",
                             (int(clock[0].timestamp() * 1000), reviewed["id"]))
        database.commit(); database.close()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("collection.anki21", path.read_bytes())
            archive.writestr("media", "{}")
        payload = output.getvalue()
    target = connect(tmp_path / "external-import.sqlite")
    try:
        migrate(target); f.bootstrap(target)
        assert f.import_apkg(target, payload)["imported"] == 1
        imported = f.browser(target)["notes"][0]["cards"][0]
        scheduled = f.require(target, "flash_cards", imported["id"])
        memory = json.loads(scheduled["fsrs"])
        assert scheduled["state"] == "review" and scheduled["reviews"] == 1
        assert memory["last_review"] == original_fsrs["last_review"]
        assert datetime.fromisoformat(scheduled["due"]).date() == datetime.fromisoformat(original["due"]).date()
        if keep_memory:
            assert memory["stability"] == original_fsrs["stability"]
            assert memory["difficulty"] == original_fsrs["difficulty"]
        else:
            assert memory["stability"] > 0 and 1 <= memory["difficulty"] <= 10
        clock[0] = datetime.fromisoformat(scheduled["due"])
        review_once(target, 3)
        assert f.require(target, "flash_cards", imported["id"])["reviews"] == 2
    finally:
        target.close()


def test_changing_note_type_updates_same_note_and_invalidates_old_review(cards):
    conn, _, state = cards
    note = add(cards)
    pending = f.start_review(conn)["card"]
    reversed_type = next(t for t in state["types"] if t["kind"] == "reversed")
    web_flashcards.post(None, conn, "/api/flashcards/note", {
        "id": note["id"], "revision": note["revision"], "deck_id": note["deck_id"],
        "type_id": reversed_type["id"], "fields": json.loads(note["fields"]), "tags": [],
    })
    browser = f.browser(conn)
    assert browser["total"] == 1 and browser["notes"][0]["id"] == note["id"]
    assert len(browser["notes"][0]["cards"]) == 2
    assert browser["notes"][0]["fields"]["Back"] == "answer-only-secret"
    with pytest.raises(ValueError, match="Card changed"):
        f.reveal(conn, pending["token"])


def test_flashcard_schema_contains_all_logical_notes_real_subdecks_and_archives(cards):
    conn, _, state = cards
    child = f.save_deck(conn, {"name": "Graph parent::Child", "settings": {}})
    parent = conn.execute("SELECT id FROM flash_decks WHERE name='Graph parent'").fetchone()[0]
    empty = f.save_deck(conn, {"name": "Empty graph deck", "settings": {}})
    note = add(cards, kind="reversed", deck=child["id"])
    f.action(conn, {"action": "archive", "id": note["id"], "revision": note["revision"], "value": True})
    conn.execute("UPDATE flash_decks SET archived=1 WHERE id=?", (child["id"],))
    expected = {note["id"]}
    for _ in range(web_atlas.SECTION_LIMIT + 3):
        expected.add(add(cards)["id"])
    graph = web_atlas.get(None, conn, "/api/atlas/sections/flashcards", {})
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert graph["total"] == graph["shown"] == len(nodes)
    assert {node["ref"] for node in nodes.values() if node["entity"] == "flashcard_note"} == expected
    assert {node["ref"] for node in nodes.values() if node["entity"] == "deck"} == {
        row[0] for row in conn.execute("SELECT id FROM flash_decks")}
    edges = {(link["source"], link["target"]) for link in graph["links"]}
    assert (f"deck:{parent}", f"deck:{child['id']}") in edges
    assert (f"deck:{child['id']}", f"flashcard_note:{note['id']}") in edges
    assert nodes[f"flashcard_note:{note['id']}"]["detail"] == "2 generated cards · archived"
    assert nodes[f"deck:{empty['id']}"]["detail"] == "0 notes · 0 cards"
    assert "answer-only-secret" not in json.dumps(graph)
    assert "synthetic-question" not in json.dumps(graph)
    focused = web_atlas.get(None, conn, "/api/atlas/sections/flashcards", {"focus": f"deck:{parent}"})
    assert {node["id"] for node in focused["nodes"]} == {
        f"deck:{parent}", f"deck:{child['id']}", f"flashcard_note:{note['id']}"}
    exact = web_flashcards.get(None, conn, "/api/flashcards/note", {"id": str(note["id"])})["note"]
    assert exact["id"] == note["id"] and exact["deck_id"] == child["id"]
    assert exact["archived"] == 1 and len(exact["cards"]) == 2
    # Existing exact nid reference links retain their browser behavior.
    browser = web_flashcards.get(None, conn, "/api/flashcards/browser", {"search": f"nid:{note['id']}"})
    assert [row["id"] for row in browser["notes"]] == [note["id"]]


def test_flashcard_schema_and_exact_editor_handle_zero_cards_and_deleted_items(cards):
    conn, _, _ = cards
    note = add(cards)
    conn.execute("DELETE FROM flash_cards WHERE note_id=?", (note["id"],))
    graph = web_atlas.get(None, conn, "/api/atlas/sections/flashcards", {})
    assert any(node["id"] == f"flashcard_note:{note['id']}" and node["detail"] == "0 generated cards"
               for node in graph["nodes"])
    exact = web_flashcards.get(None, conn, "/api/flashcards/note", {"id": str(note["id"])})["note"]
    assert exact["id"] == note["id"] and exact["cards"] == []
    f.action(conn, {"action": "delete", "id": note["id"], "revision": note["revision"]})
    with pytest.raises(APIError, match="no longer exists") as error:
        web_flashcards.get(None, conn, "/api/flashcards/note", {"id": str(note["id"])})
    assert error.value.status == 404
    graph = web_atlas.get(None, conn, "/api/atlas/sections/flashcards", {})
    assert not any(node["entity"] == "flashcard_note" for node in graph["nodes"])
    deck = conn.execute("INSERT INTO flash_decks(name,settings,created_at) VALUES('Deleted deck','{}','')").lastrowid
    conn.execute("DELETE FROM flash_decks WHERE id=?", (deck,))
    with pytest.raises(APIError, match="no longer exists") as error:
        web_atlas.get(None, conn, "/api/atlas/sections/flashcards", {"focus": f"deck:{deck}"})
    assert error.value.status == 404


def test_overview_splits_each_deck_load_and_measures_recall(cards):
    conn, clock, _ = cards
    deck = f.save_deck(conn, {"name": "Load", "settings": {}})
    child = f.save_deck(conn, {"name": "Load::Child", "settings": {}})
    for _ in range(3):
        add(cards, deck=child["id"])
    review_once(conn, 1, deck_id=deck["id"])  # new → learning
    review_once(conn, 4, deck_id=deck["id"])  # new → review
    decks = {d["name"]: d for d in f.overview(conn)["decks"]}
    assert (decks["Load"]["new"], decks["Load"]["learning"], decks["Load"]["review"]) == (1, 0, 0)
    assert decks["Load"]["recall"] is None
    clock[0] += timedelta(days=30)
    decks = {d["name"]: d for d in f.overview(conn)["decks"]}
    for name in ("Load", "Load::Child"):
        assert (decks[name]["due"], decks[name]["new"], decks[name]["learning"], decks[name]["review"]) == (2, 1, 1, 1)
    review_once(conn, 3, deck_id=deck["id"])  # a learning step, not a due review
    review_once(conn, 1, deck_id=deck["id"])  # the review card lapses
    decks = {d["name"]: d for d in f.overview(conn)["decks"]}
    assert decks["Load"]["recall"] == decks["Load::Child"]["recall"] == 0.0
    assert decks["Default"]["recall"] is None and decks["Default"]["learning"] == 0
    clock[0] += timedelta(days=91)
    assert {d["name"]: d for d in f.overview(conn)["decks"]}["Load"]["recall"] is None


def test_stats_forecast_retention_maturity_heatmap_and_queue_for_a_scope(cards):
    conn, clock, _ = cards
    deck = f.save_deck(conn, {"name": "Stats", "settings": {"retention": 0.85}})
    child = f.save_deck(conn, {"name": "Stats::Child", "settings": {}})
    other = f.save_deck(conn, {"name": "Other", "settings": {}})
    notes = [add(cards, deck=child["id"]) for _ in range(4)]
    add(cards, deck=other["id"])
    review_once(conn, 4, deck_id=deck["id"])  # new → review, due in days
    review_once(conn, 1, deck_id=deck["id"])  # new → learning, due in a minute
    scoped = f.stats(conn, deck["id"], "UTC")
    assert len(scoped["forecast"]) == 30 and scoped["forecast"][0]["day"] == "2026-10-14"
    assert scoped["forecast"][0]["learning"] == 1
    assert sum(day["review"] for day in scoped["forecast"]) == 1
    assert scoped["maturity"] == {"new": 2, "learning": 1, "young": 1, "mature": 0}
    assert len(scoped["heatmap"]) == 182 and scoped["heatmap"][-1] == {"day": "2026-10-14", "reviews": 2}
    assert scoped["streak"] == 1
    assert scoped["queue"] == {"learning": 0, "review": 0, "new": 2, "suspended": 0, "leeches": 0, "buried": 0, "seconds_per_card": 1.234}
    retention = scoped["retention"]
    assert retention["target"] == 0.85 and 0 < retention["decay"] < 1
    assert retention["stability"]["weak"] == retention["stability"]["strong"] > 0
    assert retention["measured"] == []
    whole = f.stats(conn, None, "UTC")
    assert whole["maturity"]["new"] == 3 and whole["retention"]["target"] == 0.9
    # Overdue cards fold into today; a remembered due review is measured.
    clock[0] += timedelta(days=60)
    later = f.stats(conn, deck["id"], "UTC")
    assert (later["forecast"][0]["review"], later["forecast"][0]["learning"]) == (1, 1)
    assert later["queue"]["learning"] == 1 and later["queue"]["review"] == 1
    assert later["streak"] == 0 and later["heatmap"][-1]["reviews"] == 0
    review_once(conn, 3, deck_id=deck["id"])  # the learning step
    review_once(conn, 4, deck_id=deck["id"])  # the overdue review, remembered
    measured = f.stats(conn, deck["id"], "UTC")["retention"]["measured"]
    assert [(bucket["n"], bucket["r"]) for bucket in measured] == [(1, 1.0)] and measured[0]["t"] >= 30
    assert f.stats(conn, deck["id"], "UTC")["maturity"]["mature"] == 1
    # Suspended, leech and buried cards leave today's queue and are counted.
    new_cards = [conn.execute("SELECT id,revision FROM flash_cards WHERE note_id=? AND state='new'", (note["id"],)).fetchone() for note in notes]
    new_cards = [card for card in new_cards if card]
    f.action(conn, {"action": "suspend", "id": new_cards[0]["id"], "revision": new_cards[0]["revision"]})
    f.action(conn, {"action": "bury", "id": new_cards[1]["id"], "revision": new_cards[1]["revision"]})
    queue = f.stats(conn, deck["id"], "UTC")["queue"]
    assert (queue["new"], queue["suspended"], queue["buried"]) == (0, 1, 1)
    routed = web_flashcards.get(None, conn, "/api/flashcards/stats", {"deck_id": str(deck["id"]), "timezone": "America/New_York"})
    assert routed["forecast"][0]["day"] == "2026-12-13"
    with pytest.raises(APIError, match="timezone"):
        web_flashcards.get(None, conn, "/api/flashcards/stats", {"timezone": "Mars/Base"})
