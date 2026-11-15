"""Flashcards through rika-server (server/src/flashcards): scheduling, archive round-trips and
safe review invariants; no network. The clock is the front's test clock (flashcards_front).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from flashcards_front import flashcard_front
from kitty import web_atlas


@pytest.fixture(scope="module")
def front(tmp_path_factory):
    with flashcard_front(tmp_path_factory.mktemp("cards")) as running:
        yield running


@pytest.fixture(scope="module")
def other_front(tmp_path_factory):
    """A second workspace to import into."""
    with flashcard_front(tmp_path_factory.mktemp("import"), "target") as running:
        yield running


@pytest.fixture
def cards(front):
    front.reset()
    return front


@pytest.fixture
def target(other_front):
    other_front.reset()
    return other_front


def export(cards, kind="apkg", deck=None) -> bytes:
    body = {"format": kind} | ({"deck_id": deck} if deck else {})
    return base64.b64decode(cards.ok("/api/flashcards/export", body)["data"])


def import_apkg(cards, payload: bytes) -> dict:
    return cards.ok("/api/flashcards/import", {"format": "apkg", "data": base64.b64encode(payload).decode()})


def upload(cards, name, data):
    return cards.ok("/api/flashcards/media", {"name": name, "data": base64.b64encode(data).decode()})


def test_front_does_not_leak_answer_and_grading_requires_reveal(cards):
    cards.add()
    response = cards.start()
    assert "answer-only-secret" not in json.dumps(response)
    token = response["card"]["token"]
    cards.fails("/api/flashcards/grade", {"token": token, "rating": 3}, match="Reveal")
    revealed = cards.reveal(token)
    assert "answer-only-secret" in revealed["back"]
    assert [x["rating"] for x in revealed["intervals"]] == [1, 2, 3, 4]
    cards.grade(token, 3)
    cards.fails("/api/flashcards/grade", {"token": token, "rating": 3}, match="expired")


@pytest.mark.parametrize("rating,seconds,state", [(1, 60, "learning"), (2, 330, "learning"), (3, 600, "learning"), (4, None, "review")])
def test_first_ratings_have_precise_fsrs_learning_boundaries(cards, rating, seconds, state):
    cards.add()
    card, _ = cards.review_once(rating)
    persisted = cards.row("flash_cards", card["id"])
    assert persisted["state"] == state and persisted["reviews"] == 1
    due = datetime.fromisoformat(persisted["due"])
    assert due > cards.now
    if seconds is not None:
        assert due - cards.now == timedelta(seconds=seconds)
        cards.set_clock(due - timedelta(microseconds=1))
        assert cards.start()["card"] is None
        cards.set_clock(due)
        assert cards.start()["card"]["id"] == card["id"]


def test_daily_limits_learning_bypass_local_midnight_and_parent_budget(cards):
    parent = cards.deck(name="Parent", settings={"new_limit": 1, "review_limit": 0, "timezone": "America/New_York"})
    child = cards.deck(name="Parent::Child", settings={"new_limit": 20, "timezone": "America/New_York"})
    cards.add(deck=child["id"])
    cards.add(deck=child["id"])
    assert cards.start(deck_id=parent["id"])["remaining"] == 1
    card, _ = cards.review_once(1, deck_id=parent["id"])
    assert cards.start(deck_id=parent["id"])["card"] is None
    cards.set_clock(cards.now + timedelta(seconds=60))
    assert cards.start(deck_id=parent["id"])["card"]["id"] == card["id"]
    cards.review_once(4, deck_id=parent["id"])
    cards.set_clock(datetime(2026, 10, 15, 3, 59, tzinfo=timezone.utc))
    assert cards.start(deck_id=parent["id"])["card"] is None
    cards.set_clock(datetime(2026, 10, 15, 4, 0, tzinfo=timezone.utc))
    assert cards.start(deck_id=parent["id"])["card"]["state"] == "new"


def card_rows(cards, note_id):
    with cards.db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM flash_cards WHERE note_id=? ORDER BY ordinal", (note_id,))]


def test_cloze_indices_hide_only_target_and_sibling_bury_undo(cards):
    note = cards.add("cloze")
    response = cards.start()
    front = response["card"]["front"]
    assert "hidden-one" not in front and "second-one" not in front
    assert "hidden-two" in front and "hint" in front and "extra-only" not in front
    original = card_rows(cards, note["id"])
    cards.reveal(response["card"]["token"])
    cards.grade(response["card"]["token"], 4)
    sibling = cards.row("flash_cards", original[1]["id"])
    assert sibling["buried_until"].startswith("2026-10-15T00:00")
    assert cards.start()["card"] is None
    cards.undo()
    for before, after in zip(original, card_rows(cards, note["id"])):
        for key in ("state", "due", "fsrs", "buried_until", "reviews", "lapses"):
            assert before[key] == after[key]
    assert cards.ok("/api/flashcards/history")["reviews"][0]["undone"] == 1


def test_practice_does_not_change_schedule_limits_or_streak(cards):
    note = cards.add(tags=["custom"])
    before = card_rows(cards, note["id"])[0]
    cards.review_once(1, mode="filtered", search="tag:custom")
    cards.review_once(4, mode="cram", search="tag:custom")
    assert cards.row("flash_cards", before["id"]) == before
    assert cards.overview()["stats"]["streak"] == 0
    assert len(cards.ok("/api/flashcards/history")["reviews"]) == 2
    cards.undo()
    assert cards.row("flash_cards", before["id"]) == before


def test_leech_lapse_relearning_suspend_and_undo(cards):
    deck = cards.overview()["decks"][0]
    cards.deck(id=deck["id"], revision=deck["revision"], name=deck["name"], settings={"leech_threshold": 1})
    cards.add()
    card, _ = cards.review_once(4)
    cards.set_clock(datetime.fromisoformat(cards.row("flash_cards", card["id"])["due"]))
    cards.review_once(1)
    failed = cards.row("flash_cards", card["id"])
    assert failed["state"] == "relearning" and failed["lapses"] == 1
    assert failed["leech"] == failed["suspended"] == 1
    assert datetime.fromisoformat(failed["due"]) - cards.now == timedelta(seconds=600)
    assert cards.start()["card"] is None
    cards.undo()
    restored = cards.row("flash_cards", card["id"])
    assert restored["state"] == "review" and not restored["leech"] and not restored["suspended"]


def test_optimistic_edits_and_archive_invalidate_review(cards):
    note = cards.add()
    card = cards.start()["card"]
    cards.reveal(card["token"])
    cards.action(id=note["id"], revision=note["revision"], action="archive")
    cards.fails("/api/flashcards/grade", {"token": card["token"], "rating": 3}, match="eligible")
    cards.fails("/api/flashcards/note", {"id": note["id"], "revision": note["revision"], "deck_id": note["deck_id"],
                                         "type_id": note["type_id"], "fields": json.loads(note["fields"]), "tags": []}, match="changed")
    assert cards.browser(search="is:archived")["total"] == 1


def test_review_state_survives_the_front_reopening_the_database(cards):
    cards.add()
    card, _ = cards.review_once(3)
    expected = cards.row("flash_cards", card["id"])
    with cards.db() as conn:
        assert dict(conn.execute("SELECT * FROM flash_cards WHERE id=?", (card["id"],)).fetchone()) == expected
    assert cards.overview()["stats"]["streak"] == 1


def test_safe_html_local_media_and_remote_js_are_not_rendered(cards):
    png = b"\x89PNG\r\n\x1a\nsynthetic-local-image"
    upload(cards, "local.png", png)
    cards.add(fields={"Front": '<script>alert("private")</script><img src="https://attacker.invalid/x" onerror="alert(1)">'
                               '<img src="local.png"><a href="javascript:alert(1)">safe text</a>'
                               '<b style="background:url(https://attacker.invalid)">bold</b>', "Back": "b"})
    rendered = cards.start()["card"]["front"]
    assert "attacker" not in rendered and "javascript" not in rendered and "onerror" not in rendered and "private" not in rendered
    assert "data:image/png;base64," in rendered and "<b>bold</b>" in rendered
    for name, payload in (("../escape.png", png), ("bad.svg", b'<svg onload="alert(1)"/>'), ("bad.html", b"<html>")):
        cards.fails("/api/flashcards/media", {"name": name, "data": base64.b64encode(payload).decode()})


def test_apkg_roundtrip_preserves_cards_cloze_media_and_exact_fsrs(cards, target):
    cards.add("cloze")
    cards.add("reversed", fields={"Front": '<img src="local.png">synthetic', "Back": "roundtrip-answer"})
    upload(cards, "local.png", b"\x89PNG\r\n\x1a\nsynthetic-media")
    cards.review_once(3)
    payload = export(cards)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.read("collection.anki2").startswith(b"SQLite format 3\0")
        assert json.loads(archive.read("media")) == {"0": "local.png"}
    assert import_apkg(target, payload)["imported"] == 2
    originals = cards.browser(limit=100)["notes"]
    imported = target.browser(limit=100)["notes"]
    assert sum(len(n["cards"]) for n in imported) == 4
    with target.db() as conn:
        by_guid = {r["guid"]: dict(r) for r in conn.execute("SELECT * FROM flash_notes")}
        for note in originals:
            matching = by_guid[note["guid"]]
            assert json.loads(matching["fields"]) == note["fields"]
            for card in note["cards"]:
                before = cards.row("flash_cards", card["id"])
                after = dict(conn.execute("SELECT * FROM flash_cards WHERE note_id=? AND ordinal=?", (matching["id"], card["ordinal"])).fetchone())
                for key in ("state", "due", "buried_until", "reviews", "lapses", "leech", "suspended"):
                    assert before[key] == after[key]
                if before["fsrs"]:
                    a, b = json.loads(before["fsrs"]), json.loads(after["fsrs"])
                    a.pop("card_id"); b.pop("card_id")
                    assert a == b
        media = conn.execute("SELECT content FROM flash_media WHERE name='local.png'").fetchone()[0]
    with cards.db() as conn:
        assert media == conn.execute("SELECT content FROM flash_media WHERE name='local.png'").fetchone()[0]
    assert import_apkg(target, payload)["duplicates"] == 2


def test_csv_tsv_unicode_quotes_and_atomic_invalid_import(cards):
    state = cards.overview()
    deck, typ = state["decks"][0]["id"], state["types"][0]["id"]
    cards.ok("/api/flashcards/import", {"format": "csv", "text": 'Front,Back,Tags\n"question, comma","answer\nline",one two\n',
                                        "deck_id": deck, "type_id": typ})
    exported = export(cards, "tsv", deck).decode()
    assert "question, comma" in exported and "answer\nline" in exported
    assert cards.ok("/api/flashcards/import", {"format": "tsv", "text": exported, "deck_id": deck, "type_id": typ})["imported"] == 1
    before = cards.overview()["stats"]["total"]
    cards.fails("/api/flashcards/import", {"format": "csv", "text": "valid,first\ninvalid\n", "deck_id": deck, "type_id": typ})
    assert cards.overview()["stats"]["total"] == before


def test_api_consumer_validation_and_no_answer_in_review(cards):
    cards.add()
    cards.fails("/api/flashcards/import", {"format": "apkg", "data": "not base64"}, match="base64")
    cards.fails("/api/flashcards/browser?limit=0", match="Invalid browser page")
    cards.fails("/api/flashcards/unknown", {}, status=404, match="API route not found")
    response = cards.start()
    assert "answer-only-secret" not in json.dumps(response)
    cards.fails("/api/flashcards/grade", {"token": response["card"]["token"], "rating": 3})
    cards.fails("/api/flashcards?x=1", match="Overview takes no parameters")
    cards.fails("/api/flashcards/deck?x=1", {"name": "x"}, match="does not accept query parameters")


def test_nested_optional_templates_skip_blank_cards_and_keep_schedules(cards):
    typ = cards.ok("/api/flashcards/type", {
        "name": "Optional reverse", "kind": "template", "fields": ["Front", "Back", "Reverse"],
        "templates": [
            {"name": "Forward", "front": "{{#Front}}{{#Front}}{{Front}}{{/Front}}{{/Front}}", "back": "{{Back}}"},
            {"name": "Reverse", "front": "{{#Reverse}}{{Back}}{{/Reverse}}", "back": "{{Front}}"},
        ],
    })["type"]
    default = cards.overview()["decks"][0]["id"]
    note = cards.ok("/api/flashcards/note", {"deck_id": default, "type_id": typ["id"],
                                             "fields": {"Front": "question", "Back": "answer", "Reverse": ""}})["note"]
    assert [c["ordinal"] for c in cards.browser()["notes"][0]["cards"]] == [0]
    reviewed, _ = cards.review_once(4)
    scheduled = cards.row("flash_cards", reviewed["id"])
    cards.ok("/api/flashcards/note", {"id": note["id"], "revision": note["revision"], "deck_id": note["deck_id"],
                                      "type_id": typ["id"], "fields": {"Front": "question", "Back": "answer", "Reverse": "yes"}})
    assert {c["ordinal"] for c in cards.browser()["notes"][0]["cards"]} == {0, 1}
    assert cards.row("flash_cards", reviewed["id"])["fsrs"] == scheduled["fsrs"]
    cards.fails("/api/flashcards/type", {"name": "Broken", "kind": "template", "fields": ["Front"],
                                         "templates": [{"name": "Card", "front": "{{#Front}}q{{/Wrong}}", "back": "a"}]}, match="nested")


def test_cloze_extra_field_does_not_generate_unanswerable_card(cards):
    cards.add("cloze", fields={"Text": "{{c1::actual-answer}}", "Extra": "{{c9::not-on-the-front}}"})
    assert [c["ordinal"] for c in cards.browser()["notes"][0]["cards"]] == [0]
    response = cards.start()
    assert "actual-answer" not in json.dumps(response)
    assert "actual-answer" in cards.reveal(response["card"]["token"])["back"]


def test_typed_template_prompts_input_without_sending_expected_answer(cards):
    typ = cards.ok("/api/flashcards/type", {"name": "Typing", "kind": "template", "fields": ["Front", "Back"],
                                            "templates": [{"name": "Typed", "front": "{{Front}}<br>{{type:Back}}", "back": "{{Back}}"}]})["type"]
    cards.ok("/api/flashcards/note", {"deck_id": cards.overview()["decks"][0]["id"], "type_id": typ["id"],
                                      "fields": {"Front": "question", "Back": "typed-answer-secret"}})
    response = cards.start()
    assert response["card"]["typed"] is True
    assert "typed-answer-secret" not in json.dumps(response)
    assert "typed-answer-secret" in cards.reveal(response["card"]["token"])["back"]


def test_unsuspending_leech_allows_successful_relearning(cards):
    deck = cards.overview()["decks"][0]
    cards.deck(id=deck["id"], revision=deck["revision"], name=deck["name"], settings={"leech_threshold": 1})
    cards.add()
    card, _ = cards.review_once(4)
    cards.set_clock(datetime.fromisoformat(cards.row("flash_cards", card["id"])["due"]))
    cards.review_once(1)
    leech = cards.row("flash_cards", card["id"])
    cards.action(id=leech["id"], revision=leech["revision"], action="suspend", value=False)
    cards.set_clock(datetime.fromisoformat(leech["due"]))
    cards.review_once(3)
    recovered = cards.row("flash_cards", card["id"])
    assert recovered["leech"] == 1 and recovered["suspended"] == 0
    assert recovered["state"] == "review"


def test_consecutive_undo_restores_learning_and_reusable_reveal_session(cards):
    cards.add("reversed")
    first, _ = cards.review_once(1)
    learning = cards.row("flash_cards", first["id"])
    cards.set_clock(datetime.fromisoformat(learning["due"]))
    second, _ = cards.review_once(3)
    cards.undo()
    assert cards.row("flash_cards", first["id"])["fsrs"] == learning["fsrs"]
    cards.fails("/api/flashcards/grade", {"token": second["token"], "rating": 3}, match="Reveal")
    assert "answer-only-secret" in cards.reveal(second["token"])["back"]
    cards.undo()
    assert cards.row("flash_cards", first["id"])["state"] == "new"
    assert all(not c["buried_until"] for c in cards.browser()["notes"][0]["cards"])
    assert cards.start()["remaining"] == 2
    assert cards.overview()["stats"]["streak"] == 0


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


def test_modern_normalized_apkg_import_media_cloze_templates_and_legacy_export(cards, target, tmp_path):
    deck = cards.deck(name="Portable::Child")
    cards.add("cloze", deck=deck["id"])
    cards.add("reversed", deck=deck["id"], fields={"Front": '<img src="local.png">question', "Back": "portable-answer"})
    upload(cards, "local.png", b"\x89PNG\r\n\x1a\nportable-media")
    modern = modern_apkg(export(cards, deck=deck["id"]), tmp_path)
    assert import_apkg(target, modern)["imported"] == 2
    assert target.overview()["stats"]["total"] == 4
    imported_deck = next(d for d in target.overview()["decks"] if d["name"] == "Portable::Child")
    front = target.start(deck_id=imported_deck["id"])["card"]
    assert "hidden-one" not in front["front"]
    target.reveal(front["token"]); target.grade(front["token"], 4)
    next_card = target.start(deck_id=imported_deck["id"])["card"]
    assert "data:image/png;base64," in next_card["front"]
    assert "portable-answer" not in next_card["front"]
    assert "portable-answer" in target.reveal(next_card["token"])["back"]
    assert import_apkg(target, modern)["duplicates"] == 2
    with zipfile.ZipFile(io.BytesIO(export(target, deck=imported_deck["id"]))) as archive:
        assert archive.read("collection.anki2").startswith(b"SQLite format 3\0")
        assert json.loads(archive.read("media")) == {"0": "local.png"}


def test_corrupt_modern_media_fails_atomically_with_clear_error(cards, target, tmp_path):
    cards.add(fields={"Front": '<img src="local.png">question', "Back": "answer"})
    upload(cards, "local.png", b"\x89PNG\r\n\x1a\nportable-media")
    corrupt = modern_apkg(export(cards), tmp_path, corrupt_media=True)
    target.fails("/api/flashcards/import", {"format": "apkg", "data": base64.b64encode(corrupt).decode()}, match="checksum")
    assert target.overview()["stats"]["total"] == 0
    assert target.overview()["media"] == []


def test_apkg_exports_actual_dynamic_attachments_not_plain_filename_mentions(cards):
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
    upload(cards, "local ü.png", image)
    upload(cards, "private.png", image)
    typ = cards.ok("/api/flashcards/type", {"name": "Image field", "kind": "template", "fields": ["Prompt", "Image", "Answer"],
                                            "templates": [{"name": "Image", "front": '{{Prompt}}<img src="{{Image}}">', "back": "{{Answer}}"}]})["type"]
    cards.ok("/api/flashcards/note", {"deck_id": cards.overview()["decks"][0]["id"], "type_id": typ["id"],
                                      "fields": {"Prompt": "The text mentions private.png but does not attach it.",
                                                 "Image": "local%20%C3%BC.png", "Answer": "answer"}})
    assert "data:image/png;base64," in cards.start()["card"]["front"]
    with zipfile.ZipFile(io.BytesIO(export(cards))) as archive:
        assert json.loads(archive.read("media")) == {"0": "local ü.png"}
        assert archive.read("0") == image


def test_undo_does_not_clobber_intervening_sibling_edits(cards):
    cards.add("reversed")
    reviewed, _ = cards.review_once(4)
    sibling = next(c for c in cards.browser()["notes"][0]["cards"] if c["id"] != reviewed["id"])
    cards.action(id=sibling["id"], revision=sibling["revision"], action="suspend")
    cards.fails("/api/flashcards/undo", {}, match="Sibling changed")
    assert cards.row("flash_cards", sibling["id"])["suspended"] == 1
    assert cards.row("flash_cards", reviewed["id"])["state"] == "review"


@pytest.mark.parametrize("modern,keep_memory", [(False, False), (False, True), (True, True)])
def test_external_anki_scheduled_cards_preserve_memory_and_can_be_reviewed(cards, target, tmp_path, modern, keep_memory):
    cards.add()
    reviewed, _ = cards.review_once(4)
    original = cards.row("flash_cards", reviewed["id"])
    original_fsrs = json.loads(original["fsrs"])
    payload = export(cards)
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
                             (int(cards.now.timestamp() * 1000), reviewed["id"]))
        database.commit(); database.close()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("collection.anki21", path.read_bytes())
            archive.writestr("media", "{}")
        payload = output.getvalue()
    assert import_apkg(target, payload)["imported"] == 1
    imported = target.browser()["notes"][0]["cards"][0]
    scheduled = target.row("flash_cards", imported["id"])
    memory = json.loads(scheduled["fsrs"])
    assert scheduled["state"] == "review" and scheduled["reviews"] == 1
    assert memory["last_review"] == original_fsrs["last_review"]
    assert datetime.fromisoformat(scheduled["due"]).date() == datetime.fromisoformat(original["due"]).date()
    if keep_memory:
        assert memory["stability"] == original_fsrs["stability"]
        assert memory["difficulty"] == original_fsrs["difficulty"]
    else:
        assert memory["stability"] > 0 and 1 <= memory["difficulty"] <= 10
    target.set_clock(datetime.fromisoformat(scheduled["due"]))
    target.review_once(3)
    assert target.row("flash_cards", imported["id"])["reviews"] == 2


def test_changing_note_type_updates_same_note_and_invalidates_old_review(cards):
    note = cards.add()
    pending = cards.start()["card"]
    reversed_type = next(t for t in cards.overview()["types"] if t["kind"] == "reversed")
    cards.ok("/api/flashcards/note", {"id": note["id"], "revision": note["revision"], "deck_id": note["deck_id"],
                                      "type_id": reversed_type["id"], "fields": json.loads(note["fields"]), "tags": []})
    browser = cards.browser()
    assert browser["total"] == 1 and browser["notes"][0]["id"] == note["id"]
    assert len(browser["notes"][0]["cards"]) == 2
    assert browser["notes"][0]["fields"]["Back"] == "answer-only-secret"
    cards.fails("/api/flashcards/reveal", {"token": pending["token"]}, match="Card changed")


def test_flashcard_schema_contains_all_logical_notes_real_subdecks_and_archives(cards):
    child = cards.deck(name="Graph parent::Child", settings={})
    empty = cards.deck(name="Empty graph deck", settings={})
    note = cards.add(kind="reversed", deck=child["id"])
    cards.action(action="archive", id=note["id"], revision=note["revision"], value=True)
    with cards.db() as conn:
        parent = conn.execute("SELECT id FROM flash_decks WHERE name='Graph parent'").fetchone()[0]
        conn.execute("UPDATE flash_decks SET archived=1 WHERE id=?", (child["id"],))
    expected = {note["id"]}
    for _ in range(web_atlas.SECTION_LIMIT + 3):
        expected.add(cards.add()["id"])
    with cards.db() as conn:
        graph = web_atlas.get(None, conn, "/api/atlas/sections/flashcards", {})
        nodes = {node["id"]: node for node in graph["nodes"]}
        assert graph["total"] == graph["shown"] == len(nodes)
        assert {node["ref"] for node in nodes.values() if node["entity"] == "flashcard_note"} == expected
        assert {node["ref"] for node in nodes.values() if node["entity"] == "deck"} == {row[0] for row in conn.execute("SELECT id FROM flash_decks")}
        edges = {(link["source"], link["target"]) for link in graph["links"]}
        assert (f"deck:{parent}", f"deck:{child['id']}") in edges
        assert (f"deck:{child['id']}", f"flashcard_note:{note['id']}") in edges
        assert nodes[f"flashcard_note:{note['id']}"]["detail"] == "2 generated cards · archived"
        assert nodes[f"deck:{empty['id']}"]["detail"] == "0 notes · 0 cards"
        assert "answer-only-secret" not in json.dumps(graph) and "synthetic-question" not in json.dumps(graph)
        focused = web_atlas.get(None, conn, "/api/atlas/sections/flashcards", {"focus": f"deck:{parent}"})
        assert {node["id"] for node in focused["nodes"]} == {f"deck:{parent}", f"deck:{child['id']}", f"flashcard_note:{note['id']}"}
    exact = cards.ok(f"/api/flashcards/note?id={note['id']}")["note"]
    assert exact["id"] == note["id"] and exact["deck_id"] == child["id"]
    assert exact["archived"] == 1 and len(exact["cards"]) == 2
    # Existing exact nid reference links retain their browser behavior.
    assert [row["id"] for row in cards.browser(search=f"nid:{note['id']}")["notes"]] == [note["id"]]


def test_flashcard_schema_and_exact_editor_handle_zero_cards_and_deleted_items(cards):
    note = cards.add()
    with cards.db() as conn:
        conn.execute("DELETE FROM flash_cards WHERE note_id=?", (note["id"],))
        graph = web_atlas.get(None, conn, "/api/atlas/sections/flashcards", {})
    assert any(node["id"] == f"flashcard_note:{note['id']}" and node["detail"] == "0 generated cards" for node in graph["nodes"])
    exact = cards.ok(f"/api/flashcards/note?id={note['id']}")["note"]
    assert exact["id"] == note["id"] and exact["cards"] == []
    cards.action(action="delete", id=note["id"], revision=note["revision"])
    cards.fails(f"/api/flashcards/note?id={note['id']}", status=404, match="no longer exists")
    with cards.db() as conn:
        graph = web_atlas.get(None, conn, "/api/atlas/sections/flashcards", {})
        assert not any(node["entity"] == "flashcard_note" for node in graph["nodes"])


def test_overview_splits_each_deck_load_and_measures_recall(cards):
    deck = cards.deck(name="Load", settings={})
    child = cards.deck(name="Load::Child", settings={})
    for _ in range(3):
        cards.add(deck=child["id"])
    cards.review_once(1, deck_id=deck["id"])  # new → learning
    cards.review_once(4, deck_id=deck["id"])  # new → review
    decks = {d["name"]: d for d in cards.overview()["decks"]}
    assert (decks["Load"]["new"], decks["Load"]["learning"], decks["Load"]["review"]) == (1, 0, 0)
    assert decks["Load"]["recall"] is None
    cards.set_clock(cards.now + timedelta(days=30))
    decks = {d["name"]: d for d in cards.overview()["decks"]}
    for name in ("Load", "Load::Child"):
        assert (decks[name]["due"], decks[name]["new"], decks[name]["learning"], decks[name]["review"]) == (2, 1, 1, 1)
    cards.review_once(3, deck_id=deck["id"])  # a learning step, not a due review
    cards.review_once(1, deck_id=deck["id"])  # the review card lapses
    decks = {d["name"]: d for d in cards.overview()["decks"]}
    assert decks["Load"]["recall"] == decks["Load::Child"]["recall"] == 0.0
    assert decks["Default"]["recall"] is None and decks["Default"]["learning"] == 0
    cards.set_clock(cards.now + timedelta(days=91))
    assert {d["name"]: d for d in cards.overview()["decks"]}["Load"]["recall"] is None


def test_stats_forecast_retention_maturity_heatmap_and_queue_for_a_scope(cards):
    deck = cards.deck(name="Stats", settings={"retention": 0.85})
    child = cards.deck(name="Stats::Child", settings={})
    other = cards.deck(name="Other", settings={})
    notes = [cards.add(deck=child["id"]) for _ in range(4)]
    cards.add(deck=other["id"])
    cards.review_once(4, deck_id=deck["id"])  # new → review, due in days
    cards.review_once(1, deck_id=deck["id"])  # new → learning, due in a minute
    stats = lambda deck_id=None, zone="UTC": cards.ok("/api/flashcards/stats?timezone=" + zone + (f"&deck_id={deck_id}" if deck_id else ""))
    scoped = stats(deck["id"])
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
    whole = stats()
    assert whole["maturity"]["new"] == 3 and whole["retention"]["target"] == 0.9
    # Overdue cards fold into today; a remembered due review is measured.
    cards.set_clock(cards.now + timedelta(days=60))
    later = stats(deck["id"])
    assert (later["forecast"][0]["review"], later["forecast"][0]["learning"]) == (1, 1)
    assert later["queue"]["learning"] == 1 and later["queue"]["review"] == 1
    assert later["streak"] == 0 and later["heatmap"][-1]["reviews"] == 0
    cards.review_once(3, deck_id=deck["id"])  # the learning step
    cards.review_once(4, deck_id=deck["id"])  # the overdue review, remembered
    measured = stats(deck["id"])["retention"]["measured"]
    assert [(bucket["n"], bucket["r"]) for bucket in measured] == [(1, 1.0)] and measured[0]["t"] >= 30
    assert stats(deck["id"])["maturity"]["mature"] == 1
    # Suspended, leech and buried cards leave today's queue and are counted.
    with cards.db() as conn:
        new_cards = [conn.execute("SELECT id,revision FROM flash_cards WHERE note_id=? AND state='new'", (note["id"],)).fetchone() for note in notes]
    new_cards = [dict(card) for card in new_cards if card]
    cards.action(action="suspend", id=new_cards[0]["id"], revision=new_cards[0]["revision"])
    cards.action(action="bury", id=new_cards[1]["id"], revision=new_cards[1]["revision"])
    queue = stats(deck["id"])["queue"]
    assert (queue["new"], queue["suspended"], queue["buried"]) == (0, 1, 1)
    assert stats(deck["id"], "America/New_York")["forecast"][0]["day"] == "2026-12-13"
    cards.fails("/api/flashcards/stats?timezone=Mars/Base", match="timezone")


def test_card_detail_reports_rendered_faces_memory_state_and_parking(cards):
    note = cards.add()
    with cards.db() as conn:
        ident = conn.execute("SELECT id FROM flash_cards WHERE note_id=?", (note["id"],)).fetchone()[0]
    fresh = cards.ok(f"/api/flashcards/card?id={ident}")
    assert fresh["card"]["state"] == "new" and fresh["card"]["memory"] is None
    assert "synthetic-question" in fresh["card"]["front"] and "answer-only-secret" in fresh["card"]["back"]
    assert fresh["card"]["deck"] == "Default" and fresh["card"]["note_id"] == note["id"]
    card, _ = cards.review_once(4)
    cards.set_clock(cards.now + timedelta(days=3))
    detail = cards.ok(f"/api/flashcards/card?id={card['id']}")["card"]
    memory = detail["memory"]
    assert detail["state"] == "review" and detail["reviews"] == 1
    assert memory["stability"] > 3 and 1 <= memory["difficulty"] <= 10 and 0 < memory["retrievability"] < 1
    cards.action(action="bury", id=card["id"], revision=detail["revision"])
    assert cards.ok(f"/api/flashcards/card?id={card['id']}")["card"]["buried_until"] > cards.now.isoformat()
    cards.fails("/api/flashcards/card?id=999999", status=404, match="no longer exists")


def test_fronts_lists_each_note_first_field_in_exactly_that_deck(cards):
    default = cards.overview()["decks"][0]["id"]
    child = cards.deck(name="Default::Child", settings={})["id"]
    cards.add(fields={"Front": "Capital of <b>Peru</b>?", "Back": "Lima"})
    cards.add(kind="cloze")
    cards.add(fields={"Front": "In the subdeck", "Back": "only"}, deck=child)
    fronts = cards.ok(f"/api/flashcards/fronts?deck_id={default}")["fronts"]
    assert sorted(fronts) == sorted(["Capital of <b>Peru</b>?", "{{c1::hidden-one::hint}} + {{c2::hidden-two}} + {{c1::second-one}}"])
    assert cards.ok(f"/api/flashcards/fronts?deck_id={child}")["fronts"] == ["In the subdeck"]
    cards.fails("/api/flashcards/fronts", match="deck")
    cards.fails("/api/flashcards/fronts?deck_id=999999")


def test_mutations_are_recorded_for_the_brain_and_practice_is_not(cards):
    note = cards.add()
    cards.review_once(3)
    cards.action(id=note["id"], revision=cards.row("flash_notes", note["id"])["revision"], action="archive")
    with cards.db() as conn:
        events = [tuple(row) for row in conn.execute("SELECT section, entity, entity_id, action, route FROM record_events WHERE seq > ? ORDER BY seq", (cards.events_from,))]
    assert events == [("flashcards", "note", str(note["id"]), "create", "/api/flashcards/note"),
                      ("flashcards", "note", str(note["id"]), "archive", "/api/flashcards/action")]


def test_flashcard_routes_are_refused_without_the_workspace_module(tmp_path):
    from terminal_support import front_site
    from test_web_modules import make_root, refused

    settings = make_root(tmp_path, ("applyai",))
    with front_site(settings) as running:
        assert refused(*running.request("/api/flashcards"))
        assert refused(*running.request("/api/flashcards/deck", {"name": "x"}))


@pytest.mark.parametrize("kind,fields,tags", [
    ("basic", {"Front": "<b>Bold</b> & **strong** `code`", "Back": "back"}, ["z", "a", "a"]),
    ("reversed", {"Front": "front", "Back": ""}, []),
    ("cloze", {"Text": "{{c2::two}} and {{c1::one::hint}}", "Extra": ""}, ["tag"]),
    ("basic", {"Front": "", "Back": "only a back"}, []),
    ("basic", {"Front": "x"}, []),
    ("basic", {"Front": "x", "Back": "y"}, ["has space"]),
])
def test_rika_tools_python_note_save_agrees_with_the_route(cards, kind, fields, tags):
    """Rika's in-app tools still save notes in Python (kitty.core.flashcards.save_note)."""
    from kitty.core import flashcards

    state = cards.overview()
    typ = next(t for t in state["types"] if t["kind"] == kind)
    body = {"deck_id": state["decks"][0]["id"], "type_id": typ["id"], "fields": fields, "tags": tags}
    status, routed = cards.call("/api/flashcards/note", body)
    with cards.db() as conn:
        try:
            python = flashcards.save_note(conn, body)
        except ValueError as error:
            assert status == 400 and routed["error"] == str(error)
            return
        assert status == 200, routed
        routed = routed["note"]
        shape = lambda note: ({k: v for k, v in note.items() if k not in {"id", "guid", "created_at", "updated_at"}},
                              [(r["ordinal"], r["state"], r["revision"]) for r in conn.execute(
                                  "SELECT * FROM flash_cards WHERE note_id=? ORDER BY ordinal", (note["id"],))])
        assert shape(python) == shape(routed)
        pairs = conn.execute("SELECT p.ordinal, r.id FROM flash_cards p JOIN flash_cards r ON r.ordinal=p.ordinal "
                             "WHERE p.note_id=? AND r.note_id=?", (python["id"], routed["id"])).fetchall()
        expected = {ordinal: [flashcards.render(conn, {"note_id": python["id"], "ordinal": ordinal}, answer)
                              for answer in (False, True)] for ordinal, _ in pairs}
    for ordinal, card in pairs:
        detail = cards.ok(f"/api/flashcards/card?id={card}")["card"]
        assert [detail["front"], detail["back"]] == expected[ordinal]


TRICKY_HTML = [
    '<b>bold</b><i>it</i><u>u</u><s>s</s><br><hr/><p>para</p><ul><li>one</li></ul><table><tr><td>1</td></tr></table>',
    '<script>alert(1)</script>after<style>x{}</style><iframe src="x"></iframe><object>o</object>',
    '<SCRIPT>alert(1)</SCRIPT >tail</script><b>b</b>', '<img src="local.png" alt=x><img src=local.png><img src>',
    '<img src="missing.png"><img src=\'local%2Epng\'><img src="local.png" src="other.png">', '[sound:beep.wav] and [sound:none.mp3]',
    '&amp; &lt; &gt; &quot; &#39; &#x41; &#65 &copy &notin; &bogus; &#0; &#xD800; &', 'a < b > c & d', '<<b>>', '</>x</ b><//b>',
    '<!-- comment --><!--x--!><!doctype html><![CDATA[cdata]]><?pi?><!bogus>text', '<b title="a>b">quoted</b><b title=\'x\'>s</b>',
    '<div\nclass="x"\n>multi line</div>', '<xmp><b>raw</b></xmp><textarea>&amp;<b></textarea><title>t</title>',
    '**strong** `code` **un*closed', 'line\nbreak\n\n', '<b/>self<br/><span/>', '<a href="javascript:alert(1)">link</a>',
    'ünïcödé <b>ü</b> &eacute;', '<b', 'text <b class="', '<scr<script>ipt>x</script>', '<noscript><b>n</b></noscript>',
    '<plaintext><b>p</b>', 'x &am', 'x &amp', '<B>Upper</B><Img Src="local.png">',
]


def test_review_html_is_sanitized_exactly_as_the_python_parser_did(cards):
    """server/src/flashcards/html.rs against Python's html.parser via kitty.core.flashcards.safe_html."""
    import random

    from kitty.core import flashcards

    upload(cards, "local.png", b"\x89PNG\r\n\x1a\nsynthetic")
    upload(cards, "beep.wav", b"RIFF\0\0\0\0WAVEfmt synthetic")
    rng = random.Random(5)
    pieces = ["<b>", "</b>", "<i ", ">", "&amp;", "&", "<", "</", "x", " ", "\n", '"', "'", "=", "<img src=local.png>",
              "<script>", "</script>", "<!--", "-->", "**", "`", "[sound:beep.wav]", "<br/>", "é"]
    samples = TRICKY_HTML + ["".join(rng.choice(pieces) for _ in range(rng.randint(1, 30))) for _ in range(150)]
    typ = cards.ok("/api/flashcards/type", {"name": "Raw", "kind": "template", "fields": ["Front", "Back"],
                                            "templates": [{"name": "Raw", "front": "{{Back}}", "back": "{{Front}}"}]})["type"]
    deck = cards.overview()["decks"][0]["id"]
    for sample in samples:
        note = cards.ok("/api/flashcards/note", {"deck_id": deck, "type_id": typ["id"], "fields": {"Front": sample, "Back": "q"}})["note"]
        with cards.db() as conn:
            card = conn.execute("SELECT id FROM flash_cards WHERE note_id=?", (note["id"],)).fetchone()[0]
            try:
                expected = flashcards.safe_html(conn, sample)
            except TypeError as error:
                cards.fails(f"/api/flashcards/card?id={card}", match=str(error))
                continue
        assert cards.ok(f"/api/flashcards/card?id={card}")["card"]["back"] == expected, sample
