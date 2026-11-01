// Auto-tags in the block editor (platform design Q13): tag states in block properties, which
// suggestions show, Tab/Esc in the keyboard model, the sync engine taking the server's tag
// operations and suggestions from its ack, and the Tags settings' term parsing.
// Run with Node >=22.6: node --test tests/blockTags.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { keyBetween } from "../src/lib/blockKeys.ts";
import { PageDoc, viewOnly } from "../src/lib/blockDoc.ts";
import { keyCommand } from "../src/lib/blockKeymap.ts";
import { PageSync } from "../src/lib/blockSync.ts";
import {
  acceptOps, attachedTags, liveSuggestions, mergeSuggestions, rejectOps, suggestionsAt, tagKey, tagOfKey, tagStates, unnamedTags,
} from "../src/lib/blockTags.ts";
import { parseTerms, shortTerms, termsText } from "../src/lib/tags.ts";

const PAGE = "00000000-0000-4000-8000-000000000000";
const BLOCK = "00000000-0000-4000-8000-000000000001";
const PY = "11111111-2222-4333-8444-555555555555";
const RUST = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const key = (tag) => `tag_${tag.replaceAll("-", "")}`;

function state(text, props = {}, revision = 3) {
  return {
    page_id: PAGE, seq: 10, revision,
    root: { id: PAGE, type: "page", parent_id: null, order_key: "a0", properties: { revision, number: 1 }, text: "Synthetic page" },
    blocks: [{ id: BLOCK, type: "text", parent_id: PAGE, order_key: keyBetween(null, null), properties: props, text }],
  };
}

test("a block's tag states live in tag_<id> properties", () => {
  assert.equal(tagKey(PY), key(PY));
  assert.equal(tagOfKey(key(PY)), PY);
  assert.equal(tagOfKey("marks"), null);
  assert.equal(tagOfKey("tag_short"), null);
  const block = { properties: { marks: [], [key(PY)]: "accepted", [key(RUST)]: "rejected" } };
  assert.deepEqual(tagStates(block), { [PY]: "accepted", [RUST]: "rejected" });
  assert.deepEqual(attachedTags(block, { [PY]: "Python", [RUST]: "Rust" }), [{ tag: PY, name: "Python", state: "accepted" }]);
  assert.deepEqual(attachedTags(block, {}), [], "a tag with no known name (in Trash, say) shows no chip");
  assert.deepEqual(unnamedTags([block], { [PY]: "Python" }), [RUST]);
  assert.deepEqual(acceptOps(BLOCK, PY), [{ op: "set", id: BLOCK, props: { [key(PY)]: "accepted" } }]);
  assert.deepEqual(rejectOps(BLOCK, PY), [{ op: "set", id: BLOCK, props: { [key(PY)]: "rejected" } }]);
});

test("suggestions show only for the text they were computed for and for tags the block has no state for", () => {
  const entry = { text: "pandas and python", items: [{ tag: PY, name: "Python", start: 0, end: 6 }, { tag: RUST, name: "Rust", start: 11, end: 17 }] };
  assert.equal(liveSuggestions({ text: "pandas and python", properties: {} }, entry).length, 2);
  assert.deepEqual(liveSuggestions({ text: "pandas and pythons", properties: {} }, entry), [], "typing on hides them until the next answer");
  assert.deepEqual(liveSuggestions({ text: "pandas and python", properties: { [key(PY)]: "rejected" } }, entry).map((item) => item.tag), [RUST]);
  assert.deepEqual(liveSuggestions({ text: "x", properties: {} }, undefined), []);
  const items = entry.items;
  assert.deepEqual(suggestionsAt(items, 3).map((item) => item.tag), [PY]);
  assert.deepEqual(suggestionsAt(items, 6).map((item) => item.tag), [PY], "the caret right after the word counts");
  assert.deepEqual(suggestionsAt(items, 8), []);
  assert.deepEqual(suggestionsAt(items, 2, 4), [], "a selection is not a caret on a suggestion");
  const merged = mergeSuggestions({ [BLOCK]: entry, other: entry }, { [BLOCK]: { text: "pandas", items: [] } });
  assert.deepEqual(Object.keys(merged), ["other"], "a block re-checked with no matches loses its suggestions");
  assert.deepEqual(mergeSuggestions(merged, undefined), merged);
});

test("Tab attaches and Esc ignores only while the caret is on a suggestion", () => {
  const press = (key, extra = {}) => ({ key, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, isComposing: false, ...extra });
  const text = { mode: "text", atStart: false, atEnd: true, collapsed: true, firstLine: true, lastLine: true, type: "text" };
  assert.equal(keyCommand(press("Tab"), { ...text, suggestion: true }), "acceptTag");
  assert.equal(keyCommand(press("Escape"), { ...text, suggestion: true }), "rejectTag");
  assert.equal(keyCommand(press("Tab"), text), "indent");
  assert.equal(keyCommand(press("Escape"), text), "selectBlock");
  assert.equal(keyCommand(press("Tab", { shiftKey: true }), { ...text, suggestion: true }), "outdent");
  assert.equal(keyCommand(press("Tab"), { ...text, suggestion: true, collapsed: false }), "indent");
});

test("tag states are not edits: other windows' tagging applies under a confirmed transaction", () => {
  assert.equal(viewOnly([{ op: "set", id: BLOCK, props: { [key(PY)]: "auto" } }]), true);
  assert.equal(viewOnly([{ op: "set", id: BLOCK, props: { [key(PY)]: "auto", checked: true } }]), false);
});

test("the sync engine applies the tags the server attached with an edit and passes its suggestions on", async () => {
  const replies = [];
  const acks = [];
  const sync = new PageSync({
    state: state("cargo"),
    send: (message) => new Promise((resolve) => replies.push({ message, resolve })),
    load: async () => state("cargo"),
    onupdate: () => {},
    onack: (ack) => acks.push(ack),
  });
  sync.commit([{ op: "text", id: BLOCK, text: "cargo build" }]);
  const { message, resolve } = replies.shift();
  assert.deepEqual(message.ops, [{ op: "text", id: BLOCK, at: 5, del: 0, ins: " build" }]);
  const suggestions = { [BLOCK]: { text: "cargo build", items: [{ tag: PY, name: "Python", start: 6, end: 11 }] } };
  resolve({ type: "ack", txn_id: message.txn_id, seq: { [PAGE]: 11 }, revisions: { [PAGE]: 4 },
    tagged: { [PAGE]: [{ op: "set", id: BLOCK, props: { [key(RUST)]: "auto" } }] }, suggestions, tags: { [RUST]: "Rust", [PY]: "Python" } });
  await new Promise((done) => setTimeout(done, 0));
  assert.equal(sync.server.get(BLOCK).properties[key(RUST)], "auto");
  assert.equal(sync.local.get(BLOCK).properties[key(RUST)], "auto", "the chip shows without a reload");
  assert.equal(sync.server.revision, 4);
  assert.deepEqual(acks.map((ack) => ack.suggestions), [suggestions]);
  // Accepting a suggestion is one set operation; the server answers without a revision bump.
  sync.commit(acceptOps(BLOCK, PY));
  const accept = replies.shift();
  assert.deepEqual(accept.message.ops, [{ op: "set", id: BLOCK, props: { [key(PY)]: "accepted" } }]);
  assert.deepEqual(accept.message.base, { [PAGE]: 4 });
  accept.resolve({ type: "ack", txn_id: accept.message.txn_id, seq: { [PAGE]: 12 }, revisions: { [PAGE]: 4 }, suggestions: { [BLOCK]: { text: "cargo build", items: [] } } });
  await new Promise((done) => setTimeout(done, 0));
  assert.equal(sync.local.get(BLOCK).properties[key(PY)], "accepted");
  assert.equal(sync.pending, false);
  sync.dispose();
});

test("the sync engine keeps a remote tag change that was committed before its own transaction", async () => {
  const replies = [];
  const sync = new PageSync({
    state: state("rust"), send: (message) => new Promise((resolve) => replies.push({ message, resolve })),
    load: async () => state("rust"), onupdate: () => {},
  });
  sync.commit([{ op: "text", id: BLOCK, text: "rust!" }]);
  // Another window tagged the block (no revision bump) just before this window's edit landed.
  sync.receive({ seq: 11, txn_id: "remote", page_id: PAGE, ops: [{ op: "set", id: BLOCK, props: { [key(RUST)]: "accepted" } }] });
  const { message, resolve } = replies.shift();
  resolve({ type: "ack", txn_id: message.txn_id, seq: { [PAGE]: 12 }, revisions: { [PAGE]: 4 } });
  await new Promise((done) => setTimeout(done, 0));
  assert.equal(sync.local.get(BLOCK).properties[key(RUST)], "accepted");
  assert.equal(sync.local.get(BLOCK).text, "rust!");
  sync.dispose();
});

test("Tags settings parse comma-separated terms and flag those too short to match", () => {
  assert.deepEqual(parseTerms(" pandas, NumPy ,numpy,\nmachine   learning,, "), ["pandas", "NumPy", "machine learning"]);
  assert.equal(termsText(["a", "b"]), "a, b");
  assert.deepEqual(shortTerms(["AI", "ML ops", "go"], false), ["AI", "go"]);
  assert.deepEqual(shortTerms(["AI"], true), []);
});

test("PageDoc applies tag state sets like any property (null removes)", () => {
  const doc = PageDoc.from(state("x", { [key(PY)]: "auto" }));
  doc.apply({ op: "set", id: BLOCK, props: { [key(PY)]: null } });
  assert.deepEqual(doc.get(BLOCK).properties, {});
});
