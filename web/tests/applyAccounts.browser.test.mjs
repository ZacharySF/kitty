// Settings → Apply → Accounts in a real browser against a real rika! server (`kitty-web --root`
// on a temporary workspace this test creates; synthetic and disposable, KITTY_KEYRING=none so
// passwords live in the workspace's owner-only files). The test seeds synthetic registry rows
// (migration 055) and one synthetic generated password, then checks the grants, the rows, the
// file-fallback warning and that no password ever reaches the page.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/applyAccounts.browser.test.mjs
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
const PASSWORD = "Synthetic-Generated#4821Qz";
let root;
let kitty;
let vite;
let browser;
let base;

const SEED = String.raw`
import sys
from pathlib import Path
from kitty.core import keys
from kitty.core.apply import accounts
from kitty.db import connect
root, password = Path(sys.argv[1]), sys.argv[2]
db = connect(root / "data" / "apply.db")
for origin, state in [("https://synthetic.wd5.myworkdayjobs.com", "active"),
                      ("https://careers-synthetic.icims.com", "locked")]:
    family = "workday" if "workday" in origin else "icims"
    accounts.ensure_account(db, family, origin, "pat.synthetic@example.invalid", "standard")
    accounts.set_state(db, origin, state)
keys.Keys(root, store=None, use_env=False).set(accounts.key_name("https://synthetic.wd5.myworkdayjobs.com"), password)
`;

function seed() {
  const result = spawnSync("uv", ["run", "--quiet", "--project", repository, "python", "-", root, PASSWORD], {
    cwd: repository, input: SEED, encoding: "utf8", env: { ...process.env, KITTY_KEYRING: "none" },
  });
  assert.equal(result.status, 0, `seeding failed:\n${result.stdout}\n${result.stderr}`);
}

async function open(hash) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 } });
  await kitty.authorize(context);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue());
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(`#${hash}`, base).href);
  return { page, problems, context };
}

before(async () => {
  root = mkdtempSync(join(tmpdir(), "kitty-apply-accounts-test-"));
  mkdirSync(join(root, "config"));
  for (const name of readdirSync(join(repository, "config"))) {
    if (name.endsWith(".yaml.example")) copyFileSync(join(repository, "config", name), join(root, "config", name.slice(0, -".example".length)));
  }
  writeFileSync(join(root, "config", "build.json"), `${JSON.stringify({ schema: 1, modules: ["applyai", "workspace"] }, null, 2)}\n`);
  kitty = await startKitty({ root });
  vite = await startHarness(kitty.origin);
  base = vite.resolvedUrls.local[0];
  browser = await chromium.launch({ args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
});
after(async () => {
  await browser?.close();
  await vite?.close();
  await kitty?.stop();
  if (root) rmSync(root, { recursive: true, force: true });
});

test("an empty registry lists the four family grants, none granted", async () => {
  const { page, problems, context } = await open("settings/apply-accounts");
  const panel = page.getByRole("region", { name: "Accounts", exact: true });
  await panel.getByText("No employer accounts yet.", { exact: false }).waitFor();
  const switches = panel.getByRole("switch");
  assert.deepEqual(await switches.evaluateAll((nodes) => nodes.map((node) => [node.getAttribute("aria-label"), node.getAttribute("aria-checked")])), [
    ["Workday grant", "false"], ["Oracle Recruiting Cloud grant", "false"], ["Eightfold grant", "false"], ["iCIMS grant", "false"],
  ]);
  await panel.getByText("https://*.myworkdayjobs.com/*", { exact: false }).waitFor();
  await panel.getByText("https://careers-*.icims.com/*", { exact: false }).waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("granting a family is one confirmed switch; registry rows show state and storage, never a password", async () => {
  seed();
  const { page, problems, context } = await open("settings/apply-accounts");
  const panel = page.getByRole("region", { name: "Accounts", exact: true });
  const rows = panel.getByRole("list", { name: "Employer accounts" }).getByRole("listitem");
  await rows.first().waitFor();
  assert.equal(await rows.count(), 2);
  const workday = rows.filter({ hasText: "synthetic.wd5.myworkdayjobs.com" });
  assert.match(await workday.innerText(), /Signed in/);
  assert.match(await workday.innerText(), /Password: standard · owner-only file/);
  assert.match(await workday.innerText(), /pat\.synthetic@example\.invalid/);
  const icims = rows.filter({ hasText: "careers-synthetic.icims.com" });
  assert.match(await icims.innerText(), /Locked by the employer/);
  assert.equal(await icims.getAttribute("data-state"), "locked");
  // No keyring in this workspace: the owner-only file fallback is visible.
  await panel.getByRole("note").filter({ hasText: "owner-only files" }).waitFor();

  let asked = "";
  page.once("dialog", (dialog) => { asked = dialog.message(); void dialog.accept(); });
  const toggle = panel.getByRole("switch", { name: "Workday grant" });
  const saved = page.waitForResponse((response) => response.url().includes("/api/apply/accounts/grant"));
  await toggle.click();
  assert.equal((await saved).status(), 200);
  assert.match(asked, /Workday/);
  assert.match(asked, /Submit always stays yours/);
  await page.waitForFunction(() => document.querySelector('[aria-label="Workday grant"]')?.getAttribute("aria-checked") === "true");
  const state = await kitty.request("/apply/accounts");
  assert.equal(state.grants.find((grant) => grant.family === "workday").granted, true);

  // Declining the confirmation changes nothing.
  page.once("dialog", (dialog) => void dialog.dismiss());
  await panel.getByRole("switch", { name: "iCIMS grant" }).click();
  assert.equal(await panel.getByRole("switch", { name: "iCIMS grant" }).getAttribute("aria-checked"), "false");

  // Revoking needs no confirmation.
  await toggle.click();
  await page.waitForFunction(() => document.querySelector('[aria-label="Workday grant"]')?.getAttribute("aria-checked") === "false");

  assert.ok(!(await page.content()).includes(PASSWORD), "a password reached the page");
  assert.ok(!JSON.stringify(state).includes(PASSWORD), "a password reached the API");
  assert.deepEqual(problems, []);
  await context.close();
});

test("the Keys page warns that account passwords sit in owner-only files and never lists them", async () => {
  const { page, problems, context } = await open("settings/keys");
  const panel = page.getByRole("region", { name: "Keys", exact: true });
  await panel.getByRole("alert").filter({ hasText: "employer account passwords are stored in owner-only files" }).waitFor();
  await panel.getByText("1 employer account password", { exact: false }).waitFor();
  assert.equal(await panel.locator('[data-key^="APPLY_PW_"]').count(), 0);
  assert.ok(!(await page.content()).includes(PASSWORD));
  assert.deepEqual(problems, []);
  await context.close();
});
