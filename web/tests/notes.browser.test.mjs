// Notes editor hierarchy and editing safety in a real browser against an
// in-memory stand-in for the notes API (tests/notesHarness.js; synthetic data).
// Run with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/notes.browser.test.mjs
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("..", import.meta.url));
// kitty!'s native window: default 1280×850, minimum 760×500 (desktop/src/main.rs).
const DEFAULT = { width: 1280, height: 850 };
const MINIMUM = { width: 760, height: 500 };
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

async function notes(viewport = DEFAULT, query = "") {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  // The first load pays Vite's cold transform of the whole Notes graph; give it time.
  await page.goto(new URL(`tests/notes-harness.html${query}`, base).href, { timeout: 60000 });
  await page.waitForFunction(() => !!window.notesHarness, null, { timeout: 60000 });
  await assert.doesNotReject(title(page).waitFor());
  await until(async () => (await title(page).inputValue()) !== "", "a page opened");
  return { page, problems, context };
}
async function until(check, what) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.fail(`timed out waiting until ${what}`);
}
const titleIs = (page, value) => until(async () => (await title(page).inputValue()) === value, `the page title is ${value}`);
const workspace = (page) => page.getByRole("region", { name: "Notes workspace" });
const save = (page) => page.getByRole("button", { name: "Save / retry", exact: true });
const openTab = (page, name) => page.getByRole("navigation", { name: "Open note pages" }).getByRole("button", { name, exact: true });
const title = (page) => page.getByRole("textbox", { name: "Page title" });
const block = (page, n) => page.getByRole("textbox", { name: `Block ${n}`, exact: true });
const modes = (page) => page.getByRole("group", { name: "Page mode" });
const mode = (page, name) => modes(page).getByRole("button", { name, exact: true });
// The library is a slide-over drawer (Q43): open it before using it.
async function library(page) {
  const toggle = page.getByRole("button", { name: "Open library" });
  if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
  return page.getByRole("navigation", { name: "Folders, notebooks and pages" });
}
const more = (page) => page.locator("summary", { hasText: /^More/ });
const box = async (locator) => locator.boundingBox();
async function pressedModes(page) {
  const result = [];
  for (const name of ["Writing", "Canvas", "Mind map"]) if (await mode(page, name).getAttribute("aria-pressed") === "true") result.push(name);
  return result;
}

for (const [label, viewport] of [["default", DEFAULT], ["minimum", MINIMUM]]) {
  test(`writing starts directly under one compact editor toolbar at the ${label} native size`, async () => {
    const { page, problems, context } = await notes(viewport);
    await assert.doesNotReject(block(page, 1).waitFor());
    assert.equal(await block(page, 1).inputValue(), "A hash ring maps each key to the next node clockwise.");
    const section = await box(workspace(page));
    const first = await box(block(page, 1));
    // S08: the outline began ~390px into the Notes section, below the heading,
    // a notebook strip, page tabs, the mode/title row and an outline help row.
    assert.ok(first.y - section.y <= 150, `writing starts ${Math.round(first.y - section.y)}px into Notes`);
    assert.ok(first.y + first.height <= viewport.height, "the first written block is visible without scrolling");
    // Mode, page identity and save share a single toolbar row.
    const [modeBox, titleBox, saveBox] = [await box(modes(page)), await box(title(page)), await box(save(page))];
    for (const control of [modeBox, saveBox]) {
      const middle = control.y + control.height / 2;
      assert.ok(middle > titleBox.y && middle < titleBox.y + titleBox.height, "mode, title and save are on one row");
    }
    assert.deepEqual(await pressedModes(page), ["Writing"]);
    assert.deepEqual(problems, []);
    await context.close();
  });
}

test("the library is the single notebook and page navigator", async () => {
  const { page, problems, context } = await notes();
  assert.equal(await page.getByRole("navigation", { name: "Notebooks", exact: true }).count(), 0);
  // Notebook rows read "⌄ Systems design"; their "Edit notebook …" menu buttons must not match.
  await assert.doesNotReject((await library(page)).getByRole("button", { name: /^\W*Systems design$/ }).waitFor());
  await assert.doesNotReject((await library(page)).getByRole("button", { name: /^\W*Reading log$/ }).waitFor());
  await (await library(page)).getByRole("button", { name: "Raft notes" }).click();
  await titleIs(page, "Raft notes");
  assert.equal(await block(page, 1).inputValue(), "A leader replicates log entries to followers.");
  assert.deepEqual(problems, []);
  await context.close();
});

test("infrequent page actions and editing help sit behind a labelled More panel", async () => {
  const { page, problems, context } = await notes();
  const panelItems = [
    page.getByRole("button", { name: "Reload saved" }),
    page.getByRole("button", { name: "Delete page" }),
    page.getByRole("button", { name: /Favorite/ }),
    page.getByRole("combobox", { name: /^Paper/ }),
    page.getByText("Enter new bullet"),
  ];
  for (const item of panelItems) assert.equal(await item.isVisible(), false);
  await more(page).focus();
  await page.keyboard.press("Enter");
  for (const item of panelItems) assert.equal(await item.isVisible(), true);
  await page.keyboard.press("Escape");
  for (const item of panelItems) assert.equal(await item.isVisible(), false);
  assert.deepEqual(problems, []);
  await context.close();
});

test("notes views stay one click away and the open page tab returns to the editor", async () => {
  const { page, problems, context } = await notes();
  const views = page.locator("summary", { hasText: /^Views/ });
  await views.click();
  await page.getByRole("button", { name: "Library overview" }).click();
  assert.equal(await title(page).isVisible(), false);
  await openTab(page, "Consistent hashing").click();
  await assert.doesNotReject(title(page).waitFor());
  assert.equal(await block(page, 1).isVisible(), true);
  assert.deepEqual(problems, []);
  await context.close();
});

test("mode switching shows one active mode with its matching surface and keeps canvas shortcuts", async () => {
  const { page, problems, context } = await notes();
  const canvas = page.getByLabel(/^Infinite drawing canvas/);
  await mode(page, "Canvas").focus();
  await page.keyboard.press("Space");
  assert.deepEqual(await pressedModes(page), ["Canvas"]);
  assert.equal(await canvas.isVisible(), true);
  assert.equal(await block(page, 1).isVisible(), false);
  await canvas.focus();
  await page.keyboard.press("e");
  assert.equal(await page.getByRole("toolbar", { name: "Drawing tools" }).getByRole("button", { name: "Eraser" }).getAttribute("aria-pressed"), "true");
  await mode(page, "Mind map").click();
  assert.deepEqual(await pressedModes(page), ["Mind map"]);
  assert.equal(await page.getByRole("button", { name: "+ Concept" }).first().isVisible(), true);
  await mode(page, "Writing").click();
  assert.deepEqual(await pressedModes(page), ["Writing"]);
  assert.equal(await canvas.isVisible(), false);
  assert.equal(await block(page, 1).isVisible(), true);
  assert.deepEqual(problems, []);
  await context.close();
});

test("the mind-map module opens in mind-map mode with its canvas in view at the minimum size", async () => {
  const { page, problems, context } = await notes(MINIMUM, "?mode=mindmap");
  assert.deepEqual(await pressedModes(page), ["Mind map"]);
  const canvas = await box(page.getByLabel(/^Infinite drawing canvas/));
  assert.ok(canvas.y < MINIMUM.height - 120, `the mind map starts at ${Math.round(canvas.y)}px`);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Ctrl+S saves title and writing, and the page reopens with them", async () => {
  const { page, problems, context } = await notes();
  await title(page).fill("Consistent hashing (revised)");
  await block(page, 1).fill("Each key belongs to the next node clockwise on the ring.");
  await page.keyboard.press("Control+s");
  await page.waitForFunction(() => window.notesHarness.saved(1).title === "Consistent hashing (revised)");
  assert.equal(await page.evaluate(() => window.notesHarness.saved(1).blocks[0].text), "Each key belongs to the next node clockwise on the ring.");
  await (await library(page)).getByRole("button", { name: "Raft notes" }).click();
  await titleIs(page, "Raft notes");
  await openTab(page, "Consistent hashing (revised)").click();
  await titleIs(page, "Consistent hashing (revised)");
  assert.equal(await block(page, 1).inputValue(), "Each key belongs to the next node clockwise on the ring.");
  assert.deepEqual(problems, []);
  await context.close();
});

test("an unsaved edit blocks switching pages until it saves", async () => {
  const { page, problems, context } = await notes();
  await page.evaluate(() => { window.notesHarness.failSaves = true; });
  await block(page, 1).fill("Draft that must not be lost.");
  await (await library(page)).getByRole("button", { name: "Raft notes" }).click();
  await assert.doesNotReject(page.getByRole("alert").filter({ hasText: "Synthetic save failure" }).waitFor());
  assert.equal(await title(page).inputValue(), "Consistent hashing");
  assert.equal(await block(page, 1).inputValue(), "Draft that must not be lost.");
  await page.evaluate(() => { window.notesHarness.failSaves = false; });
  await save(page).click();
  await page.waitForFunction(() => window.notesHarness.saved(1).blocks[0].text === "Draft that must not be lost.");
  assert.deepEqual(problems, []);
  await context.close();
});
