// Browser fixture for tests/backup.browser.test.mjs. Mounts Settings → Backup with a stand-in
// /api/backup and /api/reposync that follow the route contracts (src/kitty/web_backup.py,
// src/kitty/web_reposync.py); nothing is written to disk.
import { mount } from "svelte";
import "/src/styles.css";
import "/src/lib/settingsRows.css";
import { loadBuild } from "/src/lib/build.svelte.ts";
import { createBuildStub } from "./buildStub.js";
import BackupSettings from "/src/lib/BackupSettings.svelte";

const ROOT = "/home/synthetic/workspace";
const state = { folder: "/home/synthetic/rika-backups", root: ROOT, last: null };
const counts = { "Note pages": 12, "Note attachments": 3, "Calendar events": 4, "Tasks": 6, "Flashcards": 40,
  "Flashcard reviews": 118, "Jobs": 9, "Applications": 2 };
const excluded = [{ category: "Keys and passwords", reason: "stay in the keyring" }, { category: "Sign-ins", reason: "sign in again" }];
const calls = [];
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const github = { enabled: true, reason: null, repo: null, private: null, paused: false, problem: null, running: false,
  synced_at: null, pages: 0, last_run: null, next_run: null };

function post(path, body) {
  switch (path) {
    case "/api/backup/create": {
      const made = { path: `${body.folder}/rika-backup-20261117-090000.zip`, bytes: 5_242_880, created_at: "2026-11-17T09:00:00+00:00",
        counts, files: { count: 7, bytes: 4_000_000 }, excluded, missing: [] };
      state.last = made;
      return json(made);
    }
    case "/api/backup/preview": {
      const problems = body.into === ROOT ? ["That is the open workspace; restore into a new, empty folder"] : [];
      return json({ archive: body.archive, format: 1, app_version: "0.1.0", created_at: "2026-11-17T09:00:00+00:00", counts,
        files: { count: 7, bytes: 4_000_000 }, excluded, missing: [], problems, can_restore: problems.length === 0, into: body.into,
        warnings: ["Not in this backup: Keys and passwords, Sign-ins"] });
    }
    case "/api/backup/restore":
      return json({ path: body.into, archive: body.archive, counts, files: { count: 7, bytes: 4_000_000 }, warnings: [],
        excluded, relocated: {}, open: `kitty-workspace --workspace ${body.into}`, open_cli: `KITTY_HOME=${body.into} kitty-web` });
    default: return json({ error: "not in harness" }, 404);
  }
}

const build = createBuildStub();
window.fetch = async (url, init = {}) => {
  const answer = build.fetch(url, init);
  if (answer) return answer;
  const path = String(url);
  calls.push({ url: path, method: init.method ?? "GET", headers: init.headers ?? {}, body: init.body ? JSON.parse(init.body) : undefined });
  if (init.method === "POST") return post(path, JSON.parse(init.body));
  if (path === "/api/backup") return json(state);
  if (path === "/api/reposync") return json(github);
  return json({ error: "not in harness" }, 404);
};

await loadBuild();
mount(BackupSettings, { target: document.getElementById("view") });
window.backupHarness = { state, calls, posts: () => calls.filter((call) => call.method === "POST").map((call) => [call.url, call.body]) };
