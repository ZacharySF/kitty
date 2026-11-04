// Pure block-editor logic: order keys, the client page document, edit transactions,
// inline marks and Markdown, the keyboard model and the transaction sync engine.
// Run with Node >=22.6: node --test tests/blockEditor.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { keyBetween, keysBetween } from "../src/lib/blockKeys.ts";
import { PageDoc, visibleRows, codePointSplice } from "../src/lib/blockDoc.ts";
import {
  dropBlocks, deleteBlocks, indent, insertSpecs, mergeBackward, mergeForward, moveBlocks, outdent, split, turnInto,
} from "../src/lib/blockEdits.ts";
import {
  blocksMarkdown, marksHTML, parseInline, parseMarkdown, shortcut, sliceMarks, concatMarks, toggleMark, inlineMarkdown,
} from "../src/lib/blockText.ts";
import { keyCommand } from "../src/lib/blockKeymap.ts";
import { PageSync } from "../src/lib/blockSync.ts";

const PAGE = "00000000-0000-4000-8000-000000000000";
const id = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
let counter = 100;
const fresh = () => id(counter++);

function state(items, { revision = 3, seq = 10 } = {}) {
  const keys = new Map();
  const blocks = items.map(([n, type, text, parent = 0, props = {}]) => {
    const parentId = parent ? id(parent) : PAGE;
    const key = keyBetween(keys.get(parentId) ?? null, null);
    keys.set(parentId, key);
    return { id: id(n), type, parent_id: parentId, order_key: key, properties: props, text };
  });
  return {
    page_id: PAGE, seq, revision,
    root: { id: PAGE, type: "page", parent_id: null, order_key: "a0", properties: { revision, number: 1 }, text: "Synthetic page" },
    blocks,
  };
}
const outline = (doc) => visibleRows(doc).map((row) => `${"  ".repeat(row.depth)}${row.block.type}:${row.block.text}`);
function applied(doc, result) {
  const next = doc.clone();
  for (const op of result.ops) next.apply(op);
  return next;
}

test("order keys match kitty's Python fractional indexing", () => {
  assert.equal(keyBetween(null, null), "a0");
  assert.equal(keyBetween("a0", null), "a1");
  assert.equal(keyBetween(null, "a0"), "Zz");
  assert.equal(keyBetween("a0", "a1"), "a0V");
  assert.equal(keyBetween("a0", "a0V"), "a0G");
  assert.equal(keyBetween("Zz", null), "a0");
  assert.equal(keyBetween("a0V", "a1"), "a0l");
  assert.equal(keyBetween(null, "Zz"), "Zy");
  assert.equal(keyBetween("z".repeat(28), null), `${"z".repeat(28)}V`);
  assert.deepEqual(keysBetween("a0", "a1", 5), ["a08", "a0G", "a0V", "a0d", "a0l"]);
  assert.deepEqual(keysBetween(null, null, 4), ["a0", "a1", "a2", "a3"]);
  assert.deepEqual(keysBetween(null, "a0", 3), ["Zx", "Zy", "Zz"]);
  assert.throws(() => keyBetween("a1", "a0"));
  let low = "a0", high = "a1";
  for (let i = 0; i < 200; i += 1) {
    const middle = keyBetween(low, high);
    assert.ok(low < middle && middle < high);
    if (i % 2) low = middle; else high = middle;
  }
});

test("the client page applies every server operation, splices in code points and ignores canvas content", () => {
  const doc = PageDoc.from(state([[1, "text", "héllo 🙂 world"], [2, "canvas", ""], [3, "text", "child", 1]]));
  doc.apply({ op: "text", id: id(1), at: 6, del: 1, ins: "😀" });
  assert.equal(doc.get(id(1)).text, "héllo 😀 world");
  doc.apply({ op: "set", id: id(1), type: "heading_1", props: { color: "red" } });
  doc.apply({ op: "set", id: id(1), props: { color: null } });
  assert.equal(doc.get(id(1)).type, "heading_1");
  assert.deepEqual(doc.get(id(1)).properties, {});
  // Canvas elements belong to the canvas UI; remote ops about them are ignored.
  assert.equal(doc.apply({ op: "insert", id: id(9), type: "sticky", parent: id(2), key: "a0" }, false), false);
  assert.equal(doc.has(id(9)), false);
  assert.equal(doc.apply({ op: "set", id: id(8), props: { x: 1 } }, false), false);
  doc.apply({ op: "move", id: id(3), parent: PAGE, key: keyBetween(null, doc.get(id(1)).order_key) });
  assert.deepEqual(outline(doc), ["text:child", "heading_1:héllo 😀 world"]);
  doc.apply({ op: "delete", id: id(1) });
  assert.equal(doc.has(id(1)), false);
  // A restore of something this window never held needs the page reloaded.
  assert.equal(doc.apply({ op: "restore", id: id(1) }, false), true);
  doc.apply({ op: "set", id: PAGE, props: { revision: 4 } });
  assert.equal(doc.revision, 4);
  assert.throws(() => doc.apply({ op: "text", id: id(42), text: "x" }));
  assert.deepEqual(codePointSplice("a🙂b", "a🙂xb"), { at: 2, del: 0, ins: "x" });
  assert.deepEqual(codePointSplice("same", "same"), null);
  assert.deepEqual(codePointSplice("abcdef", "abXYef"), { at: 2, del: 2, ins: "XY" });
});

test("rows follow reading order, hide collapsed children and number list runs", () => {
  const doc = PageDoc.from(state([
    [1, "numbered_list", "one"], [2, "numbered_list", "two"], [3, "toggle", "fold", 0, { collapsed: true }],
    [4, "text", "hidden", 3], [5, "numbered_list", "again"], [6, "canvas", ""],
  ]));
  const rows = visibleRows(doc);
  assert.deepEqual(rows.map((row) => [row.block.text, row.number ?? null, row.hasChildren]),
    [["one", 1, false], ["two", 2, false], ["fold", null, true], ["again", 1, false]]);
});

test("Enter splits at the caret, keeps the id with the text before it and continues lists", () => {
  const doc = PageDoc.from(state([[1, "bulleted_list", "hello world", 0, { marks: [{ s: 0, e: 11, t: "b" }] }], [2, "text", "next"]]));
  const result = split(doc, id(1), 5, 6, fresh);
  const next = applied(doc, result);
  assert.deepEqual(outline(next), ["bulleted_list:hello", "bulleted_list:world", "text:next"]);
  assert.deepEqual(next.get(id(1)).properties.marks, [{ s: 0, e: 5, t: "b" }]);
  assert.deepEqual(next.get(result.focus.id).properties.marks, [{ s: 0, e: 5, t: "b" }]);
  assert.equal(result.focus.offset, 0);
  // At the start of a non-empty block, an empty block goes above and the caret stays.
  const above = split(doc, id(2), 0, 0, fresh);
  assert.deepEqual(outline(applied(doc, above)), ["bulleted_list:hello world", "text:", "text:next"]);
  assert.deepEqual(above.focus, { id: id(2), offset: 0 });
  // Enter on an empty list item leaves the list.
  const empty = PageDoc.from(state([[1, "to_do", "", 0, { checked: true }]]));
  assert.deepEqual(applied(empty, split(empty, id(1), 0, 0, fresh)).get(id(1)).type, "text");
  // Headings continue as text; a block with open children gets the new block as its first child.
  const parent = PageDoc.from(state([[1, "heading_2", "Title"], [2, "text", "child", 1]]));
  const nested = applied(parent, split(parent, id(1), 5, 5, fresh));
  assert.deepEqual(outline(nested), ["heading_2:Title", "  text:", "  text:child"]);
});

test("Backspace at the start turns a typed block into text, then merges into the block above", () => {
  const doc = PageDoc.from(state([[1, "text", "first", 0, { marks: [{ s: 0, e: 5, t: "i" }] }], [2, "quote", "second", 0, { marks: [{ s: 0, e: 6, t: "b" }] }], [3, "text", "kid", 2]]));
  const toText = mergeBackward(doc, id(2));
  const plain = applied(doc, toText);
  assert.equal(plain.get(id(2)).type, "text");
  const merged = mergeBackward(plain, id(2));
  const result = applied(plain, merged);
  assert.deepEqual(outline(result), ["text:firstsecond", "  text:kid"]);
  assert.deepEqual(result.get(id(1)).properties.marks, [{ s: 0, e: 5, t: "i" }, { s: 5, e: 11, t: "b" }]);
  assert.deepEqual(merged.focus, { id: id(1), offset: 5 });
  assert.ok(merged.ops.some((op) => op.op === "delete" && op.id === id(2)), "the merged block goes to Trash");
  const divider = PageDoc.from(state([[1, "divider", ""], [2, "text", "after"]]));
  const removed = applied(divider, mergeBackward(divider, id(2)));
  assert.deepEqual(outline(removed), ["text:after"]);
  const forward = applied(doc, mergeForward(doc, id(1)));
  assert.equal(forward.get(id(1)).text, "firstsecond");
});

test("Tab nests under the previous sibling and Shift+Tab un-nests keeping reading order", () => {
  const doc = PageDoc.from(state([[1, "text", "a"], [2, "text", "b"], [3, "text", "c"], [4, "text", "d"]]));
  const nested = applied(doc, indent(doc, [id(2), id(3)]));
  assert.deepEqual(outline(nested), ["text:a", "  text:b", "  text:c", "text:d"]);
  assert.equal(indent(doc, [id(1)]).ops.length, 0, "the first block has nothing to nest under");
  const out = applied(nested, outdent(nested, [id(2)]));
  assert.deepEqual(outline(out), ["text:a", "text:b", "  text:c", "text:d"]);
  const collapsed = PageDoc.from(state([[1, "toggle", "t", 0, { collapsed: true }], [2, "text", "x"]]));
  const opened = applied(collapsed, indent(collapsed, [id(2)]));
  assert.equal(opened.get(id(1)).properties.collapsed, false);
});

test("Ctrl+Shift+arrows move selected blocks past their neighbours and out of their parent", () => {
  const doc = PageDoc.from(state([[1, "text", "a"], [2, "text", "b"], [3, "text", "c"], [4, "text", "kid", 3]]));
  const up = applied(doc, moveBlocks(doc, [id(2), id(3)], -1));
  assert.deepEqual(outline(up), ["text:b", "text:c", "  text:kid", "text:a"]);
  const down = applied(doc, moveBlocks(doc, [id(1)], 1));
  assert.deepEqual(outline(down), ["text:b", "text:a", "text:c", "  text:kid"]);
  const outOfParent = applied(doc, moveBlocks(doc, [id(4)], -1));
  assert.deepEqual(outline(outOfParent), ["text:a", "text:b", "text:kid", "text:c"]);
});

test("dragging blocks drops them before, after or inside a target, never inside themselves", () => {
  const doc = PageDoc.from(state([[1, "text", "a"], [2, "text", "b"], [3, "text", "c"], [4, "text", "kid", 3]]));
  assert.deepEqual(outline(applied(doc, dropBlocks(doc, [id(1)], id(3), "after"))), ["text:b", "text:c", "  text:kid", "text:a"]);
  assert.deepEqual(outline(applied(doc, dropBlocks(doc, [id(3)], id(1), "before"))), ["text:c", "  text:kid", "text:a", "text:b"]);
  assert.deepEqual(outline(applied(doc, dropBlocks(doc, [id(1), id(2)], id(3), "inside"))), ["text:c", "  text:a", "  text:b", "  text:kid"]);
  assert.equal(dropBlocks(doc, [id(3)], id(4), "inside").ops.length, 0);
});

test("turn into changes only the type; delete sends the selection to Trash", () => {
  const doc = PageDoc.from(state([[1, "text", "a", 0, { marks: [{ s: 0, e: 1, t: "b" }], color: "blue" }], [2, "text", "b"], [3, "text", "c", 2]]));
  const turned = turnInto(doc, [id(1), id(2)], "to_do");
  assert.deepEqual(turned.ops, [{ op: "set", id: id(1), type: "to_do" }, { op: "set", id: id(2), type: "to_do" }]);
  const after = applied(doc, turned);
  assert.deepEqual(after.get(id(1)).properties, { marks: [{ s: 0, e: 1, t: "b" }], color: "blue" });
  const removed = deleteBlocks(doc, [id(2), id(3)]);
  assert.deepEqual(removed.ops, [{ op: "delete", id: id(2) }]);
  assert.deepEqual(outline(applied(doc, removed)), ["text:a"]);
  assert.deepEqual(removed.focus, { id: id(1), offset: 1 });
});

test("pasting Markdown creates typed, nested blocks around the caret", () => {
  const specs = parseMarkdown("# Plan\n- **bold** item\n  - nested `code`\n1. first\n2. second\n- [ ] open\n- [x] done\n> quoted\n---\n```js\nlet a;\n\nlet b;\n```\nplain [link](https://example.test)");
  assert.deepEqual(specs.map((spec) => [spec.type, spec.text, spec.depth]), [
    ["heading_1", "Plan", 0], ["bulleted_list", "bold item", 0], ["bulleted_list", "nested code", 1], ["numbered_list", "first", 0],
    ["numbered_list", "second", 0], ["to_do", "open", 0], ["to_do", "done", 0], ["quote", "quoted", 0], ["divider", "", 0],
    ["code", "let a;\n\nlet b;", 0], ["text", "plain link", 0],
  ]);
  assert.deepEqual(specs[1].props.marks, [{ s: 0, e: 4, t: "b" }]);
  assert.equal(specs[6].props.checked, true);
  assert.equal(specs[9].props.language, "js");
  assert.deepEqual(specs[10].props.marks, [{ s: 6, e: 10, t: "a", href: "https://example.test" }]);
  const doc = PageDoc.from(state([[1, "text", "before|after"], [2, "text", "tail"]]));
  const pasted = applied(doc, insertSpecs(doc, id(1), 7, 7, parseMarkdown("one\n- two\n  - three"), fresh));
  assert.deepEqual(outline(pasted), ["text:before|one", "bulleted_list:two", "  bulleted_list:threeafter", "text:tail"]);
  const empty = PageDoc.from(state([[1, "text", ""]]));
  assert.deepEqual(outline(applied(empty, insertSpecs(empty, id(1), 0, 0, parseMarkdown("## Head\nbody"), fresh))), ["heading_2:Head", "text:body"]);
  // Typed blocks pasted at the end of a written block follow it instead of joining its text.
  const written = PageDoc.from(state([[1, "text", "Written"]]));
  const typed = insertSpecs(written, id(1), 7, 7, parseMarkdown("## Head\n- item"), fresh);
  assert.deepEqual(outline(applied(written, typed)), ["text:Written", "heading_2:Head", "bulleted_list:item"]);
  assert.equal(typed.focus.offset, 4);
});

test("copying blocks produces Markdown that pastes back as the same blocks", () => {
  const doc = PageDoc.from(state([
    [1, "heading_2", "Plan"], [2, "to_do", "ship", 0, { checked: true }], [3, "bulleted_list", "deep", 2],
    [4, "numbered_list", "a"], [5, "numbered_list", "b"], [6, "code", "x = 1\ny = 2", 0, { language: "py" }],
    [7, "text", "bold", 0, { marks: [{ s: 0, e: 4, t: "b" }] }], [8, "divider", ""], [9, "quote", "q"],
  ]));
  const entries = visibleRows(doc).map((row) => ({ block: row.block, depth: row.depth }));
  const markdown = blocksMarkdown(entries);
  assert.equal(markdown, "## Plan\n- [x] ship\n  - deep\n1. a\n2. b\n```py\nx = 1\ny = 2\n```\n**bold**\n---\n> q");
  assert.deepEqual(parseMarkdown(markdown).map((spec) => [spec.type, spec.text, spec.depth]),
    entries.map((entry) => [entry.block.type, entry.block.text, entry.depth]));
});

test("Markdown shortcuts turn a block into the typed block as you type", () => {
  assert.deepEqual(shortcut("# ", "text"), { type: "heading_1", text: "", strip: 2, props: {} });
  assert.deepEqual(shortcut("## Title", "text"), { type: "heading_2", text: "Title", strip: 3, props: {} });
  assert.deepEqual(shortcut("### ", "text").type, "heading_3");
  assert.deepEqual(shortcut("- ", "text").type, "bulleted_list");
  assert.deepEqual(shortcut("* ", "text").type, "bulleted_list");
  assert.deepEqual(shortcut("1. ", "text").type, "numbered_list");
  assert.deepEqual(shortcut("[] ", "text"), { type: "to_do", text: "", strip: 3, props: { checked: false } });
  assert.deepEqual(shortcut("[x] ", "text").props, { checked: true });
  assert.deepEqual(shortcut("> ", "text").type, "quote");
  assert.deepEqual(shortcut("---", "text"), { type: "divider", text: "", strip: 3, props: {} });
  assert.deepEqual(shortcut("```", "text"), { type: "code", text: "", strip: 3, props: {} });
  assert.equal(shortcut("#hashtag", "text"), null);
  assert.equal(shortcut("- ", "to_do").type, "bulleted_list");
  assert.equal(shortcut("1. ", "bulleted_list").type, "numbered_list");
  assert.equal(shortcut("- ", "bulleted_list"), null, "a list's own marker stays text");
  assert.equal(shortcut("# ", "code"), null, "code keeps what is typed");
  assert.equal(shortcut("a - ", "text"), null);
});

test("inline marks toggle, slice, join, render escaped HTML and round-trip Markdown", () => {
  let marks = toggleMark("hello world", [], 0, 5, "b");
  assert.deepEqual(marks, [{ s: 0, e: 5, t: "b" }]);
  marks = toggleMark("hello world", marks, 3, 8, "b");
  assert.deepEqual(marks, [{ s: 0, e: 8, t: "b" }]);
  marks = toggleMark("hello world", marks, 2, 4, "b");
  assert.deepEqual(marks, [{ s: 0, e: 2, t: "b" }, { s: 4, e: 8, t: "b" }]);
  marks = toggleMark("hello world", marks, 6, 11, "a", "https://example.test");
  assert.deepEqual(sliceMarks(marks, 3, 9), [{ s: 1, e: 5, t: "b" }, { s: 3, e: 6, t: "a", href: "https://example.test" }]);
  assert.deepEqual(concatMarks([{ s: 0, e: 2, t: "i" }], 2, [{ s: 0, e: 1, t: "i" }]), [{ s: 0, e: 3, t: "i" }]);
  assert.equal(marksHTML("a<b & c", [{ s: 0, e: 3, t: "b" }, { s: 2, e: 3, t: "i" }]), "<strong>a&lt;<em>b</em></strong> &amp; c");
  assert.equal(marksHTML("x", [{ s: 0, e: 1, t: "a", href: "javascript:alert(1)" }]), "x");
  assert.equal(marksHTML("line\n", []), "line\n<br data-tail=\"\">");
  assert.equal(inlineMarkdown("bold code link", [{ s: 0, e: 4, t: "b" }, { s: 5, e: 9, t: "c" }, { s: 10, e: 14, t: "a", href: "https://k.test" }]),
    "**bold** `code` [link](https://k.test)");
  assert.deepEqual(parseInline("**bold** *it* `code` [link](https://k.test)"),
    { text: "bold it code link", marks: [{ s: 0, e: 4, t: "b" }, { s: 5, e: 7, t: "i" }, { s: 8, e: 12, t: "c" }, { s: 13, e: 17, t: "a", href: "https://k.test" }] });
  assert.deepEqual(parseInline("[[Wiki page]] and #tag stay plain"), { text: "[[Wiki page]] and #tag stay plain", marks: [] });
});

test("one keyboard model drives text editing and block selection", () => {
  const key = (k, extra = {}) => ({ key: k, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, isComposing: false, ...extra });
  const text = { mode: "text", atStart: false, atEnd: false, collapsed: true, firstLine: false, lastLine: false, type: "text" };
  assert.equal(keyCommand(key("Enter"), text), "split");
  assert.equal(keyCommand(key("Enter", { shiftKey: true }), text), "newline");
  assert.equal(keyCommand(key("Enter"), { ...text, type: "code" }), "newline");
  assert.equal(keyCommand(key("Enter", { ctrlKey: true }), { ...text, type: "code" }), "split");
  assert.equal(keyCommand(key("Enter", { isComposing: true }), text), null);
  assert.equal(keyCommand(key("Backspace"), { ...text, atStart: true }), "mergeBackward");
  assert.equal(keyCommand(key("Backspace"), text), null);
  assert.equal(keyCommand(key("Backspace"), { ...text, atStart: true, collapsed: false }), null);
  assert.equal(keyCommand(key("Delete"), { ...text, atEnd: true }), "mergeForward");
  assert.equal(keyCommand(key("Tab"), text), "indent");
  assert.equal(keyCommand(key("Tab", { shiftKey: true }), text), "outdent");
  assert.equal(keyCommand(key("Escape"), text), "selectBlock");
  assert.equal(keyCommand(key("ArrowUp"), { ...text, firstLine: true }), "focusPrevious");
  assert.equal(keyCommand(key("ArrowDown"), { ...text, lastLine: true }), "focusNext");
  assert.equal(keyCommand(key("ArrowUp"), text), null);
  assert.equal(keyCommand(key("ArrowUp", { shiftKey: true }), { ...text, firstLine: true, atStart: true }), "extendUp");
  assert.equal(keyCommand(key("ArrowUp", { ctrlKey: true, shiftKey: true }), text), "moveUp");
  assert.equal(keyCommand(key("ArrowDown", { metaKey: true, shiftKey: true }), text), "moveDown");
  assert.equal(keyCommand(key("b", { ctrlKey: true }), text), "bold");
  assert.equal(keyCommand(key("i", { metaKey: true }), text), "italic");
  assert.equal(keyCommand(key("e", { ctrlKey: true }), text), "code");
  assert.equal(keyCommand(key("k", { ctrlKey: true }), text), "link");
  const blocks = { mode: "blocks" };
  assert.equal(keyCommand(key("ArrowDown"), blocks), "selectNext");
  assert.equal(keyCommand(key("ArrowUp"), blocks), "selectPrevious");
  assert.equal(keyCommand(key("ArrowDown", { shiftKey: true }), blocks), "extendDown");
  assert.equal(keyCommand(key("ArrowUp", { ctrlKey: true, shiftKey: true }), blocks), "moveUp");
  assert.equal(keyCommand(key("Enter"), blocks), "edit");
  assert.equal(keyCommand(key("Backspace"), blocks), "deleteBlocks");
  assert.equal(keyCommand(key("Delete"), blocks), "deleteBlocks");
  assert.equal(keyCommand(key("Escape"), blocks), "clearSelection");
  assert.equal(keyCommand(key("Tab"), blocks), "indent");
  assert.equal(keyCommand(key("a", { ctrlKey: true }), blocks), "selectAll");
});

// A fake blocks socket and page loader standing in for kitty's server.
function harness(initial, options = {}) {
  const sent = [];
  const replies = [];
  let server = PageDoc.from(initial);
  let seq = initial.seq;
  const updates = [];
  const revisions = [];
  const sync = new PageSync({
    state: initial,
    send: (message) => new Promise((resolve) => { sent.push(message); replies.push(resolve); }),
    load: async () => snapshot(),
    onupdate: () => updates.push(1),
    onrevision: (from, to, own) => revisions.push([from, to, own]),
    debounce: options.debounce ?? 0,
  });
  function snapshot() {
    const root = server.get(PAGE);
    const blocks = visibleRows(server).map((row) => structuredClone(row.block));
    return { page_id: PAGE, seq, revision: server.revision, root: structuredClone(root), blocks };
  }
  // The server applies the oldest pending message (with kitty's base revision check) and acks it.
  function serve({ conflict = false } = {}) {
    const message = sent.shift();
    const resolve = replies.shift();
    const base = message.base?.[PAGE];
    if (conflict || (base !== undefined && base !== server.revision)) {
      resolve({ type: "reject", txn_id: message.txn_id, status: 409, error: "Page changed in another window; reload before saving" });
      return message;
    }
    for (const op of message.ops) server.apply(op);
    const revision = server.revision + 1;
    server.apply({ op: "set", id: PAGE, props: { revision } });
    seq += 1;
    resolve({ type: "ack", txn_id: message.txn_id, seq: { [PAGE]: seq }, revisions: { [PAGE]: revision } });
    return message;
  }
  // The server acks the oldest message as-is (for operations on pages this harness does not hold).
  function acknowledge(revision) {
    const message = sent.shift();
    replies.shift()({ type: "ack", txn_id: message.txn_id, seq: { [PAGE]: ++seq }, revisions: { [PAGE]: revision } });
    return message;
  }
  function remote(ops) {
    for (const op of ops) server.apply(op);
    const revision = server.revision + 1;
    server.apply({ op: "set", id: PAGE, props: { revision } });
    seq += 1;
    return { type: "change", seq, txn_id: `remote-${seq}`, page_id: PAGE, at: "2026-11-01T00:00:00Z", device_id: "other", root: { type: "page", number: 1 },
      ops: [...ops, { op: "set", id: PAGE, props: { revision } }] };
  }
  return { sync, sent, serve, acknowledge, remote, updates, revisions, server: () => server };
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test("edits echo locally, go out one transaction at a time with base revisions and text splices", async () => {
  const h = harness(state([[1, "text", "hello"]]));
  h.sync.commit([{ op: "text", id: id(1), text: "hello w" }], { typing: true });
  h.sync.commit([{ op: "text", id: id(1), text: "hello wo" }], { typing: true });
  assert.equal(h.sync.local.get(id(1)).text, "hello wo");
  await tick();
  assert.equal(h.sent.length, 1);
  assert.deepEqual(h.sent[0].ops, [{ op: "text", id: id(1), at: 5, del: 0, ins: " wo" }]);
  assert.deepEqual(h.sent[0].base, { [PAGE]: 3 });
  h.sync.commit([{ op: "text", id: id(1), text: "hello wor" }], { typing: true });
  await tick();
  assert.equal(h.sent.length, 1, "the next transaction waits for the ack");
  assert.ok(h.sync.pending);
  h.serve();
  await tick();
  assert.equal(h.sent.length, 1);
  assert.deepEqual(h.sent[0].base, { [PAGE]: 4 });
  assert.deepEqual(h.sent[0].ops, [{ op: "text", id: id(1), at: 8, del: 0, ins: "r" }]);
  h.serve();
  await h.sync.flush();
  assert.equal(h.sync.pending, false);
  assert.equal(h.server().get(id(1)).text, "hello wor");
  assert.deepEqual(h.revisions, [[3, 4, true], [4, 5, true]]);
  // Its own changes echoed back by the socket are not applied twice.
  h.sync.receive({ type: "change", seq: 11, txn_id: h.sync.lastTxn, page_id: PAGE, ops: [{ op: "text", id: id(1), at: 8, del: 0, ins: "r" }] });
  assert.equal(h.sync.local.get(id(1)).text, "hello wor");
});

test("another window's change renders without clobbering text typed here", async () => {
  const h = harness(state([[1, "text", "mine"], [2, "text", "theirs"]]), { debounce: 1000 });
  h.sync.commit([{ op: "text", id: id(1), text: "mine, typing" }], { typing: true });
  h.sync.receive(h.remote([{ op: "text", id: id(2), text: "theirs, edited" }, { op: "text", id: id(1), text: "overwritten?" }]));
  assert.equal(h.sync.local.get(id(2)).text, "theirs, edited");
  assert.equal(h.sync.local.get(id(1)).text, "mine, typing", "unsent local text wins over the remote edit");
  assert.ok(h.updates.length >= 2);
  const flushing = h.sync.flush();
  await tick();
  assert.deepEqual(h.sent[0].base, { [PAGE]: 4 });
  h.serve();
  await flushing;
  assert.equal(h.server().get(id(1)).text, "mine, typing");
  // Changes the loaded page already contained are skipped.
  h.sync.receive({ type: "change", seq: 5, txn_id: "old", page_id: PAGE, ops: [{ op: "text", id: id(2), text: "stale" }] });
  assert.equal(h.sync.local.get(id(2)).text, "theirs, edited");
});

test("a 409 reject re-syncs the page and resends the local edits on top of it", async () => {
  const h = harness(state([[1, "text", "a"], [2, "text", "b"]]));
  h.sync.commit([{ op: "text", id: id(1), text: "a!" }]);
  await tick();
  // Another window commits first; its change has not reached this window yet.
  h.remote([{ op: "text", id: id(2), text: "b from elsewhere" }]);
  h.serve();
  await tick();
  await tick();
  assert.equal(h.sync.local.get(id(2)).text, "b from elsewhere");
  assert.equal(h.sent.length, 1, "the edit is sent again after the reload");
  assert.deepEqual(h.sent[0].base, { [PAGE]: 4 });
  assert.deepEqual(h.sent[0].ops, [{ op: "text", id: id(1), at: 1, del: 0, ins: "!" }]);
  h.serve();
  await h.sync.flush();
  assert.equal(h.server().get(id(1)).text, "a!");
  assert.equal(h.server().get(id(2)).text, "b from elsewhere");
});

test("local edits that no longer apply after a re-sync are dropped, not forced", async () => {
  const h = harness(state([[1, "text", "a"], [2, "text", "b"]]));
  h.sync.commit([{ op: "text", id: id(2), text: "b edited here" }]);
  await tick();
  h.remote([{ op: "delete", id: id(2) }]);
  h.serve();
  await tick();
  await tick();
  assert.equal(h.sync.local.has(id(2)), false);
  assert.equal(h.sent.length, 0);
  assert.equal(h.sync.pending, false);
});

test("an HTTP save of the same page holds editor transactions until its change arrives", async () => {
  const h = harness(state([[1, "text", "a"]]));
  let resumed = false;
  const saving = h.sync.exclusive(async (revision) => {
    assert.equal(revision, 3);
    const change = h.remote([{ op: "text", id: PAGE, text: "Renamed" }]);
    setTimeout(() => { resumed = true; h.sync.receive(change); }, 5);
    return h.server().revision;
  });
  await saving;
  h.sync.commit([{ op: "text", id: id(1), text: "ab" }]);
  await tick();
  assert.equal(h.sent.length, 0, "held until the save's own change is applied");
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.ok(resumed);
  assert.equal(h.sent.length, 1);
  assert.deepEqual(h.sent[0].base, { [PAGE]: 4 });
  assert.equal(h.sync.local.root.text, "Renamed");
});

test("typing in a formatted block sends only the last text and marks per block", async () => {
  const h = harness(state([[1, "text", "ab", 0, { marks: [{ s: 0, e: 2, t: "b" }] }]]));
  h.sync.commit([{ op: "text", id: id(1), text: "abc" }, { op: "set", id: id(1), props: { marks: [{ s: 0, e: 3, t: "b" }] } }], { typing: true });
  h.sync.commit([{ op: "text", id: id(1), text: "abcd" }, { op: "set", id: id(1), props: { marks: [{ s: 0, e: 4, t: "b" }] } }], { typing: true });
  await tick();
  assert.deepEqual(h.sent[0].ops, [{ op: "text", id: id(1), at: 2, del: 0, ins: "cd" }, { op: "set", id: id(1), props: { marks: [{ s: 0, e: 4, t: "b" }] } }]);
});

test("blocks moved onto another page leave this one only when the server confirms it", async () => {
  const h = harness(state([[1, "text", "stay"], [2, "text", "go"]]));
  const other = "11111111-1111-4111-8111-111111111111";
  const moving = h.sync.transactNow([{ op: "move", id: id(2), parent: other, key: "a0" }]);
  await tick();
  assert.equal(h.sync.local.has(id(2)), true, "still here until the ack");
  assert.deepEqual(h.sent[0].base, { [PAGE]: 3 });
  h.acknowledge(4);
  assert.equal((await moving).type, "ack");
  assert.equal(h.sync.local.has(id(2)), false);
  assert.deepEqual(h.revisions.at(-1), [3, 4, true]);
});
