// Browser fixture for tests/tern.browser.test.mjs. Mounts the real Tern view with a
// stand-in kitty! API, so the page never reaches a tern binary or daemon.
import { mount } from "svelte";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { applySavedTheme } from "/src/lib/themes/index.ts";
import TernView from "/src/lib/TernView.svelte";

const BASE = {
  install_url: "https://stencil.so/tern", installed: true, path: "/home/user/.local/bin/tern",
  source: "path", override: null, version: "0.9.8", problem: null, workspace: "/home/user/kitty-workspace",
  socket: "/run/user/1000/kitty/tern-0123456789ab/daemon.sock", running: false, windows: 0, sessions: [],
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
const STATES = {
  absent: { ...BASE, installed: false, path: null, source: null, version: null },
  broken: { ...BASE, installed: false, path: null, source: "settings", override: "/opt/tern/tern", version: null,
            problem: "The Tern path in settings is not an executable file: /opt/tern/tern" },
  stopped: BASE,
  running: RUNNING,
};

const calls = [];
let state = STATES[new URLSearchParams(location.search).get("state") ?? "stopped"];
/** Answers for the next POSTs, by path: a status object or {status, error}. */
const replies = {};

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
window.fetch = async (url, init = {}) => {
  const call = { url: String(url), method: init.method ?? "GET", headers: init.headers ?? {},
                 body: init.body ? JSON.parse(init.body) : undefined };
  calls.push(call);
  if (call.method === "GET" && call.url === "/api/terminal/tern") return json(state);
  const reply = replies[call.url];
  if (call.method === "POST" && reply) {
    if (reply.error) return json({ error: reply.error }, reply.status);
    state = reply;
    return json(state);
  }
  return json({ error: "not in harness" }, 404);
};

applySavedTheme();
mount(TernView, { target: document.getElementById("view") });

window.ternHarness = {
  calls, STATES,
  posts: () => calls.filter((call) => call.method === "POST"),
  reply(path, answer) { replies[path] = typeof answer === "string" ? STATES[answer] : answer; },
  set(name) { state = STATES[name]; },
};
