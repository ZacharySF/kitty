// Apply → Health in a real browser against a real rika! server (`kitty-web --root` on a
// temporary workspace this test creates; synthetic and disposable). The test first checks
// the empty state, then writes synthetic apply_attempts rows (migration 054) straight into
// that workspace's SQLite file and checks the per-family rates, causes and correctness.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/applyHealth.browser.test.mjs
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { startHarness, startKitty } from "./kittyServer.js";

const repository = fileURLToPath(new URL("../..", import.meta.url));
let root;
let kitty;
let vite;
let browser;
let base;

// Synthetic jobs only. greenhouse: 25 live jobs, 10 ready (Wilson 95%: 0.2340–0.5926), plus one
// closed posting; lever: 5 live jobs, 1 ready (below min_n = 20). Ready greenhouse attempts
// record 2 profile fields (20 compared, 1 edited) and 1 saved answer (10 compared).
const SEED = String.raw`
import json, sys
from kitty.core import jobs as jobs_core
from kitty.db import connect
db = connect(sys.argv[1])
now = "2026-11-01T10:00:00+00:00"
plan = [("greenhouse", "ready")] * 10 + [("greenhouse", "needs_owner")] * 15 + [("greenhouse", "closed")] \
     + [("lever", "ready")] + [("lever", "needs_owner")] * 4
db.execute("BEGIN")
for number, (family, outcome) in enumerate(plan):
    job = jobs_core.upsert_job(db, company="Synthetic Health Co", title=f"Synthetic role {number}",
                               url=f"https://{family}.example.test/jobs/{number}")[0].id
    cause = {"ready": "ready", "needs_owner": "missing_answer", "closed": "closed"}[outcome]
    stage = {"ready": "review", "needs_owner": "page", "closed": "live-check"}[outcome]
    attempt = db.execute(
        "INSERT INTO apply_attempts (job_id, ats_family, tenant_origin, started_at, ended_at, duration_ms, stage,"
        " page_number, page_name, outcome, cause, failing_fields, owner_inputs, hide_automation)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
        (job, family, f"https://{family}.example.test", now, now, 4200, stage,
         1 if stage == "page" else None, "Application" if stage == "page" else None, outcome, cause,
         json.dumps(["Work authorization"] if outcome == "needs_owner" else [])),
    ).lastrowid
    if family == "greenhouse" and outcome == "ready":
        fields = [(0, "First name", "profile"), (1, "Email", "profile"), (2, "Why this team", "saved_answer")]
        for position, label, provenance in fields:
            db.execute(
                "INSERT INTO apply_attempt_fields (attempt_id, position, label, provenance, ready_hash) VALUES (?, ?, ?, ?, ?)",
                (attempt, position, label, provenance, f"synthetic-hash-{attempt}-{position}"),
            )
            edited = 1 if (number == 0 and position == 0) else 0
            db.execute(
                "INSERT INTO apply_field_checks (attempt_id, position, edited, confirmed_at) VALUES (?, ?, ?, ?)",
                (attempt, position, edited, now),
            )
db.execute("COMMIT")
`;

function seed() {
  const result = spawnSync("uv", ["run", "--quiet", "--project", repository, "python", "-", join(root, "data", "apply.db")], {
    cwd: repository, input: SEED, encoding: "utf8",
  });
  assert.equal(result.status, 0, `seeding failed:\n${result.stdout}\n${result.stderr}`);
}

/** A fresh returning-user page on Find jobs → List → Preparation → Health. */
async function openHealth() {
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 } });
  await kitty.authorize(context);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue());
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("#jobs", base).href);
  const views = page.getByRole("navigation", { name: "Jobs view" });
  await views.waitFor({ timeout: 60_000 });
  await views.getByRole("button", { name: "List", exact: true }).click();
  await page.getByRole("button", { name: "Preparation", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "Prepare applications" });
  await drawer.waitFor();
  const toggle = drawer.getByRole("button", { name: "Health", exact: true });
  assert.equal(await toggle.getAttribute("aria-expanded"), "false");
  const loaded = page.waitForResponse((response) => response.url().includes("/api/apply/health"));
  await toggle.click();
  assert.equal((await loaded).status(), 200);
  assert.equal(await toggle.getAttribute("aria-expanded"), "true");
  const panel = drawer.getByRole("region", { name: "Apply health" });
  await panel.waitFor();
  await panel.getByRole("heading", { name: "Apply health", level: 3 }).waitFor();
  return { page, panel, problems, context };
}

before(async () => {
  root = mkdtempSync(join(tmpdir(), "kitty-apply-health-test-"));
  mkdirSync(join(root, "config"));
  // The applyai module needs its config files: the public examples, never runtime YAML.
  for (const name of readdirSync(join(repository, "config"))) {
    if (name.endsWith(".yaml.example")) copyFileSync(join(repository, "config", name), join(root, "config", name.slice(0, -".example".length)));
  }
  writeFileSync(join(root, "config", "build.json"), `${JSON.stringify({ schema: 1, modules: ["applyai", "workspace"] }, null, 2)}\n`);
  kitty = await startKitty({ root });
  vite = await startHarness(kitty.origin);
  base = vite.resolvedUrls.local[0];
  // The jobs map is WebGL-capable: software rendering where there is no GPU.
  browser = await chromium.launch({ args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
});
after(async () => {
  await browser?.close();
  await vite?.close();
  await kitty?.stop();
  if (root) rmSync(root, { recursive: true, force: true });
});

test("a workspace without apply attempts shows the empty state", async () => {
  const { panel, problems, context } = await openHealth();
  await panel.getByText("No apply attempts recorded yet").waitFor();
  assert.equal(await panel.getByRole("table").count(), 0);
  assert.deepEqual(problems, []);
  await context.close();
});

test("per-family ready rates with Wilson intervals, insufficient samples, top causes and correctness", async () => {
  seed();
  const { panel, problems, context } = await openHealth();

  // Overall: 11 ready of 30 live jobs (the closed posting is removed): 37% (22–54%).
  assert.equal((await panel.locator("[data-overall]").innerText()).trim(), "37% (22–54%), n = 30");

  const families = panel.getByRole("table", { name: "By form provider" });
  await families.waitFor();
  assert.deepEqual(await families.getByRole("columnheader").allInnerTexts(), ["Provider", "Jobs", "Ready", "Ready rate", "Closed"]);
  const greenhouse = families.getByRole("row").filter({ has: panel.page().getByRole("rowheader", { name: "greenhouse", exact: true }) });
  assert.deepEqual((await greenhouse.getByRole("cell").allInnerTexts()).map((text) => text.trim()), ["25", "10", "40% (23–59%)", "1"]);
  const lever = families.getByRole("row").filter({ has: panel.page().getByRole("rowheader", { name: "lever", exact: true }) });
  assert.deepEqual((await lever.getByRole("cell").allInnerTexts()).map((text) => text.trim()), ["5", "1", "insufficient data (n = 5)", "0"]);
  // Sorted by jobs, most first.
  assert.deepEqual(await families.getByRole("rowheader").allInnerTexts(), ["greenhouse", "lever"]);

  // Top causes: 19 live never-ready jobs whose latest attempt needs an answer.
  const causes = panel.getByRole("table", { name: "Top causes" });
  const missing = causes.getByRole("row").filter({ has: panel.page().getByRole("rowheader", { name: "Missing answer", exact: true }) });
  assert.deepEqual((await missing.getByRole("cell").allInnerTexts()).map((text) => text.trim()), ["Needs you", "19"]);
  assert.equal(await missing.locator('[data-outcome="needs_owner"]').count(), 1);

  // Correctness: profile 19 of 20 kept (Wilson 0.7639–0.9911); saved answers below 20 compared.
  const correctness = panel.getByRole("table", { name: "Fill correctness" });
  const profile = correctness.getByRole("row").filter({ has: panel.page().getByRole("rowheader", { name: "Profile", exact: true }) });
  assert.deepEqual((await profile.getByRole("cell").allInnerTexts()).map((text) => text.trim()), ["20", "1", "95% (76–99%)"]);
  const saved = correctness.getByRole("row").filter({ has: panel.page().getByRole("rowheader", { name: "Saved answer", exact: true }) });
  assert.deepEqual((await saved.getByRole("cell").allInnerTexts()).map((text) => text.trim()), ["10", "0", "insufficient data (n = 10)"]);

  assert.equal(await panel.getByText("No apply attempts recorded yet").count(), 0);
  assert.deepEqual(problems, []);
  await context.close();
});

test("an API failure is reported inline and Retry loads again", async () => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 } });
  await kitty.authorize(context);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue());
  let fail = true;
  await context.route("**/api/apply/health", (route) =>
    fail ? route.fulfill({ status: 500, json: { error: "Synthetic health failure" } }) : route.continue());
  const page = await context.newPage();
  await page.goto(new URL("#jobs", base).href);
  const views = page.getByRole("navigation", { name: "Jobs view" });
  await views.waitFor({ timeout: 60_000 });
  await views.getByRole("button", { name: "List", exact: true }).click();
  await page.getByRole("button", { name: "Preparation", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "Prepare applications" });
  await drawer.getByRole("button", { name: "Health", exact: true }).click();
  const panel = drawer.getByRole("region", { name: "Apply health" });
  const alert = panel.getByRole("alert");
  await alert.waitFor();
  assert.match(await alert.innerText(), /Synthetic health failure/);
  fail = false;
  await alert.getByRole("button", { name: "Retry" }).click();
  await panel.getByRole("table", { name: "By form provider" }).waitFor();
  assert.equal(await panel.getByRole("alert").count(), 0);
  await context.close();
});
