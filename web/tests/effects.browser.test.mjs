// The pending-approvals prompt (EffectsPrompt.svelte) in a real browser against a stand-in
// /api/effects (tests/effectsHarness.js); every effect is synthetic.
// Run with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/effects.browser.test.mjs
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("..", import.meta.url));
let server;
let browser;
let base;

before(async () => {
  server = await createServer({ root, logLevel: "error", server: { host: "127.0.0.1", port: 0 } });
  await server.listen();
  base = server.resolvedUrls.local[0];
  browser = await chromium.launch();
});
after(async () => {
  await browser?.close();
  await server?.close();
});

test("approving runs the exact digest, a changed target is refused by name, and a submission only records the decision", async () => {
  const context = await browser.newContext({ viewport: { width: 1000, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("tests/effects-harness.html", base).href, { timeout: 90000 });
  const cards = page.locator('[aria-label^="Approval request"]');
  await cards.nth(2).waitFor({ timeout: 60000 });

  // Multi-line cards, not the one-line notice pill they share their look with.
  const radius = await cards.first().evaluate((element) => parseFloat(getComputedStyle(element).borderTopLeftRadius));
  assert.ok(radius <= 20, `card radius ${radius}px`);

  const drop = cards.filter({ hasText: "drop this duplicate" });
  await drop.getByText("Agent · omp").waitFor();
  await drop.getByRole("button", { name: "Approve", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Done: Delete a note block." }).waitFor();

  await cards.filter({ hasText: "edited since" }).getByRole("button", { name: "Approve", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "(changed: target)" }).waitFor();

  const submit = page.locator('[data-kind="submit_application"]');
  await submit.getByText("You do this yourself; rika! never runs it").waitFor();
  await submit.getByRole("button", { name: "Approve (I'll submit it myself)" }).click();
  await page.getByRole("status").filter({ hasText: "never submits applications" }).waitFor();
  await cards.first().waitFor({ state: "detached" });

  assert.deepEqual(await page.evaluate(() => window.effectsHarness.posts()), [
    ["/api/effects/approve", { id: "e-drop", digest: "digest-e-drop" }],
    ["/api/effects/execute", { id: "e-drop", digest: "digest-e-drop" }],
    ["/api/effects/approve", { id: "e-moved", digest: "digest-e-moved" }],
    ["/api/effects/execute", { id: "e-moved", digest: "digest-e-moved" }],
    ["/api/effects/approve", { id: "e-submit", digest: "digest-e-submit" }],
  ]);
  assert.deepEqual(problems, []);
  await context.close();
});
