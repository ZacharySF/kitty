// The Mirror pane's pure logic (src/lib/mirror.ts): live frames, paragraphs, and the quote
// a passage becomes in the note.
// Run with Node >=22.6: node --test tests/mirror.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { PageDoc, visibleRows } from "../src/lib/blockDoc.ts";
import { age, applyFrame, cleanPassage, endMirror, fromListing, imageUrl, MAX_QUOTE, paragraphs, quoteEdit, quoteSpecs, stamp } from "../src/lib/mirror.ts";

const frame = (fields = {}) => ({
  type: "mirror", id: "m1", client: { id: "c", name: "kitty for Chromium" }, tab: "7", url: "https://example.com/a", title: "A page",
  selection: "", frame: 1, text_rev: 1, text: "One.\n\nTwo.", at: "2026-11-02T10:00:00+00:00", started_at: "2026-11-02T10:00:00+00:00",
  image: { frame: 1, type: "image/jpeg", width: 10, height: 10, bytes: 100 }, expires_in: 150, ...fields,
});

test("frames keep the last text, newest mirror first, and a missed first frame asks for a reload", () => {
  let { list, stale } = applyFrame([], frame());
  assert.equal(stale, false);
  assert.equal(list[0].text, "One.\n\nTwo.");
  const second = { ...frame({ frame: 2, selection: "Two." }) };
  delete second.text;
  ({ list, stale } = applyFrame(list, second));
  assert.equal(stale, false);
  assert.equal(list[0].text, "One.\n\nTwo.");
  assert.equal(list[0].selection, "Two.");
  ({ list } = applyFrame(list, frame({ id: "m2", tab: "8" })));
  assert.deepEqual(list.map((item) => item.id), ["m2", "m1"]);
  const unseen = frame({ id: "m3", text_rev: 4 });
  delete unseen.text;
  assert.equal(applyFrame(list, unseen).stale, true);
  const changed = frame({ text_rev: 2 });
  delete changed.text;
  assert.equal(applyFrame(list, changed).stale, true, "a text revision this window never received");
  assert.deepEqual(endMirror(list, "m2").map((item) => item.id), ["m1"]);
  assert.equal(fromListing({ mirrors: [frame()], retention: {} })[0].text, "One.\n\nTwo.");
});

test("the screenshot URL names the frame it shows", () => {
  const [mirror] = applyFrame([], frame({ image: { frame: 3, type: "image/png", width: 1, height: 1, bytes: 1 } })).list;
  assert.equal(imageUrl(mirror), "/api/mirror/m1/image?frame=3");
  assert.equal(imageUrl({ ...mirror, image: null }), null);
});

test("paragraphs, ages and passages", () => {
  assert.deepEqual(paragraphs("  One.\n\n\n Two \n  \nThree."), ["One.", "Two", "Three."]);
  assert.deepEqual(paragraphs(""), []);
  const now = Date.parse("2026-11-02T10:05:00Z");
  assert.equal(age("2026-11-02T10:04:58Z", now), "just now");
  assert.equal(age("2026-11-02T10:04:30Z", now), "30 s ago");
  assert.equal(age("2026-11-02T10:01:00Z", now), "4 min ago");
  assert.equal(cleanPassage("  a  \r\n\r\n\r\n\r\nb "), "a\n\nb");
  const long = cleanPassage("x".repeat(MAX_QUOTE + 10));
  assert.equal(long.length, MAX_QUOTE + 1);
  assert.ok(long.endsWith("…"));
  const emoji = cleanPassage("x".repeat(MAX_QUOTE - 1) + "😀" + "y");
  assert.ok(!/[\uD800-\uDBFF]…$/.test(emoji), "no half surrogate before the ellipsis");
});

test("a quote is a callout with the linked title and time, holding the passage", () => {
  const at = new Date(2026, 10, 2, 9, 7);
  const specs = quoteSpecs({ passage: "  The passage.  ", title: "A page", url: "https://example.com/a", at });
  assert.equal(stamp(at), "2026-11-02 09:07");
  assert.deepEqual(specs, [
    { type: "callout", text: "A page · 2026-11-02 09:07", depth: 0,
      props: { source_url: "https://example.com/a", marks: [{ s: 0, e: 6, t: "a", href: "https://example.com/a" }], quoted_at: at.toISOString() } },
    { type: "quote", text: "The passage.", props: {}, depth: 1 },
  ]);
  const unsafe = quoteSpecs({ passage: "x", title: "", url: "javascript:alert(1)", at });
  assert.equal(unsafe[0].text.startsWith("javascript:alert(1) · "), true);
  assert.equal(unsafe[0].props.marks, undefined);
  assert.equal(unsafe[0].props.source_url, undefined);
});

const page = (children) => PageDoc.from({
  page_id: "p", seq: 1, revision: 1,
  root: { id: "p", parent_id: null, type: "page", order_key: "a0", text: "Page", properties: { revision: 1 } },
  blocks: children.map(([id, key, type = "text", parent = "p"]) => ({ id, parent_id: parent, type, order_key: key, text: id, properties: {} })),
});
const ids = () => { let n = 0; return () => `n${++n}`; };

function apply(doc, edit) {
  for (const op of edit.ops) doc.apply(op);
  return visibleRows(doc).map((row) => [row.block.id, row.depth, row.block.type]);
}

test("a quote goes after the block the caret was in", () => {
  const doc = page([["a", "a0"], ["b", "a1"], ["c", "a2"]]);
  const specs = quoteSpecs({ passage: "P", title: "T", url: "https://e.com/", at: new Date() });
  const edit = quoteEdit(doc, "a", specs, ids());
  assert.deepEqual(apply(doc, edit), [["a", 0, "text"], ["n1", 0, "callout"], ["n2", 1, "quote"], ["b", 0, "text"], ["c", 0, "text"]]);
  assert.deepEqual(edit.focus, { id: "n2", offset: 1 });
});

test("without a caret block a quote goes to the end of the page, even an empty one", () => {
  const specs = quoteSpecs({ passage: "P", title: "T", url: "https://e.com/", at: new Date() });
  const doc = page([["a", "a0"], ["b", "a1"]]);
  assert.deepEqual(apply(doc, quoteEdit(doc, null, specs, ids())).map((row) => row[0]), ["a", "b", "n1", "n2"]);
  const gone = page([["a", "a0"]]);
  assert.deepEqual(apply(gone, quoteEdit(gone, "deleted-block", specs, ids())).map((row) => row[0]), ["a", "n1", "n2"]);
  const empty = page([]);
  assert.deepEqual(apply(empty, quoteEdit(empty, null, specs, ids())), [["n1", 0, "callout"], ["n2", 1, "quote"]]);
  // The caret in a nested block: the quote follows its top-level block, children and all.
  const nested = page([["a", "a0"], ["x", "a0", "text", "a"], ["b", "a1"]]);
  assert.deepEqual(apply(nested, quoteEdit(nested, "x", specs, ids())).map((row) => row.slice(0, 2)),
    [["a", 0], ["x", 1], ["n1", 0], ["n2", 1], ["b", 0]]);
  // A canvas element last on the page: the quote still lands after it.
  const canvas = page([["a", "a0"], ["k", "a1", "canvas"]]);
  const edit = quoteEdit(canvas, null, specs, ids());
  assert.ok(edit.ops[0].key > "a1");
  assert.deepEqual(apply(canvas, edit).map((row) => row[0]), ["a", "n1", "n2"]);
});
