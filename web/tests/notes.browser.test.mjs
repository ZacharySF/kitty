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
import { THEMES } from "../src/lib/themes/catalog.ts";
import { textContrast } from "./textContrast.js";

const root = fileURLToPath(new URL("..", import.meta.url));
// rika!'s native window: default 1280×850, minimum 760×500 (desktop/src/main.rs).
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
  // The title lives in the Write page; Canvas and Map hide it, so wait for it to exist, not show.
  await assert.doesNotReject(title(page).waitFor({ state: "attached" }));
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
const openTab = (page, name) => page.getByRole("navigation", { name: "Open note pages" }).getByRole("button", { name, exact: true });
// Canvas and Map hide the Write page (and its H1 title) but keep it mounted.
const title = (page) => page.getByRole("textbox", { name: "Page title", includeHidden: true });
const block = (page, n) => page.getByRole("textbox", { name: `Block ${n}`, exact: true });
const modes = (page) => page.getByRole("group", { name: "Page mode" });
const mode = (page, name) => modes(page).getByRole("button", { name, exact: true });
const canvas = (page) => page.getByLabel(/^Infinite drawing canvas/);
const pageMap = (page) => page.getByRole("region", { name: "Page map" });
// Q6: rare page actions sit in the ⋯ menu, always the header's last control.
const more = (page) => page.getByRole("button", { name: "More page actions", exact: true });
const menuItem = (page, name) => page.getByRole("menu", { name: "More page actions" }).getByRole("menuitem", { name, exact: true });
async function choose(page, name) {
  await more(page).click();
  await menuItem(page, name).click();
}
// The library is a slide-over drawer (Q43): open it before using it.
async function library(page) {
  const toggle = page.getByRole("button", { name: "Open library" });
  if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
  return page.getByRole("navigation", { name: "Folders, notebooks and pages" });
}
const box = async (locator) => locator.boundingBox();
async function pressedModes(page) {
  const result = [];
  for (const name of ["Write", "Canvas", "Map"]) if (await mode(page, name).getAttribute("aria-pressed") === "true") result.push(name);
  return result;
}
/** Native dropdowns and fold-outs anywhere on the page (Q6, Q7: there are none). NoteReferences, the
 *  library's Connections list in its own component, still folds and is left to its own change. */
const natives = (page) => page.evaluate(() => ({ select: document.querySelectorAll("select").length, summary: document.querySelectorAll("summary:not(.references > summary)").length }));

for (const [label, viewport] of [["default", DEFAULT], ["minimum", MINIMUM]]) {
  test(`one header row holds the library, page tabs, Page mode, Find and ⋯ last, with the page under it at the ${label} native size`, async () => {
    const { page, problems, context } = await notes(viewport);
    await assert.doesNotReject(block(page, 1).waitFor());
    await until(async () => (await block(page, 1).textContent()) === "A hash ring maps each key to the next node clockwise.", "the first block shows");
    // Q17: ☰, the tabs, the Write/Canvas/Map switch, Find and ⋯ share one row.
    const header = page.locator(".notes-heading");
    const controls = {
      library: page.getByRole("button", { name: "Open library" }),
      tabs: page.getByRole("navigation", { name: "Open note pages" }),
      modes: modes(page),
      find: page.getByRole("button", { name: "Find in page", exact: true }),
      more: more(page),
    };
    const boxes = {};
    for (const [name, control] of Object.entries(controls)) boxes[name] = await box(control);
    const row = await box(header);
    for (const [name, control] of Object.entries(boxes)) {
      const middle = control.y + control.height / 2;
      assert.ok(middle > row.y && middle < row.y + row.height, `${name} is not in the header row`);
      assert.ok(Math.abs(middle - (boxes.more.y + boxes.more.height / 2)) <= 4, `${name} is off the row's centre line`);
    }
    assert.ok(row.height <= 52, `the header is ${row.height}px tall: more than one row`);
    assert.ok(boxes.library.x < boxes.tabs.x && boxes.tabs.x < boxes.modes.x && boxes.modes.x < boxes.find.x && boxes.find.x < boxes.more.x, "header controls are out of order");
    const last = await header.evaluate((node) => [...node.querySelectorAll("button, input, a[href]")].filter((item) => item.offsetParent).at(-1)?.getAttribute("aria-label"));
    assert.equal(last, "More page actions", "⋯ is not the last control in the header");
    // Q1: every header control (the segmented switch as one control) is 28px tall.
    for (const [name, control] of Object.entries(boxes)) if (name !== "tabs") assert.equal(Math.round(control.height), 28, `${name} is not 28px tall`);
    for (const tab of await controls.tabs.locator(".note-tab").all()) assert.equal(Math.round((await box(tab)).height), 28, "a page tab is not 28px tall");
    assert.deepEqual(await pressedModes(page), ["Write"]);
    // Nothing else stays in the bar: no "Notes" heading, status text, Save button or Views fold-out.
    assert.equal(await header.getByRole("heading").count(), 0, "the header still has a heading");
    assert.equal(await page.getByRole("button", { name: /^Save/ }).count(), 0, "a Save button is still shown");
    assert.equal(await page.getByText("Saved locally").count(), 0, "save status is still text");
    // Q5/Q17: the title is the page's Fraunces H1, borderless, with a meta line above it.
    const heading = page.getByRole("heading", { level: 1 });
    assert.equal(await heading.getByRole("textbox", { name: "Page title" }).count(), 1, "the title is not the page's H1");
    const look = await title(page).evaluate((node) => { const style = getComputedStyle(node); return { font: style.fontFamily, border: style.borderTopWidth, placeholder: node.placeholder }; });
    assert.match(look.font, /Fraunces/, "the title is not set in Fraunces");
    assert.equal(look.border, "0px", "the title is still a boxed field");
    assert.equal(look.placeholder, "Untitled");
    assert.match(await page.locator(".page-meta").textContent(), /^Systems design · edited /);
    const [titleBox, first] = [await box(title(page)), await box(block(page, 1))];
    assert.ok(titleBox.y > row.y + row.height && titleBox.y < first.y, "the title does not sit between the header and the blocks");
    const section = await box(workspace(page));
    assert.ok(first.y - section.y <= 190, `writing starts ${Math.round(first.y - section.y)}px into Notes`);
    assert.ok(first.y + first.height <= viewport.height, "the first written block is visible without scrolling");
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
  await until(async () => (await block(page, 1).textContent()) === "A leader replicates log entries to followers.", "the Raft page's block shows");
  assert.deepEqual(problems, []);
  await context.close();
});

test("rare page actions sit in the ⋯ menu, which follows the page mode", async () => {
  const { page, problems, context } = await notes();
  const writeItems = ["Today's page", "Library overview", "Import PDF or Markdown…", "Favourite", "Page history…", "Trash…", "Privacy & storage…", "Reload saved", "Delete page…"];
  assert.equal(await page.getByRole("menu", { name: "More page actions" }).count(), 0);
  await more(page).focus();
  await page.keyboard.press("Enter");
  const menu = page.getByRole("menu", { name: "More page actions" });
  await menu.waitFor();
  assert.deepEqual((await menu.getByRole("menuitem").allTextContents()).map((text) => text.trim()), writeItems);
  assert.equal(await menu.getByRole("separator").count(), 1, "Delete page… is not set apart");
  await page.keyboard.press("Escape");
  await menu.waitFor({ state: "hidden" });
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("aria-label")), "More page actions", "Escape does not return to ⋯");
  // Page history (the block backend's sessions and Restore) opens from ⋯.
  await choose(page, "Page history…");
  await page.getByRole("dialog", { name: /history/i }).waitFor();
  await page.keyboard.press("Escape");
  // In Canvas, Canvas fullscreen joins the menu.
  await mode(page, "Canvas").click();
  await more(page).click();
  await menuItem(page, "Canvas fullscreen").waitFor();
  await page.keyboard.press("Escape");
  assert.deepEqual(problems, []);
  await context.close();
});

test("Library overview and Today's page are one ⋯ away and the open tab returns to the editor", async () => {
  const { page, problems, context } = await notes();
  await choose(page, "Library overview");
  await until(async () => !(await title(page).isVisible()), "the overview replaced the page");
  await openTab(page, "Consistent hashing").click();
  await assert.doesNotReject(title(page).waitFor());
  assert.equal(await block(page, 1).isVisible(), true);
  await choose(page, "Today's page");
  await until(async () => (await page.evaluate(() => window.notesHarness.calls.some((call) => call.path === "/api/notes/daily"))), "Today's page asked for the daily note");
  assert.deepEqual(problems, []);
  await context.close();
});

test("Write, Canvas and Map show one active mode with its matching surface and keep canvas shortcuts", async () => {
  const { page, problems, context } = await notes();
  await mode(page, "Canvas").focus();
  await page.keyboard.press("Space");
  assert.deepEqual(await pressedModes(page), ["Canvas"]);
  assert.equal(await canvas(page).isVisible(), true);
  assert.equal(await block(page, 1).isVisible(), false);
  assert.equal(await title(page).isVisible(), false, "the H1 shows outside Write");
  await canvas(page).focus();
  await page.keyboard.press("e");
  assert.equal(await page.getByRole("toolbar", { name: "Drawing tools" }).getByRole("button", { name: "Eraser" }).getAttribute("aria-pressed"), "true");
  // Q19: Map is the one mind-map editor, not a second concept canvas.
  await mode(page, "Map").click();
  assert.deepEqual(await pressedModes(page), ["Map"]);
  await assert.doesNotReject(pageMap(page).getByRole("button", { name: "Create map" }).first().waitFor());
  assert.equal(await canvas(page).isVisible(), false, "Map mode still shows the drawing canvas");
  assert.equal(await page.getByRole("button", { name: "+ Concept" }).count(), 0);
  await mode(page, "Write").click();
  assert.deepEqual(await pressedModes(page), ["Write"]);
  assert.equal(await canvas(page).isVisible(), false);
  assert.equal(await pageMap(page).count(), 0);
  assert.equal(await block(page, 1).isVisible(), true);
  assert.deepEqual(problems, []);
  await context.close();
});

test("the mind-map module opens Notes in Map mode with the editor filling the stage at the minimum size", async () => {
  const { page, problems, context } = await notes(MINIMUM, "?mode=map");
  assert.deepEqual(await pressedModes(page), ["Map"]);
  await assert.doesNotReject(pageMap(page).getByRole("button", { name: "Create map" }).first().waitFor());
  const map = await box(pageMap(page));
  assert.ok(map.y < MINIMUM.height - 120, `the map starts at ${Math.round(map.y)}px`);
  assert.ok(map.y + map.height <= MINIMUM.height, "the map runs below the window");
  assert.equal(await canvas(page).isVisible(), false);
  // The page's own map is looked up, not a separate list of saved maps.
  assert.ok(await page.evaluate(() => window.notesHarness.calls.some((call) => call.path === "/api/notes/pages/1/map")), "Map mode did not load the page's map");
  assert.deepEqual(problems, []);
  await context.close();
});

test("the canvas stage ends above the window bottom and its pill ⋯ holds paper, layers and file actions", async () => {
  const { page, problems, context } = await notes();
  await mode(page, "Canvas").click();
  // The stage is sized to the window after the mode switch lays out; measure once it settles.
  await page.waitForFunction((min) => {
    const el = [...document.querySelectorAll("[aria-label]")].find((node) => /^Infinite drawing canvas/.test(node.getAttribute("aria-label")));
    return el && el.getBoundingClientRect().height >= min;
  }, 320, { timeout: 5000 }).catch(() => {});
  const stage = await box(canvas(page));
  assert.ok(stage.y + stage.height <= DEFAULT.height, `the canvas runs to ${Math.round(stage.y + stage.height)}px, under the window edge`);
  assert.ok(stage.height >= 320, "the canvas is squashed");
  // No footer hint or element count under the canvas, and no boxed wells.
  assert.equal(await page.getByText(/Space \+ drag/).count(), 0);
  assert.equal(await page.getByText(/\d+ elements/).count(), 0);
  const tools = page.getByRole("toolbar", { name: "Drawing tools" });
  await tools.getByRole("button", { name: "More canvas tools" }).click();
  const options = page.getByRole("dialog", { name: "Canvas options" });
  await options.waitFor();
  const paper = options.getByRole("combobox", { name: "Paper" });
  assert.equal(await paper.textContent().then((text) => text.trim()), "Dotted");
  await paper.click();
  await page.getByRole("option", { name: "Grid" }).click();
  assert.equal(await paper.textContent().then((text) => text.trim()), "Grid");
  for (const name of ["Export JSON", "Export SVG", "Export PNG"]) assert.equal(await options.getByRole("button", { name }).count(), 1, `no ${name}`);
  assert.equal(await options.getByText("Import image or PDF").count(), 1);
  assert.equal(await options.getByText("Restore JSON page").count(), 1);
  await options.getByRole("button", { name: "Layers…" }).click();
  const layers = page.getByRole("dialog", { name: "Layers" });
  await layers.waitFor();
  assert.equal(await layers.getByRole("textbox", { name: "Layer name", exact: true }).inputValue(), "Drawing");
  await layers.getByRole("textbox", { name: "New layer name" }).fill("Annotations");
  await layers.getByRole("button", { name: "Add layer" }).click();
  assert.equal(await layers.getByRole("textbox", { name: "Layer name", exact: true }).count(), 2);
  await layers.getByRole("button", { name: "Close" }).click();
  await layers.waitFor({ state: "hidden" });
  assert.deepEqual(problems, []);
  await context.close();
});

test("no native dropdown or fold-out remains in Notes, the library or the canvas", async () => {
  const { page, problems, context } = await notes();
  assert.deepEqual(await natives(page), { select: 0, summary: 0 });
  // The library editor's folder picker and the canvas text outline's Tag are themed.
  await (await library(page)).waitFor();
  await page.getByRole("button", { name: "＋ Folder" }).click();
  await page.getByRole("combobox", { name: "Parent folder" }).waitFor();
  assert.deepEqual(await natives(page), { select: 0, summary: 0 });
  await page.keyboard.press("Escape");
  await mode(page, "Canvas").click();
  await page.getByRole("toolbar", { name: "Drawing tools" }).getByRole("button", { name: "More canvas tools" }).click();
  await page.getByRole("dialog", { name: "Canvas options" }).getByRole("button", { name: "Text outline" }).click();
  const outline = page.getByRole("region", { name: "Accessible editable text outline" });
  await outline.waitFor();
  assert.equal(await outline.getByRole("combobox", { name: "Tag" }).count(), 2);
  await outline.getByRole("checkbox").first().check();
  await page.getByRole("combobox", { name: "Move selection to layer" }).waitFor();
  assert.deepEqual(await natives(page), { select: 0, summary: 0 });
  assert.deepEqual(problems, []);
  await context.close();
});

test("Import and Privacy & storage open drawers from ⋯ instead of wells under the page", async () => {
  const { page, problems, context } = await notes();
  assert.equal(await page.getByRole("region", { name: "Import documents" }).count(), 0, "the importer still sits under the page");
  await choose(page, "Import PDF or Markdown…");
  const drawer = page.getByRole("dialog", { name: "Import PDF or Markdown" });
  await drawer.waitFor();
  assert.equal(await drawer.getByRole("region", { name: "Import documents" }).count(), 1, "the drawer has no importer");
  await page.keyboard.press("Escape");
  await drawer.waitFor({ state: "hidden" });
  await choose(page, "Privacy & storage…");
  const privacy = page.getByRole("dialog", { name: "Privacy & storage" });
  await privacy.waitFor();
  assert.equal(await privacy.getByRole("checkbox", { name: "Allow selected-text AI sharing" }).isChecked(), false);
  await privacy.getByRole("spinbutton", { name: "Unused reference retention (days)" }).fill("14");
  await privacy.getByRole("button", { name: "Save", exact: true }).click();
  await until(async () => page.evaluate(() => window.notesHarness.calls.some((call) => call.path === "/api/notes/preferences" && call.body?.retention_days === 14)), "the retention saved");
  assert.equal(await privacy.getByRole("button", { name: "Clean unused references" }).count(), 1);
  // Q9: at most one short line, before the destructive cleanup.
  const words = (await privacy.locator("p").allTextContents()).map((text) => text.trim().split(/\s+/).length);
  assert.ok(words.length <= 1 && words.every((count) => count <= 12), `privacy help is ${words.join("/")} words`);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Find in page opens a small find field from the header and Ctrl+F", async () => {
  const { page, problems, context } = await notes();
  const find = page.getByRole("button", { name: "Find in page", exact: true });
  assert.match(await find.getAttribute("title"), /Ctrl F/);
  await find.click();
  const panel = page.getByRole("dialog", { name: "Find in page" });
  const field = panel.getByRole("searchbox", { name: "Find text" });
  await field.waitFor();
  await field.fill("Virtual nodes");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Block 2");
  await block(page, 1).click();
  await page.keyboard.press("Control+f");
  await panel.waitFor();
  assert.equal(await field.inputValue(), "Virtual nodes");
  await page.keyboard.press("Escape");
  await panel.waitFor({ state: "hidden" });
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
  await until(async () => (await block(page, 1).textContent()) === "Each key belongs to the next node clockwise on the ring.", "the saved block reopens");
  assert.deepEqual(problems, []);
  await context.close();
});

test("an unsaved title edit blocks switching pages until Retry save succeeds, and the tab dot shows the failure", async () => {
  const { page, problems, context } = await notes();
  await page.evaluate(() => { window.notesHarness.failSaves = true; });
  await title(page).fill("Draft title that must not be lost");
  await (await library(page)).getByRole("button", { name: "Raft notes" }).click();
  await assert.doesNotReject(page.getByRole("alert").filter({ hasText: "Synthetic save failure" }).waitFor());
  assert.equal(await title(page).inputValue(), "Draft title that must not be lost");
  assert.equal(await page.evaluate(() => window.notesHarness.status?.state), "failed", "the tab dot does not show the failed save");
  await page.evaluate(() => { window.notesHarness.failSaves = false; });
  await page.getByRole("button", { name: "Retry save", exact: true }).click();
  await page.waitForFunction(() => window.notesHarness.saved(1).title === "Draft title that must not be lost");
  await until(async () => (await page.evaluate(() => window.notesHarness.status?.state)) === "saved", "the tab dot shows saved");
  assert.equal(await page.getByRole("button", { name: "Retry save" }).count(), 0);
  assert.deepEqual(problems, []);
  await context.close();
});

test("an edit saved in another window appears live without a reload", async () => {
  const { page, problems, context } = await notes();
  await page.waitForFunction(() => window.notesHarness.sockets.some((socket) => socket.readyState === 1));
  assert.match(await page.evaluate(() => window.notesHarness.sockets[0].url), /^ws:\/\/[^/]+\/api\/live\/socket\?token=synthetic-token$/);
  await page.evaluate(() => window.notesHarness.editElsewhere(1, { title: "Consistent hashing (from window 2)", text: "Edited live in a second window." }));
  // Well under the old 4 s refresh poll: the push itself renders the edit.
  await until(async () => (await block(page, 1).textContent()) === "Edited live in a second window.", "the pushed edit shows");
  await titleIs(page, "Consistent hashing (from window 2)");
  await until(async () => (await (await library(page)).innerText()).includes("Consistent hashing (from window 2)"), "the library shows the new title");
  assert.deepEqual(problems, []);
  await context.close();
});

test("a live change never overwrites an unsaved local edit", async () => {
  const { page, problems, context } = await notes();
  await page.waitForFunction(() => window.notesHarness.sockets.some((socket) => socket.readyState === 1));
  await block(page, 1).waitFor();
  await page.evaluate(() => { window.notesHarness.server.holdAcks = true; });
  await block(page, 1).fill("My unsaved draft.");
  await page.waitForFunction(() => window.notesHarness.server.held.length > 0);
  await page.evaluate(() => window.notesHarness.editElsewhere(1, { text: "Someone else's edit." }));
  await page.waitForTimeout(600);
  assert.equal(await block(page, 1).textContent(), "My unsaved draft.");
  // Once confirmed, the server's order decides: the edit committed after the draft wins, in both places.
  await page.evaluate(() => window.notesHarness.server.release());
  await until(async () => (await block(page, 1).textContent()) === await page.evaluate(() => window.notesHarness.saved(1).blocks[0].text), "editor and server agree");
  assert.deepEqual(problems, []);
  await context.close();
});

test("a pinned citation says when its source changed since and shows the change", async () => {
  const { page, problems, context } = await notes();
  await page.waitForFunction(() => window.notesHarness.sockets.some((socket) => socket.readyState === 1));
  await block(page, 1).waitFor();
  // An agent cites block 4 of "Raft notes" as of seq 2 (that page's first transaction); then it changes.
  await page.evaluate(() => {
    const { server } = window.notesHarness;
    const agent = { kind: "agent", name: "claude-code", session: "agent-session-1" };
    server.commit([{ op: "text", id: "40000000-0000-4000-8000-000000000003",
      text: "Replication details live in ((40000000-0000-4000-8000-000000000004@2))." }], undefined, agent);
  });
  await page.waitForTimeout(600);
  assert.equal(await page.getByRole("button", { name: /source changed since/ }).count(), 0, "an unchanged source is not flagged");
  await page.evaluate(() => window.notesHarness.editElsewhere(2, { text: "A leader replicates log entries to a majority." }));
  const badge = page.getByRole("button", { name: "source changed since" });
  await assert.doesNotReject(badge.waitFor());
  // The pill: a compact warning chip with a status dot, apart from the reference's own text.
  const pill = await badge.evaluate((button) => {
    const style = getComputedStyle(button), dot = button.querySelector(".dot");
    return { height: button.getBoundingClientRect().height, radius: parseFloat(style.borderTopLeftRadius), color: style.color,
      background: style.backgroundColor, dot: dot && getComputedStyle(dot).backgroundColor, text: getComputedStyle(button.parentElement).color };
  });
  assert.ok(pill.height >= 16 && pill.height <= 22, `pill is ${pill.height}px tall`);
  assert.ok(pill.radius >= 8, "the pill is rounded");
  assert.ok(pill.dot, "the pill carries a status dot");
  assert.notEqual(pill.color, pill.text, "the pill reads apart from the text around it");
  assert.notEqual(pill.background, "rgba(0, 0, 0, 0)", "the pill has its own fill");
  await badge.click();
  const panel = page.getByRole("region", { name: "Source changed since" });
  await assert.doesNotReject(panel.waitFor());
  // A word diff: the cited words struck through, the new words marked as added, the rest plain.
  assert.equal(await panel.locator("del").textContent(), "followers");
  assert.equal(await panel.locator("ins").textContent(), "a majority");
  assert.equal(await panel.locator(".diff").textContent(), "A leader replicates log entries to followersa majority.");
  assert.match(await panel.innerText(), /Cited at seq 2 · changed at seq \d+/);
  const marks = () => panel.evaluate((section) => {
    const [del, ins, diff] = ["del", "ins", ".diff"].map((selector) => getComputedStyle(section.querySelector(selector)));
    return { del: { line: del.textDecorationLine, color: del.color, background: del.backgroundColor },
      ins: { line: ins.textDecorationLine, color: ins.color, background: ins.backgroundColor }, text: diff.color };
  });
  const found = await marks();
  assert.equal(found.del.line, "line-through");
  assert.equal(found.ins.line, "none");
  assert.notEqual(found.del.color, found.ins.color, "removed and added words read apart");
  assert.notEqual(found.del.background, "rgba(0, 0, 0, 0)", "removed words carry a delete wash");
  assert.notEqual(found.ins.background, "rgba(0, 0, 0, 0)", "added words carry an insert wash");
  // Every theme keeps the marks and the pill AA over the side panel.
  for (const theme of THEMES) {
    await page.evaluate((id) => window.notesHarness.setTheme(id), theme.id);
    await page.waitForFunction((id) => document.documentElement.dataset.theme === id, theme.id);
    // Colours transition on a theme switch; measure once they have settled (ambient art loops forever: not those).
    await page.evaluate(() => Promise.all(document.getAnimations().filter((animation) => animation instanceof CSSTransition)
      .map((animation) => animation.finished.catch(() => {}))));
    const ratios = await page.evaluate(([measure, grounds]) => {
      const contrast = new Function(`return (${measure})`)();
      const panel = document.querySelector('[aria-label="Source changed since"]');
      return Object.fromEntries([["del", panel.querySelector("del")], ["ins", panel.querySelector("ins")], ["meta", panel.querySelector(".meta")],
        ["pill", document.querySelector(".source-changed")]].map(([name, node]) => [name, contrast(node, grounds)]));
    }, [textContrast.toString(), [theme.roles.void, theme.roles.panel, theme.roles.inset]]);
    assert.deepEqual(Object.entries(ratios).filter(([, ratio]) => ratio < 4.5), [], `${theme.label}: diff marks below AA`);
  }
  assert.deepEqual(problems, []);
  await context.close();
});
