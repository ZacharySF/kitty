// Q8/Q9 builds in a real browser against a real kitty! server (`kitty-web --root` on a
// temporary workspace this test creates; synthetic and disposable). Two pages play two
// windows; the test writes config/build.json itself to play an outside editor.
// Run with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/builds.browser.test.mjs
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright-core";
import { startHarness, startKitty } from "./kittyServer.js";

// Screenshots for review land here when the folder can be made; the test never depends on them.
const SHOTS = process.env.KITTY_BUILD_SHOTS ?? "/tmp/kitty-crew/builds";
let root;
let kitty;
let vite;
let browser;
let base;
let context;
let windowA;
let windowB;
const problems = [];

const file = () => join(root, "config", "build.json");
const writeBuild = (build) => writeFileSync(file(), typeof build === "string" ? build : `${JSON.stringify(build, null, 2)}\n`);
const property = (page, name) => page.evaluate((name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim(), name);
const waitProperty = (page, name, value) => page.waitForFunction(([name, value]) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() === value, [name, value], { timeout: 20_000 });
const scopedCss = (page) => page.evaluate(() => document.getElementById("kitty-build-css")?.textContent ?? null);
const outline = (page) => page.evaluate(() => getComputedStyle(document.querySelector(".workbench")).outlineColor);
const editor = (page) => page.getByRole("textbox", { name: "Build file (JSON)" });
async function shot(page, name) {
  try {
    mkdirSync(SHOTS, { recursive: true });
    await page.screenshot({ path: join(SHOTS, `${name}.png`) });
  } catch { /* Optional evidence only. */ }
}

async function open(hash = "#settings") {
  const page = await context.newPage();
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(hash, base).href);
  await page.getByRole("heading", { name: "Settings", level: 1 }).waitFor({ timeout: 60_000 });
  return page;
}

before(async () => {
  root = mkdtempSync(join(tmpdir(), "kitty-builds-test-"));
  mkdirSync(join(root, "config"));
  writeBuild({ schema: 1, modules: ["workspace"] });
  kitty = await startKitty({ root });
  vite = await startHarness(kitty.origin);
  base = vite.resolvedUrls.local[0];
  browser = await chromium.launch();
  context = await browser.newContext({ viewport: { width: 1280, height: 850 } });
  // A returning user: the first-run welcome guide is covered by its own tests.
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue());
  windowA = await open();
  windowB = await open();
});
after(async () => {
  await browser?.close();
  await vite?.close();
  await kitty?.stop();
  if (root) rmSync(root, { recursive: true, force: true });
});

test("a token edited in the build editor restyles this window and the other one", async () => {
  await windowA.getByRole("button", { name: "Open build editor" }).click();
  const field = editor(windowA);
  await field.waitFor();
  // The workbench narrows beside the docked editor: a real split, not an overlay.
  const split = await windowA.evaluate(() => {
    const pane = document.querySelector('aside[aria-label="Build editor"]').getBoundingClientRect();
    const workbench = document.querySelector(".workbench").getBoundingClientRect();
    return { paneLeft: pane.left, workbenchRight: workbench.right };
  });
  assert.ok(split.workbenchRight <= split.paneLeft + 1, `workbench ends at ${split.workbenchRight}, editor starts at ${split.paneLeft}`);
  await field.fill(JSON.stringify({ schema: 1, modules: ["workspace"], theme: { tokens: { probe: "#123456", ink: "#ff0000" } } }, null, 2));
  for (const page of [windowA, windowB]) {
    await waitProperty(page, "--probe", "#123456");
    await waitProperty(page, "--br-ink", "#ff0000");
  }
  assert.match(readFileSync(file(), "utf8"), /"probe": "#123456"/);
  await windowA.locator('aside[aria-label="Build editor"] [role="status"]').filter({ hasText: "Saved" }).waitFor();
  // Custom style is on: the safe-mode control says so in both windows.
  for (const page of [windowA, windowB]) await page.locator("[data-safe-control]").getByText("Custom style on").waitFor();
});

test("an outside editor's save restyles both windows, drops removed tokens and fills the open editor", async () => {
  const css = ".workbench { outline: 3px solid rgb(0, 255, 0); }";
  writeBuild({ schema: 1, modules: ["workspace"], theme: { tokens: { probe2: "#abcdef" }, css } });
  for (const page of [windowA, windowB]) {
    await waitProperty(page, "--probe2", "#abcdef");
    await page.waitForFunction(() => getComputedStyle(document.querySelector(".workbench")).outlineColor === "rgb(0, 255, 0)");
    assert.equal(await property(page, "--probe"), "");
    assert.equal(await property(page, "--br-ink"), "#e6e6e6");
    assert.equal(await scopedCss(page), `@scope (#app) {\n${css}\n}`);
  }
  // The open editor had no unsaved edits, so it shows the file as written.
  await windowA.waitForFunction(() => document.querySelector('textarea[aria-label="Build file (JSON)"]').value.includes("probe2"));
});

test("errors: an invalid file is reported everywhere and an invalid edit is marked at its line; the last good look stays", async () => {
  const good = readFileSync(file(), "utf8");
  // Outside editor: broken JSON.
  writeBuild(good.replace('"modules"', '"modules" ['));
  await windowB.locator("[data-build-problem]").waitFor({ timeout: 20_000 });
  assert.match(await windowB.locator("[data-build-problem]").innerText(), /Build file error · line \d+/);
  await windowA.getByRole("list", { name: "Problems" }).waitFor();
  for (const page of [windowA, windowB]) {
    assert.equal(await property(page, "--probe2"), "#abcdef");
    assert.equal(await outline(page), "rgb(0, 255, 0)");
  }
  writeBuild(good);
  await windowB.locator("[data-build-problem]").waitFor({ state: "detached", timeout: 20_000 });

  // In-app: CSS with @import is refused at its line and never written.
  const lines = JSON.stringify({ schema: 1, modules: ["workspace"], theme: { tokens: { probe2: "#abcdef" }, css: [
    ".workbench { outline: 3px solid rgb(0, 255, 0); }", "@import url('https://example.invalid/x.css');",
  ] } }, null, 2).split("\n");
  const line = lines.findIndex((text) => text.includes("@import")) + 1;
  const field = editor(windowA);
  await field.fill(lines.join("\n"));
  await windowA.locator(`[data-problem-line="${line}"]`).waitFor();
  await field.press("Control+s");
  const problemsList = windowA.getByRole("list", { name: "Problems" });
  await problemsList.getByText(`Line ${line}:`).first().waitFor();
  await shot(windowA, "build-editor-error");
  // Clicking a problem moves the caret to its line.
  await problemsList.getByRole("button").first().click();
  const caretLine = await field.evaluate((node) => node.value.slice(0, node.selectionStart).split("\n").length);
  assert.equal(caretLine, line);
  await windowA.waitForTimeout(800);
  assert.equal(readFileSync(file(), "utf8"), good);
  for (const page of [windowA, windowB]) {
    assert.equal(await property(page, "--probe2"), "#abcdef");
    assert.equal(await outline(page), "rgb(0, 255, 0)");
  }
  await windowA.getByRole("button", { name: "Close build editor" }).click();
  assert.equal(await editor(windowA).count(), 0);
});

test("importing a build shows the diff with a restart flag, installs it everywhere and rolls back", async () => {
  const second = { schema: 1, name: "Second look", modules: ["workspace", "terminal"], theme: { base: "nord", tokens: { probe3: "#0000ff" }, css: "" } };
  const panel = windowA.locator('section[aria-label="Build settings"]');
  await panel.locator('input[type="file"]').setInputFiles({ name: "second.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(second)) });
  const modules = panel.locator('tr[data-path="modules"]');
  await modules.waitFor();
  assert.match(await modules.innerText(), /restart/i);
  assert.match(await panel.locator('tr[data-path="theme.css"]').innerText(), /1 line of CSS → no CSS/);
  await modules.scrollIntoViewIfNeeded();
  await shot(windowA, "build-import-diff");
  await panel.getByRole("button", { name: "Install" }).click();
  await panel.getByText("This build changes modules. They apply after you restart kitty").waitFor();
  for (const page of [windowA, windowB]) {
    await waitProperty(page, "--probe3", "#0000ff");
    await page.waitForFunction(() => document.documentElement.dataset.theme === "nord");
    assert.equal(await scopedCss(page), null);
    assert.equal(await property(page, "--probe2"), "");
  }
  await panel.getByRole("button", { name: "Roll back to previous build" }).click();
  for (const page of [windowA, windowB]) {
    await waitProperty(page, "--probe2", "#abcdef");
    await page.waitForFunction(() => document.documentElement.dataset.theme === "harness");
    assert.equal(await property(page, "--probe3"), "");
    assert.equal(await outline(page), "rgb(0, 255, 0)");
  }
});

test("safe mode (?safe and Ctrl+Alt+Shift+S) drops custom CSS and tokens; its control survives hostile CSS", async () => {
  const hostile = "* { display: none !important; }\n#app::after { content: ''; position: fixed; inset: 0; z-index: 2147483647; background: red; }";
  writeBuild({ schema: 1, modules: ["workspace"], theme: { tokens: { probe4: "#00ff00" }, css: hostile } });
  await waitProperty(windowA, "--probe4", "#00ff00");
  const control = windowA.locator("[data-safe-control]");
  await control.getByText("Custom style on").waitFor();
  // In the top layer, above anything the custom CSS draws.
  const onTop = await control.evaluate((node) => {
    const box = node.getBoundingClientRect();
    return !!document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)?.closest("[data-build-status]");
  });
  assert.ok(onTop, "the safe-mode control is covered");
  await shot(windowA, "safe-control-over-hostile-css");

  await windowA.keyboard.press("Control+Alt+Shift+S");
  await control.getByText("Safe mode: custom style off").waitFor();
  assert.equal(await scopedCss(windowA), null);
  assert.equal(await property(windowA, "--probe4"), "");
  assert.equal(new URL(windowA.url()).searchParams.has("safe"), true);
  await control.getByRole("button", { name: "Turn back on" }).click();
  await waitProperty(windowA, "--probe4", "#00ff00");
  assert.match(await scopedCss(windowA), /^@scope \(#app\)/);
  assert.equal(new URL(windowA.url()).searchParams.has("safe"), false);

  // ?safe opens one window without the custom look; the other keeps it.
  await windowB.goto(new URL("?safe#settings", base).href);
  await windowB.locator("[data-safe-control]").getByText("Safe mode: custom style off").waitFor({ timeout: 60_000 });
  assert.equal(await scopedCss(windowB), null);
  assert.equal(await property(windowB, "--probe4"), "");
  assert.equal(await property(windowA, "--probe4"), "#00ff00");
  assert.deepEqual(problems, []);
});
