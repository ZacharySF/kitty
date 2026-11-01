// Browser fixture for tests/devices.browser.test.mjs. Mounts Settings → Devices with a
// stand-in /api/devices that follows the route contract; no sync listener is involved.
import { mount } from "svelte";
import "/src/styles.css";
import { applySavedTheme } from "/src/lib/themes.ts";
import Devices from "/src/lib/Devices.svelte";

const minutesAgo = (minutes) => new Date(Date.now() - minutes * 60_000).toISOString();
const state = {
  device: { id: "dev-a", name: "Synthetic desk", fingerprint: "3f2a 9c01 77be 0d4e", port: 47800, addresses: ["192.0.2.10:47800"],
    listening: true, error: null, paused: false, discovery: "on", discovery_error: null },
  pairing: null,
  discovered: [
    { device_id: "dev-c", name: "Synthetic laptop", address: "192.0.2.11:47800", pairing: true, paired: false },
    { device_id: "dev-d", name: "Synthetic tablet", address: "192.0.2.12:47800", pairing: false, paired: false },
  ],
  peers: [
    { device_id: "dev-b", name: "Synthetic phone", address: null, last_address: "192.0.2.13:47800", paired_at: minutesAgo(600),
      last_sync_at: minutesAgo(5), reached_at: minutesAgo(5), status: "idle", error: null, pending: 3, holds_history: true, online: true },
  ],
};
const calls = [];
/** Error answers for the next POST, by path (consumed once). */
const errors = {};
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const peer = (id) => state.peers.find((item) => item.device_id === id);

function post(path, body) {
  if (errors[path]) { const [status, message] = errors[path]; delete errors[path]; return json({ error: message }, status); }
  switch (path) {
    case "/api/devices/name": state.device.name = body.name; break;
    case "/api/devices/pair/show": state.pairing = { code: "QRS-TUV", expires_at: new Date(Date.now() + 300_000).toISOString(), attempts_left: 3, paired: null }; break;
    case "/api/devices/pair/cancel": state.pairing = null; break;
    case "/api/devices/pair/enter": {
      const found = state.discovered.find((item) => item.address === body.address);
      state.peers.push({ device_id: found?.device_id ?? "dev-x", name: found?.name ?? "Typed device", address: found ? null : body.address,
        last_address: body.address, paired_at: new Date().toISOString(), last_sync_at: null, reached_at: null, status: "syncing",
        error: null, pending: 0, holds_history: false, online: true });
      if (found) found.paired = true;
      break;
    }
    case "/api/devices/sync": for (const item of state.peers) if (!body.device_id || item.device_id === body.device_id) item.status = "syncing"; break;
    case "/api/devices/unpair": state.peers = state.peers.filter((item) => item.device_id !== body.device_id); break;
    case "/api/devices/address": peer(body.device_id).address = body.address; break;
    case "/api/devices/pause": state.device.paused = body.paused; for (const item of state.peers) item.status = body.paused ? "paused" : "idle"; break;
    default: return json({ error: "not in harness" }, 404);
  }
  return json(state);
}

window.fetch = async (url, init = {}) => {
  const path = String(url);
  calls.push({ url: path, method: init.method ?? "GET", headers: init.headers ?? {}, body: init.body ? JSON.parse(init.body) : undefined });
  if (init.method === "POST") return post(path, JSON.parse(init.body));
  if (path === "/api/devices") return json(state);
  return json({ error: "not in harness" }, 404);
};
const confirms = [];
let confirmAnswer = true;
window.confirm = (message) => { confirms.push(message); return confirmAnswer; };

applySavedTheme();
mount(Devices, { target: document.getElementById("view") });
window.devicesHarness = {
  state, calls, errors, confirms,
  answerConfirm(answer) { confirmAnswer = answer; },
  posts: () => calls.filter((call) => call.method === "POST").map((call) => [call.url, call.body]),
};
