// Browser fixture for tests/notes.browser.test.mjs. Mounts the real Notes view
// with an in-memory stand-in for rika!'s notes API; every record is synthetic.
import { mount } from "svelte";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { applySavedTheme } from "/src/lib/themes/index.ts";
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
const pages = new Map([
  [1, {
    id: 1, notebook_id: 1, title: "Consistent hashing", revision: 1, viewport_revision: 1, position: 0, favorite: 0, last_opened: now, updated_at: now,
    viewport: { x: 0, y: 0, zoom: 1 },
    document: { version: 1, grid: true, snap: false, background: "dotted", layers, elements: [
      element("hash-ring", "sticky", 80, 80, "Hash ring"),
      element("virtual-nodes", "sticky", 420, 80, "Virtual nodes"),
      { id: "ring-link", kind: "connector", layer: "default", x: 0, y: 0, w: 0, h: 0, color: "#292438", fill: "none", width: 2, from: "hash-ring", to: "virtual-nodes", points: [[300, 128, 0.5], [420, 128, 0.5]] },
    ] },
    blocks: [
      { id: "b-ring", parent_id: null, text: "A hash ring maps each key to the next node clockwise.", ordering: 0, collapsed: false },
      { id: "b-virtual", parent_id: "b-ring", text: "Virtual nodes spread load when a server joins or leaves.", ordering: 1, collapsed: false },
      { id: "b-raft", parent_id: null, text: "Replication details live in [[Raft notes]].", ordering: 2, collapsed: false },
    ],
  }],
  [2, {
    id: 2, notebook_id: 1, title: "Raft notes", revision: 1, viewport_revision: 1, position: 1, favorite: 0, last_opened: now, updated_at: now,
    viewport: { x: 0, y: 0, zoom: 1 },
    document: { version: 1, grid: true, snap: false, layers, elements: [] },
    blocks: [{ id: "b-leader", parent_id: null, text: "A leader replicates log entries to followers.", ordering: 0, collapsed: false }],
  }],
  [3, {
    id: 3, notebook_id: 2, title: "Designing data-intensive applications", revision: 1, viewport_revision: 1, position: 0, favorite: 0, last_opened: null, updated_at: now,
    viewport: { x: 0, y: 0, zoom: 1 },
    document: { version: 1, grid: true, snap: false, layers, elements: [] },
    blocks: [{ id: "b-ch5", parent_id: null, text: "Chapter 5: replication.", ordering: 0, collapsed: false }],
  }],
]);

const calls = [];
let preferences = { allow_ai: false, retention_days: 30, revision: 1 };
// Map mode: no page has a mind map yet; creating one hands back a fake map id.
const pageMaps = new Map();
// `status` is the save state Notes reports to its workspace tab dot (Q3).
const harness = { calls, failSaves: false, status: null, saved: (id) => structuredClone(pages.get(id)) };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const summary = ({ blocks, document, viewport, viewport_revision, ...page }) => page;
const blocksResult = (page) => ({ revision: page.revision, blocks: structuredClone(page.blocks), references: {}, citation_counts: {} });

window.fetch = async (input, init = {}) => {
  const url = new URL(String(input), location.href);
  const method = init.method ?? "GET";
  const body = init.body ? JSON.parse(init.body) : undefined;
  calls.push({ path: url.pathname + url.search, method, body });
  const path = url.pathname.replace(/^\/api/, "");
  const match = /^\/notes\/pages\/(\d+)(\/[a-z]+)?$/.exec(path);
  const page = match ? pages.get(Number(match[1])) : undefined;
  if (path === "/notes" && method === "GET") {
    return json({ folders: [], notebooks, pages: [...pages.values()].map(summary), preferences });
  }
  if (path === "/notes/preferences" && method === "POST") {
    preferences = { allow_ai: body.allow_ai, retention_days: body.retention_days, revision: preferences.revision + 1 };
    return json(preferences);
  }
  if (path === "/notes/daily" && method === "POST") {
    const { blocks, ...rest } = structuredClone(pages.get(1));
    return json(rest);
  }
  if (match && !page) return json({ error: "Page not found" }, 404);
  if (match && !match[2] && method === "GET") {
    const { blocks, ...rest } = structuredClone(page);
    return json(rest);
  }
  if (match?.[2] === "/blocks" && method === "GET") return json(blocksResult(page));
  if (match?.[2] === "/blocks" && method === "POST") {
    if (harness.failSaves) return json({ error: "Synthetic save failure" }, 500);
    if (body.revision !== page.revision) return json({ error: "This page changed elsewhere." }, 409);
    Object.assign(page, { title: body.title, document: body.document, blocks: body.blocks, revision: page.revision + 1, updated_at: new Date().toISOString() });
    return json(blocksResult(page));
  }
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

const params = new URLSearchParams(location.search);
sessionStorage.setItem("kitty.notes.open-page", params.get("page") ?? "1");
applySavedTheme();
// App.svelte measures its pinned top bar into --workbench-top; the harness's stand-in band is 96px.
document.documentElement.style.setProperty("--workbench-top", "96px");
window.notesHarness = harness;
mount(NotesHarness, {
  target: document.getElementById("view"),
  props: { initialMode: params.get("mode") === "map" ? "map" : undefined },
});
