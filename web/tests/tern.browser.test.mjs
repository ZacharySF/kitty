// Tern view states (docs/terminal-dock-design.md, Tern) in a real browser against a
// stand-in API; no tern binary or daemon is involved.
// Run with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/tern.browser.test.mjs
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

async function harness(state) {
  const context = await browser.newContext({ viewport: { width: 1000, height: 800 } });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(`tests/tern-harness.html?state=${state}`, base).href);
  await page.waitForFunction(() => !!window.ternHarness);
  const view = page.getByRole("region", { name: "Stencil Tern" });
  await view.waitFor();
  return { page, view, problems, context };
}
const posts = (page) => page.evaluate(() => window.ternHarness.posts().map(({ url, headers, body }) => ({ url, headers, body })));
/** A status panel, never a fake terminal: no canvas, frame or xterm in the view. */
async function noTerminal(view) {
  assert.equal(await view.locator("canvas, iframe, .xterm, [data-kitty-terminal]").count(), 0);
}

test("without Tern installed: an install pointer, no Open action and nothing started", async () => {
  const { page, view, problems, context } = await harness("absent");
  await view.getByText("Tern is not installed").waitFor();
  const link = view.getByRole("link", { name: /stencil\.so\/tern/ });
  assert.equal(await link.getAttribute("href"), "https://stencil.so/tern");
  assert.equal(await view.getByRole("button", { name: "Open in Tern" }).count(), 0);
  assert.ok(await view.getByText(/herdr stays kitty!'s built-in terminal/).isVisible());
  await view.getByRole("button", { name: "Check again" }).click();
  await page.waitForFunction(() => window.ternHarness.calls.filter((call) => call.method === "GET").length >= 2);
  assert.deepEqual(await posts(page), []);
  await noTerminal(view);
  assert.deepEqual(problems, []);
  await context.close();
});

test("installed but stopped: Open in Tern starts kitty!'s session and shows its panes", async () => {
  const { page, view, problems, context } = await harness("stopped");
  await view.getByText("Tern 0.9.8").waitFor();
  assert.ok(await view.getByText("/home/user/.local/bin/tern").isVisible());
  assert.ok(await view.getByText(/kitty!'s Tern session is not running/).isVisible());
  await page.evaluate(() => window.ternHarness.reply("/api/terminal/tern/open", "running"));
  await view.getByRole("button", { name: "Open in Tern" }).click();
  const sessions = view.getByRole("list", { name: "kitty!'s Tern sessions" });
  await sessions.waitFor();
  const [open] = await posts(page);
  assert.equal(open.url, "/api/terminal/tern/open");
  assert.deepEqual(open.body, {});
  assert.equal(open.headers["X-Kitty-Request"], "1");
  const working = sessions.getByRole("listitem").filter({ hasText: "cargo test" });
  assert.match(await working.textContent(), /working/);
  assert.match(await working.textContent(), /160×45/);
  assert.match(await sessions.getByRole("listitem").filter({ hasText: "notes" }).textContent(), /idle/);
  assert.ok(await view.getByText("1 Tern window open").isVisible());
  assert.ok(await view.getByText(/TERN_DAEMON_SOCKET=\/run\/user\/1000\/kitty\/tern-0123456789ab\/daemon\.sock tern ls/).isVisible());
  await noTerminal(view);
  assert.deepEqual(problems, []);
  await context.close();
});

test("installed but stopped: the session can start without a window", async () => {
  const { page, view, context } = await harness("stopped");
  await page.evaluate(() => window.ternHarness.reply("/api/terminal/tern/start", { ...window.ternHarness.STATES.running, windows: 0 }));
  await view.getByRole("button", { name: "Start kitty!'s Tern session" }).click();
  await view.getByRole("list", { name: "kitty!'s Tern sessions" }).waitFor();
  assert.deepEqual((await posts(page)).map((call) => call.url), ["/api/terminal/tern/start"]);
  assert.ok(await view.getByText("No Tern window open").isVisible());
  await context.close();
});

test("a broken path in settings is explained, with the install pointer and a way to fix it", async () => {
  const { page, view, problems, context } = await harness("broken");
  const alert = view.getByRole("alert");
  await alert.waitFor();
  assert.match(await alert.textContent(), /not an executable file: \/opt\/tern\/tern/);
  assert.equal(await view.getByRole("link", { name: /stencil\.so\/tern/ }).getAttribute("href"), "https://stencil.so/tern");
  assert.equal(await view.getByRole("button", { name: "Open in Tern" }).count(), 0);
  const field = view.getByLabel("Tern executable path");
  assert.equal(await field.inputValue(), "/opt/tern/tern");
  await page.evaluate(() => window.ternHarness.reply("/api/terminal/tern/settings", { ...window.ternHarness.STATES.stopped, source: "settings", override: "/opt/tern/bin/tern", path: "/opt/tern/bin/tern" }));
  await field.fill("/opt/tern/bin/tern");
  await view.getByRole("button", { name: "Save path" }).click();
  await view.getByText("Tern 0.9.8").waitFor();
  await page.evaluate(() => window.ternHarness.reply("/api/terminal/tern/settings", "stopped"));
  await view.getByRole("button", { name: "Use tern on PATH" }).click();
  await page.waitForFunction(() => window.ternHarness.posts().length === 2);
  assert.deepEqual((await posts(page)).map((call) => call.body), [{ path: "/opt/tern/bin/tern" }, { path: "" }]);
  assert.deepEqual(problems, []);
  await context.close();
});

test("a failed Open is reported, not hidden", async () => {
  const { page, view, context } = await harness("stopped");
  await page.evaluate(() => window.ternHarness.reply("/api/terminal/tern/open", { status: 503, error: "kitty!'s Tern daemon did not start" }));
  await view.getByRole("button", { name: "Open in Tern" }).click();
  const alert = view.getByRole("alert");
  await alert.waitFor();
  assert.match(await alert.textContent(), /did not start/);
  await noTerminal(view);
  await context.close();
});

test("the kitty! plugin for Tern installs and removes from the Tern view", async () => {
  const { page, view, context } = await harness("stopped");
  const state = view.locator("[data-plugin-state]");
  await view.locator('[data-plugin-state="install"]').waitFor();
  assert.equal((await state.innerText()).trim(), "Not installed in Tern");
  await view.getByRole("button", { name: "Install kitty! plugin into Tern" }).click();
  await view.locator('[data-plugin-state="installed"]').waitFor();
  assert.equal((await state.innerText()).trim(), "Installed (0.1.0) · Tern reports: ready");
  await view.getByRole("button", { name: "Remove from Tern" }).click();
  await view.locator('[data-plugin-state="install"]').waitFor();
  assert.deepEqual((await posts(page)).map((call) => call.url),
    ["/api/terminal/tern/plugin/install", "/api/terminal/tern/plugin/remove"]);
  await context.close();
});

test("without Tern there is no plugin panel and nothing asks Tern", async () => {
  const { page, view, context } = await harness("absent");
  await view.getByText("Tern is not installed").waitFor();
  assert.equal(await view.getByText("kitty! plugin for Tern").count(), 0);
  assert.ok(!(await page.evaluate(() => window.ternHarness.calls.some((call) => call.url.includes("/plugin")))));
  await context.close();
});
