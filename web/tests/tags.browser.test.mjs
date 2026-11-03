// Auto-tags (Q13) end to end in a real browser against a real rika! server (`kitty-web
// --demo`: a disposable synthetic workspace; tests/kittyServer.js): the faint underline and
// Tab to attach, Esc remembered across a reload, Always attach then removal never re-attached,
// and a tag page's backlinks and Link all.
// Run with Node >=22, uv, and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/tags.browser.test.mjs
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { startHarness, startKitty } from "./kittyServer.js";

let kitty;
let vite;
let browser;
let base;
const tags = {};

before(async () => {
  kitty = await startKitty();
  vite = await startHarness(kitty.origin);
  base = vite.resolvedUrls.local[0];
  browser = await chromium.launch();
  tags.python = await kitty.request("/tags/create", { name: "Python", keywords: ["pandas"] });
  tags.kafka = await kitty.request("/tags/create", { name: "Kafka", keywords: ["event streaming"] });
});
after(async () => {
  await browser?.close();
  await vite?.close();
  kitty?.stop();
});

let notebook = null;
async function newPage(title) {
  notebook ??= await kitty.request("/notes/notebooks", { title: "Auto-tag tests" });
  return (await kitty.request("/notes/pages", { notebook_id: notebook.id, title })).id;
}

async function open(number) {
  const context = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(`tests/tags-harness.html?page=${number}`, base).href, { timeout: 90000 });
  await page.locator(".block-editor[aria-busy=false]").waitFor({ timeout: 60000 });
  return { page, context, problems };
}
async function until(check, what) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.fail(`timed out waiting until ${what}`);
}
const block = (page, n) => page.getByRole("textbox", { name: `Block ${n}`, exact: true });
const underlined = (page) => page.evaluate(() => [...(CSS.highlights.get("kitty-tag-suggestion") ?? [])].map((range) => range.toString()));
const chips = (page, n) => page.getByRole("list", { name: `Tags of block ${n}` }).getByRole("button", { name: /^#/ }).allTextContents();
async function write(page, text) {
  await page.getByRole("button", { name: "Start writing…" }).click();
  await block(page, 1).waitFor();
  await page.keyboard.type(text);
}
async function tagPage(tag) {
  return kitty.request(`/tags/page?id=${tag.id}`);
}
async function blockId(page, n) {
  return block(page, n).getAttribute("data-editable");
}

test("a suggested keyword is underlined and Tab attaches its tag", async () => {
  const { page, context, problems } = await open(await newPage("Suggest and Tab"));
  await write(page, "Cleaning data with pandas");
  await until(async () => JSON.stringify(await underlined(page)) === JSON.stringify(["pandas"]), "pandas is underlined");
  const hint = page.getByRole("group", { name: "Suggested tags" });
  await hint.getByRole("button", { name: "Tag #Python" }).waitFor();
  await page.screenshot({ path: process.env.TAGS_SCREENSHOTS ? `${process.env.TAGS_SCREENSHOTS}/suggest-underline.png` : undefined });
  await page.keyboard.press("Tab");
  await until(async () => JSON.stringify(await chips(page, 1)) === JSON.stringify(["#Python"]), "the #Python chip shows");
  assert.deepEqual(await underlined(page), []);
  assert.equal(await hint.count(), 0);
  const id = await blockId(page, 1);
  await until(async () => (await tagPage(tags.python)).blocks.some((item) => item.id === id && item.source === "accepted"), "the server has it accepted");
  assert.equal(await page.locator(".block-row").first().evaluate((row) => getComputedStyle(row).getPropertyValue("--depth").trim()), "0", "Tab did not indent");
  assert.deepEqual(problems, []);
  await context.close();
});

test("Esc ignores a suggestion for good, also after a reload", async () => {
  const number = await newPage("Esc ignores");
  const { page, context, problems } = await open(number);
  await write(page, "More pandas here");
  await until(async () => (await underlined(page)).length === 1, "pandas is underlined");
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft");
  for (let i = 0; i < 5; i += 1) await page.keyboard.press("ArrowLeft");
  await page.getByRole("group", { name: "Suggested tags" }).waitFor();
  await page.keyboard.press("Escape");
  await until(async () => (await underlined(page)).length === 0, "the underline is gone");
  assert.equal(await page.locator(".block-row.selected").count(), 0, "Esc ignored the tag instead of selecting the block");
  const id = await blockId(page, 1);
  await until(async () => {
    const view = await tagPage(tags.python);
    return !view.unlinked.some((item) => item.id === id) && !view.blocks.some((item) => item.id === id);
  }, "the server remembers the rejection");
  await page.reload();
  await page.locator(".block-editor[aria-busy=false]").waitFor({ timeout: 60000 });
  await block(page, 1).click();
  await page.keyboard.press("End");
  await page.keyboard.type(", and pandas again");
  await new Promise((resolve) => setTimeout(resolve, 1200));
  assert.deepEqual(await underlined(page), [], "never suggested again on this block");
  assert.deepEqual(await chips(page, 1), []);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Always attach tags as you write; a removed tag is never attached again", async () => {
  const { page, context, problems } = await open(await newPage("Always attach"));
  const settings = page.getByRole("region", { name: "Tags" });
  await settings.getByRole("radio", { name: /Always attach/ }).check();
  await until(async () => (await kitty.request("/tags")).mode === "always", "the setting is saved");
  try {
    await write(page, "Notes on event streaming");
    await until(async () => JSON.stringify(await chips(page, 1)) === JSON.stringify(["#Kafka"]), "#Kafka is attached automatically");
    assert.equal(await page.locator(".tag-chip.auto").count(), 1, "marked automatic");
    assert.deepEqual(await underlined(page), [], "always-attach tags are not suggested");
    await page.screenshot({ path: process.env.TAGS_SCREENSHOTS ? `${process.env.TAGS_SCREENSHOTS}/always-attached.png` : undefined });
    const id = await blockId(page, 1);
    assert.ok((await tagPage(tags.kafka)).blocks.some((item) => item.id === id && item.source === "auto"));
    await page.getByRole("button", { name: "Remove tag Kafka" }).click();
    await until(async () => (await chips(page, 1)).length === 0, "the chip is gone");
    await block(page, 1).click();
    await page.keyboard.press("End");
    await page.keyboard.type(". More event streaming, event streaming everywhere");
    await until(async () => (await block(page, 1).textContent()).endsWith("everywhere"), "typed");
    await new Promise((resolve) => setTimeout(resolve, 1500));
    assert.deepEqual(await chips(page, 1), [], "later edits never re-attach a removed tag");
    const view = await tagPage(tags.kafka);
    assert.ok(!view.blocks.some((item) => item.id === id) && !view.unlinked.some((item) => item.id === id));
  } finally {
    await settings.getByRole("radio", { name: /Suggest/ }).check();
    await until(async () => (await kitty.request("/tags")).mode === "suggest", "the setting is back to Suggest");
  }
  assert.deepEqual(problems, []);
  await context.close();
});

test("a tag page lists its blocks and unlinked mentions, and Link all links them", async () => {
  const { page, context, problems } = await open(await newPage("Tag page"));
  await write(page, "Python notes start here");
  await until(async () => (await underlined(page)).includes("Python"), "Python is underlined");
  await page.keyboard.press("Home");
  await page.getByRole("group", { name: "Suggested tags" }).getByRole("button", { name: "Tag #Python" }).click();
  await until(async () => JSON.stringify(await chips(page, 1)) === JSON.stringify(["#Python"]), "attached by click");
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await page.keyboard.type("pandas joins");
  await page.keyboard.press("Enter");
  await page.keyboard.type("pandas pivots");
  await until(async () => (await underlined(page)).length >= 2, "the new mentions are underlined");
  await page.getByRole("list", { name: "Tags of block 1" }).getByRole("button", { name: "#Python" }).click();
  const aside = page.getByRole("region", { name: "Tag page" });
  await aside.getByRole("heading", { name: "#Python" }).waitFor();
  const mentions = aside.locator("h4", { hasText: "Unlinked mentions" });
  await until(async () => /Unlinked mentions\s*[2-9]/.test(await mentions.textContent()), "the two new mentions are listed");
  assert.ok((await aside.textContent()).includes("Python notes start here"), "the tagged block is a backlink");
  await page.screenshot({ path: process.env.TAGS_SCREENSHOTS ? `${process.env.TAGS_SCREENSHOTS}/tag-page.png` : undefined });
  await aside.getByRole("button", { name: "Link all" }).click();
  await until(async () => /Unlinked mentions\s*0/.test(await mentions.textContent()), "every mention is linked");
  assert.match(await aside.locator("h4", { hasText: "Tagged blocks" }).textContent(), /Tagged blocks\s*([3-9]|\d\d)/);
  await until(async () => JSON.stringify([await chips(page, 2), await chips(page, 3)]) === JSON.stringify([["#Python"], ["#Python"]]),
    "the editor shows the new tags live");
  assert.deepEqual(await underlined(page), []);
  await aside.locator("button.open", { hasText: "pandas pivots" }).click();
  assert.equal((await page.evaluate(() => window.tagsHarness.opened)).length, 1, "a backlink opens its page at the block");
  assert.deepEqual(problems, []);
  await context.close();
});
