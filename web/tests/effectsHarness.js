// Browser fixture for tests/effects.browser.test.mjs. Mounts the pending-approvals prompt
// (EffectsPrompt.svelte) with a stand-in /api/effects that follows the route contract
// (src/kitty/web_effects.py): approve, execute (409 with the changed preconditions when a
// target moved) and deny. Every effect is synthetic.
import { mount } from "svelte";
import "/src/styles.css";
import { loadBuild } from "/src/lib/build.svelte.ts";
import { createBuildStub } from "./buildStub.js";
import EffectsPrompt from "/src/lib/EffectsPrompt.svelte";

const at = new Date().toISOString();
const agent = { kind: "agent", name: "omp", session: "agent-session-1" };
const effect = (id, kind, title, preview, executable, extra = {}) => ({
  id, kind, title, preview, executable, actor: agent, state: "proposed", digest: `digest-${id}`,
  payload: {}, preconditions: { target: { text_sha256: "ab12" } }, created_at: at, expires_at: at,
  approval_expires_at: null, handoff: null, events: [], ...extra,
});
const HANDOFF = "rika! never submits applications. Your approval is recorded; open the application in your browser, check it, and press Submit yourself. Then mark the job applied.";
const state = {
  pending: [
    effect("e-drop", "delete_block", "Delete a note block", {
      action: "Move this block and its children to Trash (restorable from Trash or History)", page: "Plans",
      type: "text", text: "drop this duplicate", reason: "duplicate", child_count: 1 }, true),
    effect("e-moved", "delete_block", "Delete a note block", {
      action: "Move this block and its children to Trash (restorable from Trash or History)", page: "Plans",
      type: "text", text: "edited since", child_count: 0 }, true),
    effect("e-submit", "submit_application", "Submit an application", {
      action: "Submit this application (you do it, in the browser)", company: "Synthetic Labs", title: "Intern",
      apply_url: "https://example.com/jobs/1/apply", reason: "deadline Friday" }, false, { handoff: HANDOFF }),
  ],
  recent: [],
};
const calls = [];
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const find = (id) => state.pending.find((item) => item.id === id);
const decided = (item, next) => {
  state.pending = state.pending.filter((other) => other.id !== item.id);
  const done = { ...item, state: next };
  state.recent.unshift(done);
  return done;
};

function post(path, body) {
  const item = find(body.id);
  if (!item) return json({ error: "Effect not found" }, 404);
  if (path !== "/api/effects/deny" && body.digest !== item.digest) {
    return json({ error: "The reviewed effect differs from this one; review it again" }, 409);
  }
  switch (path) {
    case "/api/effects/approve":
      item.state = "approved";
      if (!item.executable) decided(item, "approved");
      return json({ effect: item, notice: item.executable ? null : HANDOFF,
        pending: state.pending.filter((other) => other.state === "proposed"), recent: state.recent });
    case "/api/effects/execute":
      if (!item.executable) return json({ error: "submit_application has no executor" }, 400);
      if (item.id === "e-moved") {
        decided(item, "refused");
        return json({ error: "Something this effect depends on changed since it was proposed; review it again",
          problems: [{ precondition: "target" }] }, 409);
      }
      return json({ effect: decided(item, "executed"), result: { txn_id: "t1" }, ...state });
    case "/api/effects/deny":
      return json({ effect: decided(item, "denied"), ...state });
    default:
      return json({ error: "not in harness" }, 404);
  }
}

const build = createBuildStub();
window.fetch = async (url, init = {}) => {
  const answer = build.fetch(url, init);
  if (answer) return answer;
  const path = String(url);
  const body = init.body ? JSON.parse(init.body) : undefined;
  calls.push({ url: path, method: init.method ?? "GET", body });
  if (init.method === "POST") return post(path, body);
  if (path === "/api/effects") return json({ pending: state.pending.filter((item) => item.state === "proposed"), recent: state.recent });
  return json({ error: "not in harness" }, 404);
};

await loadBuild();
mount(EffectsPrompt, { target: document.getElementById("view") });
window.effectsHarness = {
  posts: () => calls.filter((call) => call.method === "POST").map((call) => [call.url, call.body]),
};
