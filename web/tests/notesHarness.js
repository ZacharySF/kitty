// Browser fixture for tests/notes.browser.test.mjs. Mounts the real Notes view with an
// in-memory stand-in for kitty!'s notes API and block backend (tests/fakeBlockServer.js);
// every record is synthetic. A page's title, revision and outline blocks live in the block
// stand-in, as they live in kitty's block tables; its canvas document lives here.
import { mount } from "svelte";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { loadBuild } from "/src/lib/build.svelte.ts";
import { createBuildStub } from "./buildStub.js";
import { keysBetween } from "/src/lib/blockKeys.ts";
import { createBlockServer } from "./fakeBlockServer.js";
import NotesHarness from "./NotesHarness.svelte";

const now = "2026-10-28T12:00:00+00:00";
const layers = [{ id: "default", name: "Drawing", visible: true, locked: false }];
const element = (id, kind, x, y, text, extra = {}) => ({
  id, kind, layer: "default", x, y, w: 220, h: 96, color: "#292438", fill: "#fff2a8", width: 2, text, fontSize: 20, ...extra,
});
const notebooks = [
  { id: 1, title: "Systems design", subject: "", project: "", created_at: now, updated_at: now, revision: 1, folder_id: null, position: 0 },
  { id: 2, title: "Reading log", subject: "", project: "", created_at: now, updated_at: now, revision: 1, folder_id: null, position: 1 },
];
const root = (n) => `30000000-0000-4000-8000-00000000000${n}`;
const block = (n) => `40000000-0000-4000-8000-0000000000${String(n).padStart(2, "0")}`;
/** Outline blocks for one page: [n, text, parent n | 0] in reading order. */
function outline(page, items) {
  const keys = keysBetween(null, null, items.length);
  return items.map(([n, text, parent], index) => ({ id: block(n), type: "text", parent_id: parent ? block(parent) : page, order_key: keys[index], properties: {}, text }));
}
const pages = new Map([
  [1, {
    id: 1, notebook_id: 1, viewport_revision: 1, position: 0, favorite: 0, last_opened: now, viewport: { x: 0, y: 0, zoom: 1 },
    document: { version: 1, grid: true, snap: false, background: "dotted", layers, elements: [
      element("hash-ring", "sticky", 80, 80, "Hash ring"),
      element("virtual-nodes", "sticky", 420, 80, "Virtual nodes"),
      { id: "ring-link", kind: "connector", layer: "default", x: 0, y: 0, w: 0, h: 0, color: "#292438", fill: "none", width: 2, from: "hash-ring", to: "virtual-nodes", points: [[300, 128, 0.5], [420, 128, 0.5]] },
    ] },
  }],
  [2, { id: 2, notebook_id: 1, viewport_revision: 1, position: 1, favorite: 0, last_opened: now, viewport: { x: 0, y: 0, zoom: 1 }, document: { version: 1, grid: true, snap: false, layers, elements: [] } }],
  [3, { id: 3, notebook_id: 2, viewport_revision: 1, position: 0, favorite: 0, last_opened: null, viewport: { x: 0, y: 0, zoom: 1 }, document: { version: 1, grid: true, snap: false, layers, elements: [] } }],
]);
const server = createBlockServer({
  pages: [
    { page_id: root(1), number: 1, title: "Consistent hashing", blocks: outline(root(1), [
      [1, "A hash ring maps each key to the next node clockwise.", 0],
      [2, "Virtual nodes spread load when a server joins or leaves.", 1],
      [3, "Replication details live in [[Raft notes]].", 0],
    ]) },
    { page_id: root(2), number: 2, title: "Raft notes", blocks: outline(root(2), [[4, "A leader replicates log entries to followers.", 0]]) },
    { page_id: root(3), number: 3, title: "Designing data-intensive applications", blocks: outline(root(3), [[5, "Chapter 5: replication.", 0]]) },
  ],
});

const calls = [];
const blocks = (id) => server.state(root(id));
const harness = {
  calls, server, failSaves: false,
  /** What kitty would have saved for page `id`: title, canvas and outline text. */
  saved: (id) => ({ title: blocks(id).root.text, revision: blocks(id).revision, document: structuredClone(pages.get(id).document),
    blocks: blocks(id).blocks.map((item) => ({ id: item.id, text: item.text, type: item.type })) }),
  /** Another window edits page `id`: a new title and/or the text of its first outline block. */
  editElsewhere(id, { title, text }) {
    const state = blocks(id);
    const ops = [];
    if (title !== undefined) ops.push({ op: "text", id: state.page_id, text: title });
    if (text !== undefined) ops.push({ op: "text", id: state.blocks[0].id, text });
    server.commit(ops, "window-two");
  },
};
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const pageOf = (id) => {
  const state = blocks(id);
  const { viewport_revision, ...rest } = pages.get(id);
  return { ...structuredClone(rest), viewport_revision, title: state.root.text, revision: state.revision, updated_at: now };
};
const summary = (id) => {
  const { document, viewport, viewport_revision, ...page } = pageOf(id);
  return page;
};
const outlineResult = (id) => {
  const state = blocks(id);
  return { revision: state.revision, blocks: state.blocks.map((item, index) => ({ id: item.id, page_id: id, parent_id: item.parent_id === state.page_id ? null : item.parent_id,
    text: item.text, ordering: index, collapsed: false, type: item.type, props: {} })), references: {}, citation_counts: {} };
};
let preferences = { allow_ai: false, retention_days: 30, revision: 1 };
// Map mode: no page has a mind map yet; creating one hands back a fake map id.
const pageMaps = new Map();
// `status` is the save state Notes reports to its workspace tab dot (Q3).
harness.status = null;

const build = createBuildStub();
window.fetch = async (input, init = {}) => {
  const answer = build.fetch(input, init);
  if (answer) return answer;
  const url = new URL(String(input), location.href);
  const method = init.method ?? "GET";
  const body = init.body ? JSON.parse(init.body) : undefined;
  calls.push({ path: url.pathname + url.search, method, body });
  const handled = server.fetch(url, method, body);
  if (handled) return handled;
  const path = url.pathname.replace(/^\/api/, "");
  const match = /^\/notes\/pages\/(\d+)(\/[a-z]+)?$/.exec(path);
  const id = match ? Number(match[1]) : 0;
  const page = pages.get(id);
  if (path === "/notes" && method === "GET") {
    return json({ folders: [], notebooks, pages: [...pages.keys()].map(summary), preferences });
  }
  if (path === "/notes/preferences" && method === "POST") {
    preferences = { allow_ai: body.allow_ai, retention_days: body.retention_days, revision: preferences.revision + 1 };
    return json(preferences);
  }
  if (path === "/notes/daily" && method === "POST") {
    return json(pageOf(1));
  }
  if (match && !page) return json({ error: "Page not found" }, 404);
  if (match && !match[2] && method === "GET") return json(pageOf(id));
  if (match && !match[2] && method === "POST") {
    if (harness.failSaves) return json({ error: "Synthetic save failure" }, 500);
    if (body.revision !== blocks(id).revision) return json({ error: "This page changed elsewhere." }, 409);
    page.document = body.document;
    // Like kitty's save: the canvas and title change in one transaction that bumps the revision.
    server.commit([{ op: "text", id: blocks(id).page_id, text: body.title }, { op: "set", id: blocks(id).page_id, props: { canvas_saved: Date.now() } }], "http");
    return json(pageOf(id));
  }
  if (match?.[2] === "/blocks" && method === "GET") return json(outlineResult(id));
  if (match?.[2] === "/mentions") return json({ mentions: [] });
  if (match?.[2] === "/viewport" && method === "POST") {
    page.viewport = body.viewport;
    page.viewport_revision += 1;
    return json({ viewport_revision: page.viewport_revision });
  }
  if (match?.[2] === "/organize") return json({});
  if (match?.[2] === "/map" && method === "GET") return json({ map_id: pageMaps.get(page.id) ?? null });
  if (match?.[2] === "/map" && method === "POST") {
    pageMaps.set(page.id, 900 + page.id);
    return json({ map_id: pageMaps.get(page.id) });
  }
  if (path === "/schema" && method === "GET") return json({ maps: [], subjects: [] });
  if (path === "/documents/originals") return json({ originals: [] });
  if (path === "/backlinks/related") return json({ linked: [], unlinked: [], related: [] });
  if (path === "/notes/tags") return json({ tags: [] });
  if (path.startsWith("/atlas/sections/")) return json({ nodes: [], links: [], total: 0, shown: 0 });
  return json({ error: "not in harness" }, 404);
};
window.WebSocket = server.FakeSocket;
harness.sockets = server.sockets;

const params = new URLSearchParams(location.search);
sessionStorage.setItem("kitty.notes.open-page", params.get("page") ?? "1");
await loadBuild();
// App.svelte measures its pinned top bar into --workbench-top; the harness's stand-in band is 96px.
document.documentElement.style.setProperty("--workbench-top", "96px");
window.notesHarness = harness;
mount(NotesHarness, {
  target: document.getElementById("view"),
  props: { initialMode: params.get("mode") === "map" ? "map" : undefined },
});
