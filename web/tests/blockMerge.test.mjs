// A window applying a merge from another device (the server's `put` operations).
// Run with Node >=22.6: node --test tests/blockMerge.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { PageDoc, visibleRows } from "../src/lib/blockDoc.ts";

const PAGE = "00000000-0000-4000-8000-000000000000";
const id = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const block = (n, text, key, parent = PAGE) => ({ id: id(n), type: "text", parent_id: parent, order_key: key, properties: {}, text });

function doc() {
  return PageDoc.from({
    page_id: PAGE, seq: 4, revision: 2,
    root: { id: PAGE, type: "page", parent_id: null, order_key: "a0", properties: { revision: 2 }, text: "Page" },
    blocks: [block(1, "one", "a0"), block(2, "two", "a1"), block(3, "child", "a0", id(2))],
  });
}
const put = (n, fields) => ({ op: "put", id: id(n), type: "text", parent: PAGE, key: "a0", props: {}, deleted: null, ...fields });
const texts = (page) => visibleRows(page).map((row) => row.block.text);

test("a merged block moves, changes and keeps its text unless the put carries one", () => {
  const page = doc();
  assert.equal(page.apply(put(3, { parent: PAGE, key: "a2", props: { checked: true }, type: "to_do" }), false), false);
  assert.deepEqual(texts(page), ["one", "two", "child"]);
  assert.equal(page.get(id(3)).type, "to_do");
  assert.deepEqual(page.get(id(3)).properties, { checked: true });
  page.apply(put(1, { key: "a3", text: "one, merged" }), false);
  assert.deepEqual(texts(page), ["two", "child", "one, merged"]);
});

test("a new block arrives with its text; one sent to Trash leaves the page", () => {
  const page = doc();
  page.apply(put(9, { key: "a0V", text: "from the laptop" }), false);
  assert.deepEqual(texts(page), ["one", "from the laptop", "two", "child"]);
  page.apply(put(2, { key: "a1", deleted: "2026-11-01T10:00:00+00:00" }), false);
  assert.deepEqual(texts(page), ["one", "from the laptop"]);
});

test("a block the window never held without its text means reload; the root keeps its place", () => {
  const page = doc();
  assert.equal(page.apply(put(7, { key: "a5" }), false), true);
  page.apply({ op: "put", id: PAGE, type: "page", parent: null, key: "a0", props: { revision: 3 }, deleted: null }, false);
  assert.equal(page.revision, 3);
  assert.throws(() => page.apply(put(1, { text: "x" })), /server/);
});
