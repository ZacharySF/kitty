"""The block API (kitty.core.block_api): reads with pinned citations, writes as block transactions."""
from __future__ import annotations

import json

import pytest

from kitty.core import block_api, documents, notes, quick_notes, search
from kitty.core.blocks import BlockError, history, load_page, replay, tags


def _flat(blocks):
    for block in blocks:
        yield block
        yield from _flat(block["children"])


def _log(conn, seq):
    row = conn.execute("SELECT page_id, txn_id, ops FROM op_log WHERE seq=?", (seq,)).fetchone()
    return row["page_id"], row["txn_id"], json.loads(row["ops"])["ops"]


def test_a_page_is_created_read_as_a_tree_and_cited_at_its_sequence(workspace):
    conn = workspace.conn
    made = block_api.create_page(conn, "Cell biology", notebook="Biology", blocks=[
        {"type": "heading_1", "text": "Organelles"},
        {"type": "bulleted_list", "text": "Mitochondria make ATP", "children": [{"text": "Inner membrane folds"}]},
        {"type": "to_do", "text": "Read chapter 4", "props": {"checked": False}},
        "Plain string blocks are text",
    ])
    assert made["revision"] == 1 and made["number"] >= 1
    page_id, txn, _ops = _log(conn, made["seq"])
    assert page_id == made["page_id"] and txn == made["txn_id"]
    assert [item["cite"] for item in made["blocks"]] == [f"(({item['id']}@{made['seq']}))" for item in made["blocks"]]

    page = block_api.get_page(conn, made["page_id"])
    assert page["seq"] == made["seq"] and page["revision"] == 1
    assert page["page"]["title"] == "Cell biology" and page["page"]["notebook"] == "Biology"
    assert page["page"]["canvas_elements"] == 0
    assert [block["type"] for block in page["blocks"]] == ["heading_1", "bulleted_list", "to_do", "text"]
    nested = page["blocks"][1]
    assert nested["children"][0]["text"] == "Inner membrane folds"
    assert page["blocks"][2]["props"] == {"checked": False}
    assert all(block["cite"] == f"(({block['id']}@{page['seq']}))" for block in _flat(page["blocks"]))
    assert [block["order"] for block in page["blocks"]] == sorted(block["order"] for block in page["blocks"])
    assert block_api.get_page(conn, str(made["number"]))["page"]["id"] == made["page_id"]
    # The citation names a moment History can show again.
    preview = history.preview(conn, made["page_id"], made["seq"])
    assert any(block["text"] == "Mitochondria make ATP" for block in preview["blocks"])


def test_writes_insert_update_move_and_delete_through_transactions(workspace):
    conn = workspace.conn
    made = block_api.create_page(conn, "Plan", blocks=["one", "three"])
    one, three = (item["id"] for item in made["blocks"])
    two = block_api.insert_blocks(conn, ["two"], after=one, revision=1)
    assert two["revision"] == 2 and two["seq"] > made["seq"]
    assert [block["text"] for block in block_api.get_page(conn, made["page_id"])["blocks"]] == ["one", "two", "three"]

    first = block_api.insert_blocks(conn, ["zero"], before=one)
    appended = block_api.append_blocks(conn, made["page_id"], [{"text": "child"}], parent=three)
    updated = block_api.update_block(conn, three, text="three!", kind="to_do", props={"checked": True})
    assert updated["revision"] == 5
    page = block_api.get_page(conn, made["page_id"])
    assert [block["text"] for block in page["blocks"]] == ["zero", "one", "two", "three!"]
    assert page["blocks"][3]["type"] == "to_do" and page["blocks"][3]["props"] == {"checked": True}
    assert page["blocks"][3]["children"][0]["id"] == appended["blocks"][0]["id"]

    block_api.move_block(conn, first["blocks"][0]["id"], after=three)
    other = block_api.create_page(conn, "Elsewhere")
    moved = block_api.move_block(conn, two["blocks"][0]["id"], page=other["page_id"])
    assert set(moved["pages"]) == {made["page_id"], other["page_id"]} and moved["destination"] == other["page_id"]
    block_api.delete_block(conn, one)
    assert [block["text"] for block in block_api.get_page(conn, made["page_id"])["blocks"]] == ["three!", "zero"]
    assert [block["text"] for block in block_api.get_page(conn, other["page_id"])["blocks"]] == ["two"]
    assert any(item["id"] == one for item in history.trash(conn)["items"])
    block_api.update_block(conn, made["page_id"], text="Renamed plan")
    assert block_api.get_page(conn, made["page_id"])["page"]["title"] == "Renamed plan"
    # The log replays to the projection: every write was an ordinary transaction.
    for page_id in (made["page_id"], other["page_id"]):
        assert replay(conn, page_id).rows() == load_page(conn, page_id).rows()
    assert search.check(conn, repair=False)["ok"]


def test_a_stale_revision_is_refused_and_nothing_is_written(workspace):
    conn = workspace.conn
    made = block_api.create_page(conn, "Shared", blocks=["keep"])
    block_api.append_blocks(conn, made["page_id"], ["someone else typed"])
    before = conn.execute("SELECT MAX(seq) FROM op_log").fetchone()[0]
    with pytest.raises(BlockError) as refused:
        block_api.update_block(conn, made["blocks"][0]["id"], text="clobber", revision=1)
    assert refused.value.status == 409
    assert conn.execute("SELECT MAX(seq) FROM op_log").fetchone()[0] == before
    assert block_api.get_page(conn, made["page_id"])["blocks"][0]["text"] == "keep"


@pytest.mark.parametrize("call, message", [
    (lambda conn, ids: block_api.append_blocks(conn, ids["page"], [{"type": "canvas", "text": "x"}]), "Block type"),
    (lambda conn, ids: block_api.append_blocks(conn, ids["page"], [{"text": "x", "props": {"tag_" + "0" * 32: "manual"}}]), "properties"),
    (lambda conn, ids: block_api.append_blocks(conn, ids["page"], [{"text": "x", "props": {"checked": "yes"}}]), "checked"),
    (lambda conn, ids: block_api.append_blocks(conn, ids["document"], ["x"]), "Only note pages"),
    (lambda conn, ids: block_api.append_blocks(conn, ids["quick"], ["x"]), "Only note pages"),
    (lambda conn, ids: block_api.insert_blocks(conn, ["x"], parent=ids["canvas"]), "canvas"),
    (lambda conn, ids: block_api.update_block(conn, ids["page"], kind="text"), "title"),
    (lambda conn, ids: block_api.delete_block(conn, ids["page"]), "Delete pages"),
    (lambda conn, ids: block_api.move_block(conn, ids["page"], page=ids["page"]), "cannot be moved"),
    (lambda conn, ids: block_api.insert_blocks(conn, ["x"]), "Say where"),
    (lambda conn, ids: block_api.get_page(conn, "not a page"), "block ID"),
])
def test_writes_reach_only_text_blocks_of_note_pages(workspace, call, message):
    conn = workspace.conn
    made = block_api.create_page(conn, "Target")
    book = notes.create_notebook(conn, "Docs")["id"]
    document = documents.save(conn, {"title": "Doc", "content": "# Heading\n\nBody\n", "format": "markdown", "notebook_id": book})
    doc_id = conn.execute("SELECT id FROM blocks WHERE type='document' AND parent_id IS NULL "
                          "AND json_extract(properties,'$.number')=?", (document["id"],)).fetchone()[0]
    quick = block_api.quick_note(conn, "scratch")
    canvas = conn.execute("SELECT id FROM blocks WHERE page_id=? AND type='canvas'", (made["page_id"],)).fetchone()[0]
    ids = {"page": made["page_id"], "document": doc_id, "quick": quick["page_id"], "canvas": canvas}
    with pytest.raises(BlockError, match=message):
        call(conn, ids)


def test_search_finds_blocks_and_backlinks_cover_references_links_mentions_and_tags(workspace):
    conn = workspace.conn
    target = block_api.create_page(conn, "Krebs cycle", blocks=["Citric acid is oxidised"])
    fact = target["blocks"][0]["id"]
    other = block_api.create_page(conn, "Review", blocks=[
        f"As cited in (({fact}@{target['seq']}))",
        "See [[Krebs cycle]] for detail",
        "The krebs cycle runs in the matrix",
    ])
    cited, linked, mentioned = (item["id"] for item in other["blocks"])

    found = block_api.search_blocks(conn, "citric")
    assert [hit["block_id"] for hit in found["hits"]] == [fact]
    hit = found["hits"][0]
    assert hit["page_title"] == "Krebs cycle" and "**Citric**" in hit["snippet"] and hit["cite"] == f"(({fact}@{found['seq']}))"
    assert block_api.search_blocks(conn, "krebs")["total"] >= 3

    of_block = block_api.backlinks(conn, fact)
    assert [item["block_id"] for item in of_block["references"]["blocks"]] == [cited]
    of_page = block_api.backlinks(conn, target["page_id"])
    assert [item["block_id"] for item in of_page["references"]["blocks"]] == [cited]
    assert [item["block_id"] for item in of_page["links"]["blocks"]] == [linked]
    assert [item["block_id"] for item in of_page["mentions"]["blocks"]] == [mentioned]
    assert of_page["target"]["cite"] == f"(({target['page_id']}@{of_page['seq']}))"

    tagged = block_api.tag_block(conn, mentioned, "#metabolism", create=True)
    assert tagged["revision"] == other["revision"]  # tagging is not an edit
    listed = block_api.list_tags(conn)
    assert [tag["name"] for tag in listed["tags"]] == ["metabolism"] and listed["tags"][0]["count"] == 1
    assert block_api.get_page(conn, other["page_id"])["blocks"][2]["tags"] == ["metabolism"]
    assert [item["block_id"] for item in block_api.backlinks(conn, tagged["tag"])["tagged"]["blocks"]] == [mentioned]
    with pytest.raises(BlockError, match="No tag"):
        block_api.tag_block(conn, linked, "unknown")
    assert tags.find(conn, "metabolism") == tagged["tag"]


def test_listing_inbox_and_quick_notes(workspace):
    conn = workspace.conn
    block_api.create_page(conn, "Alpha notes")
    inbox = block_api.add_to_inbox(conn, ["captured idea", {"type": "quote", "text": "quoted"}])
    again = block_api.add_to_inbox(conn, ["second"])
    assert again["page_id"] == inbox["page_id"] and again["revision"] == 2
    texts = [block["text"] for block in block_api.get_page(conn, inbox["page_id"])["blocks"]]
    assert texts == ["captured idea", "quoted", "second"]
    note = block_api.quick_note(conn, "Call the lab\nabout samples")
    assert quick_notes.get(conn, note["number"])["body"].startswith("Call the lab")

    listed = block_api.list_pages(conn)
    assert {page["title"] for page in listed["pages"]} >= {"Alpha notes", "Inbox", "Call the lab"}
    assert [page["title"] for page in block_api.list_pages(conn, "alpha")["pages"]] == ["Alpha notes"]
    assert [page["type"] for page in block_api.list_pages(conn, kind="quick_note")["pages"]] == ["quick_note"]
    assert next(page for page in listed["pages"] if page["title"] == "Inbox")["inbox"] is True
    limited = block_api.list_pages(conn, limit=1)
    assert len(limited["pages"]) == 1 and limited["truncated"] and limited["total"] == listed["total"]


def test_pinned_citations_count_as_block_references(workspace):
    from kitty.core.blocks import references

    bid = "0b0b0b0b-0000-4000-8000-000000000001"
    assert references(f"see (({bid}@42)) and (({bid}))")[0] == {bid}
