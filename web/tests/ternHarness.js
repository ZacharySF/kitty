// Browser fixture for tests/tern.browser.test.mjs. Mounts the real Tern view with a
// stand-in rika! API, so the page never reaches a tern binary or daemon.
import { mount } from "svelte";
import "/src/styles.css";
import { loadBuild } from "/src/lib/build.svelte.ts";
import { createBuildStub } from "./buildStub.js";
import "/src/lib/themes/engine.css";
import TernView from "/src/lib/TernView.svelte";

const BASE = {
  install_url: "https://stencil.so/tern", installed: true, path: "/home/user/.local/bin/tern",
  source: "path", override: null, version: "0.9.8", problem: null, workspace: "/home/user/kitty-workspace",
  socket: "/run/user/1000/kitty/tern-0123456789ab/daemon.sock", running: false, windows: 0, sessions: [],
  activity: [], quick_notes: true,
};
const RUNNING = {
  ...BASE, running: true, windows: 1, sessions: [{
    id: 1, name: "kitty", shown: true, tabs: [{
      id: 2, number: 1, name: null, shown: true, blocks: [
        { id: 3, title: "cargo test", cwd: "/home/user/kitty-workspace", program: "/bin/bash", cols: 160, rows: 45,
          focused: true, live: true, exited: null, working: true },
        { id: 4, title: "notes", cwd: "/home/user/kitty-workspace/docs", program: "/bin/bash", cols: 80, rows: 45,
          focused: false, live: true, exited: null, working: false },
      ],
    }],
  }],
};
const block = (id, extra) => ({ id, title: "", cwd: "/home/user/kitty-workspace", program: "/bin/bash", cols: 120, rows: 30,
  focused: false, live: true, exited: null, working: false, ...extra });
/** rika!'s session with a working and an exited pane; POSTs to its pane routes change it like rika! would. */
const PANES = {
  ...BASE, running: true, windows: 0,
  activity: [{ event: "pane_exited", pane: 5, status: 3, at: "2026-10-31T20:00:00+00:00" },
             { event: "pane_spawned", pane: 5, status: null, at: "2026-10-31T19:59:00+00:00" }],
  sessions: [{ id: 1, name: "kitty", shown: true, tabs: [
    { id: 2, number: 1, name: null, shown: true, blocks: [block(4, { title: "cargo test", focused: true, working: true })] },
    { id: 3, number: 2, name: null, shown: false, blocks: [block(5, { title: "make check", exited: 3 })] },
  ] }],
};
const STATES = {
  absent: { ...BASE, installed: false, path: null, source: null, version: null },
  broken: { ...BASE, installed: false, path: null, source: "settings", override: "/opt/tern/tern", version: null,
            problem: "The Tern path in settings is not an executable file: /opt/tern/tern" },
  stopped: BASE,
  running: RUNNING,
  panes: PANES,
};

/** Error answers for the next pane POSTs, by path. */
const paneErrors = {};
function panePost(url, body) {
  if (paneErrors[url]) return json({ error: paneErrors[url] }, 400);
  const action = url.split("/").pop();
  const blocks = state.sessions[0].tabs.flatMap((tab) => tab.blocks);
  if (action === "new") {
    // Like Tern without a window: the new tab's block is focused, the shown tab stays.
    const id = Math.max(...blocks.map((item) => item.id)) + 1;
    state.sessions[0].tabs.push({ id: id + 100, number: state.sessions[0].tabs.length + 1, name: null, shown: false,
      blocks: [block(id, { focused: true })] });
  } else if (action === "open") {
    state.windows += 1;
  } else if (action === "run") {
    blocks.find((item) => item.id === body.block).title = body.text;
  } else if (action === "focus") {
    for (const item of blocks) item.focused = item.id === body.block;
    if (!state.windows) state.windows = 1;
  } else if (action === "capture") {
    return json({ note_id: 7, block: body.block, truncated: false });
  }
  return json(state);
}
const opened = [];
window.open = (url, name) => { opened.push({ url, name }); return null; };

const calls = [];
const mode = new URLSearchParams(location.search).get("state") ?? "stopped";
let state = mode === "panes" ? structuredClone(PANES) : STATES[mode];
/** Answers for the next POSTs, by path: a status object or {status, error}. */
const replies = {};
/** GET /api/terminal/tern/plugin; install and remove change it. */
let plugin = { available: true, version: "0.1.0", dir: "/home/user/kitty-workspace/.private/tern/plugin", installed: false,
               installed_version: null, current: false, linked_elsewhere: null, tern_status: null, problems: [], enabled: true };

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const build = createBuildStub();
window.fetch = async (url, init = {}) => {
  const answer = build.fetch(url, init);
  if (answer) return answer;
  const call = { url: String(url), method: init.method ?? "GET", headers: init.headers ?? {},
                 body: init.body ? JSON.parse(init.body) : undefined };
  calls.push(call);
  if (call.method === "GET" && call.url === "/api/terminal/tern") return json(state);
  if (call.url === "/api/terminal/tern/plugin" || call.url.startsWith("/api/terminal/tern/plugin/")) {
    if (call.url.endsWith("/install")) plugin = { ...plugin, installed: true, installed_version: "0.1.0", current: true, tern_status: "ready" };
    if (call.url.endsWith("/remove")) plugin = { ...plugin, installed: false, installed_version: null, current: false, tern_status: null };
    return json(plugin);
  }
  if (mode === "panes" && call.method === "POST" && /^\/api\/terminal\/tern\/(new|open|run|focus|capture)$/.test(call.url))
    return panePost(call.url, call.body);
  const reply = replies[call.url];
  if (call.method === "POST" && reply) {
    if (reply.error) return json({ error: reply.error }, reply.status);
    state = reply;
    return json(state);
  }
  return json({ error: "not in harness" }, 404);
};

await loadBuild();
mount(TernView, { target: document.getElementById("view") });

window.ternHarness = {
  calls, STATES, opened, paneErrors,
  posts: () => calls.filter((call) => call.method === "POST"),
  reply(path, answer) { replies[path] = typeof answer === "string" ? STATES[answer] : answer; },
  set(name) { state = STATES[name]; },
};
