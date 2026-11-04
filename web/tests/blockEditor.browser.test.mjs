// rika!'s block editor in a real browser against an in-memory stand-in for the block
// backend (tests/blockEditorHarness.js, tests/fakeBlockServer.js; synthetic data only):
// typing, split/merge, the / menu, Markdown shortcuts, drag and drop, IME composition,
// block selection and moves, copy/paste, live changes from another window, History and Trash.
// Run with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/blockEditor.browser.test.mjs
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

async function open() {
  const context = await browser.newContext({ viewport: { width: 1100, height: 800 } });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("tests/block-editor-harness.html", base).href, { timeout: 60000 });
  await page.waitForFunction(() => !!window.blockHarness, null, { timeout: 60000 });
  await block(page, 4).waitFor({ timeout: 30000 });
  return { page, problems, context };
}
async function until(check, what) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.fail(`timed out waiting until ${what}`);
}
const block = (page, n) => page.getByRole("textbox", { name: `Block ${n}`, exact: true });
const texts = (page) => page.locator("[data-editable]").allTextContents();
const saved = (page) => page.evaluate(() => window.blockHarness.outline());
const sent = (page) => page.evaluate(() => window.blockHarness.server.sent);
async function caretAtEnd(page, n) {
  await block(page, n).click();
  await page.keyboard.press("End");
}
async function serverIs(page, expected, what) {
  await until(async () => JSON.stringify(await saved(page)) === JSON.stringify(expected), what);
}

test("typing echoes at once and reaches the server as a small text splice with the base revision", async () => {
  const { page, problems, context } = await open();
  await caretAtEnd(page, 1);
  await page.keyboard.type(" More.");
  assert.equal(await block(page, 1).textContent(), "Alpha block about hash rings. More.");
  await serverIs(page, [["text", "Alpha block about hash rings. More.", 0], ["text", "Bravo block on virtual nodes.", 0],
    ["text", "Charlie block for replication.", 0], ["text", "Delta block closes the page.", 0]], "the typed text is saved");
  const txns = await sent(page);
  assert.ok(txns.length >= 1 && txns.length <= 3, `typing was batched into ${txns.length} transactions`);
  assert.deepEqual(txns[0].ops[0], { op: "text", id: await page.evaluate(() => window.blockHarness.id(1)), at: 29, del: 0, ins: txns[0].ops[0].ins });
  assert.ok(Object.values(txns[0].base)[0] >= 1, "transactions carry the page's base revision");
  assert.deepEqual(problems, []);
  await context.close();
});

test("Enter splits a block at the caret and Backspace at the start merges it back", async () => {
  const { page, problems, context } = await open();
  await block(page, 2).click();
  await page.keyboard.press("Home");
  for (let i = 0; i < 5; i += 1) await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Enter");
  assert.deepEqual((await texts(page)).slice(1, 3), ["Bravo", " block on virtual nodes."]);
  assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-label")), "Block 3");
  await serverIs(page, [["text", "Alpha block about hash rings.", 0], ["text", "Bravo", 0], ["text", " block on virtual nodes.", 0],
    ["text", "Charlie block for replication.", 0], ["text", "Delta block closes the page.", 0]], "the split is saved");
  const bravo = await page.evaluate(() => window.blockHarness.block(window.blockHarness.id(2)));
  assert.equal(bravo.text, "Bravo", "the block keeps its id (and its references) with the text before the caret");
  await page.keyboard.press("Backspace");
  assert.deepEqual((await texts(page)).slice(1, 3), ["Bravo block on virtual nodes.", "Charlie block for replication."]);
  await page.keyboard.type("|");
  assert.equal(await block(page, 2).textContent(), "Bravo| block on virtual nodes.", "the caret sits at the join");
  await serverIs(page, [["text", "Alpha block about hash rings.", 0], ["text", "Bravo| block on virtual nodes.", 0],
    ["text", "Charlie block for replication.", 0], ["text", "Delta block closes the page.", 0]], "the merge is saved");
  assert.deepEqual(problems, []);
  await context.close();
});

test("the / menu turns a block into another type and keeps its text and properties", async () => {
  const { page, problems, context } = await open();
  await block(page, 3).click();
  await page.keyboard.press("End");
  await page.keyboard.down("Shift");
  for (let i = 0; i < 12; i += 1) await page.keyboard.press("ArrowLeft");
  await page.keyboard.up("Shift");
  await page.keyboard.press("Control+b");
  await block(page, 1).click();
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await page.keyboard.type("/head");
  const menu = page.getByRole("listbox", { name: "Turn into" });
  await assert.doesNotReject(menu.waitFor());
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await page.keyboard.type("Typed heading");
  await until(async () => JSON.stringify((await saved(page))[1]) === JSON.stringify(["heading_2", "Typed heading", 0]), "the new block is a heading 2 with the typed text");
  // Turn an existing block into a to-do from its menu: only the type changes.
  const charlie = await page.evaluate(() => window.blockHarness.id(3));
  await page.locator(`[data-block-id="${charlie}"] .handle`).click();
  await page.getByRole("menu").getByRole("menuitem", { name: "To-do" }).click();
  await until(async () => (await page.evaluate((bid) => window.blockHarness.block(bid).type, charlie)) === "to_do", "Charlie is a to-do");
  const turned = await page.evaluate((bid) => window.blockHarness.block(bid), charlie);
  assert.equal(turned.text, "Charlie block for replication.");
  assert.deepEqual(turned.properties.marks, [{ s: 18, e: 30, t: "b" }], "inline bold survives turn-into");
  assert.equal(await page.locator(`[data-block-id="${charlie}"] strong`).textContent(), "replication.");
  assert.deepEqual(problems, []);
  await context.close();
});

test("Markdown shortcuts turn blocks into headings, lists, to-dos, quotes, dividers and code as you type", async () => {
  const { page, problems, context } = await open();
  await caretAtEnd(page, 4);
  for (const line of ["# Title", "- bullet", "1. first", "[] task", "> quoted"]) {
    await page.keyboard.press("Enter");
    await page.keyboard.type(line);
  }
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await page.keyboard.type("---");
  await page.keyboard.type("```");
  await page.keyboard.type("let x = 1;");
  await serverIs(page, [["text", "Alpha block about hash rings.", 0], ["text", "Bravo block on virtual nodes.", 0],
    ["text", "Charlie block for replication.", 0], ["text", "Delta block closes the page.", 0], ["heading_1", "Title", 0],
    ["bulleted_list", "bullet", 0], ["numbered_list", "first", 0], ["to_do", "task", 0], ["quote", "quoted", 0],
    ["text", "", 0], ["divider", "", 0], ["code", "let x = 1;", 0]], "shortcuts became typed blocks");
  assert.equal(await page.locator(".type-heading_1 [data-editable]").textContent(), "Title");
  assert.equal(await page.locator(".type-divider hr").count(), 1);
  assert.deepEqual(problems, []);
  await context.close();
});

test("dragging a block by its handle reorders the page", async () => {
  const { page, problems, context } = await open();
  const [alpha, delta] = await page.evaluate(() => [window.blockHarness.id(1), window.blockHarness.id(4)]);
  const target = page.locator(`[data-block-id="${delta}"]`);
  const height = (await target.boundingBox()).height;
  await page.locator(`[data-block-id="${alpha}"]`).hover();
  await page.locator(`[data-block-id="${alpha}"] .handle`).dragTo(target, { targetPosition: { x: 50, y: height - 3 } });
  await serverIs(page, [["text", "Bravo block on virtual nodes.", 0], ["text", "Charlie block for replication.", 0],
    ["text", "Delta block closes the page.", 0], ["text", "Alpha block about hash rings.", 0]], "Alpha moved below Delta");
  assert.deepEqual(await texts(page), ["Bravo block on virtual nodes.", "Charlie block for replication.", "Delta block closes the page.", "Alpha block about hash rings."]);
  const moves = (await sent(page)).flatMap((txn) => txn.ops).filter((op) => op.op === "move");
  assert.equal(moves.length, 1, "one move with a fresh order key; no sibling was rewritten");
  // Dropping onto another page moves the block there.
  await page.locator(`[data-block-id="${delta}"]`).hover();
  await page.locator(`[data-block-id="${delta}"] .handle`).dragTo(page.getByRole("button", { name: "Second page" }));
  await until(async () => (await texts(page)).length === 3, "Delta left this page");
  assert.deepEqual(await page.evaluate(() => window.blockHarness.outline(window.blockHarness.OTHER)),
    [["text", "Already on the second page.", 0], ["text", "Delta block closes the page.", 0]]);
  assert.deepEqual(problems, []);
  await context.close();
});

test("IME composition commits once, never splits on its Enter and never drops text", async () => {
  const { page, problems, context } = await open();
  const cdp = await context.newCDPSession(page);
  await caretAtEnd(page, 2);
  await cdp.send("Input.imeSetComposition", { text: "に", selectionStart: 1, selectionEnd: 1 });
  await cdp.send("Input.imeSetComposition", { text: "にほん", selectionStart: 3, selectionEnd: 3 });
  // A change from another window arrives mid-composition; the composing block is left alone.
  await page.evaluate(() => window.blockHarness.server.commit([{ op: "text", id: window.blockHarness.id(4), text: "Delta edited elsewhere." }], "other-device"));
  await until(async () => (await block(page, 4).textContent()) === "Delta edited elsewhere.", "the remote edit shows");
  await cdp.send("Input.insertText", { text: "日本" });
  assert.equal(await block(page, 2).textContent(), "Bravo block on virtual nodes.日本");
  assert.equal((await texts(page)).length, 4, "committing the composition did not split the block");
  await page.keyboard.type(" ok");
  await serverIs(page, [["text", "Alpha block about hash rings.", 0], ["text", "Bravo block on virtual nodes.日本 ok", 0],
    ["text", "Charlie block for replication.", 0], ["text", "Delta edited elsewhere.", 0]], "the composed text is saved once");
  assert.ok(!(await sent(page)).some((txn) => txn.ops.some((op) => op.op === "text" && /にほ/.test(op.ins ?? op.text ?? ""))), "no partial composition was sent");
  assert.deepEqual(problems, []);
  await context.close();
});

test("Esc selects blocks, Shift+arrows extend, Ctrl+Shift+arrows move them and Tab nests them", async () => {
  const { page, problems, context } = await open();
  await block(page, 2).click();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Shift+ArrowDown");
  assert.equal(await page.locator("[data-selected=true]").count(), 2);
  await page.keyboard.press("Control+Shift+ArrowUp");
  await serverIs(page, [["text", "Bravo block on virtual nodes.", 0], ["text", "Charlie block for replication.", 0],
    ["text", "Alpha block about hash rings.", 0], ["text", "Delta block closes the page.", 0]], "both selected blocks moved up");
  assert.equal(await page.locator("[data-selected=true]").count(), 2, "the moved blocks stay selected");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Tab");
  await serverIs(page, [["text", "Bravo block on virtual nodes.", 0], ["text", "Charlie block for replication.", 0],
    ["text", "Alpha block about hash rings.", 0], ["text", "Delta block closes the page.", 1]], "Delta nests under Alpha");
  await page.keyboard.press("Enter");
  await page.keyboard.type("!");
  await until(async () => (await saved(page))[3][1] === "Delta block closes the page.!", "Enter returns to typing at the end");
  await page.keyboard.press("Shift+Tab");
  await serverIs(page, [["text", "Bravo block on virtual nodes.", 0], ["text", "Charlie block for replication.", 0],
    ["text", "Alpha block about hash rings.", 0], ["text", "Delta block closes the page.!", 0]], "Shift+Tab un-nests while typing");
  assert.deepEqual(problems, []);
  await context.close();
});

test("copying selected blocks gives Markdown, and pasting Markdown creates typed blocks", async () => {
  const { page, problems, context } = await open();
  await page.evaluate(() => window.blockHarness.server.commit([{ op: "set", id: window.blockHarness.id(2), type: "to_do", props: { checked: true } }]));
  await until(async () => (await page.locator(".type-to_do").count()) === 1, "Bravo became a to-do");
  await block(page, 1).click();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Shift+ArrowDown");
  const copied = await page.evaluate(() => {
    const data = new DataTransfer();
    document.dispatchEvent(new ClipboardEvent("copy", { clipboardData: data, bubbles: true, cancelable: true }));
    return { plain: data.getData("text/plain"), kitty: data.getData("application/x-kitty-blocks") };
  });
  assert.equal(copied.plain, "Alpha block about hash rings.\n- [x] Bravo block on virtual nodes.");
  assert.equal(JSON.parse(copied.kitty).length, 2);
  await caretAtEnd(page, 4);
  await block(page, 4).evaluate((element) => {
    const data = new DataTransfer();
    data.setData("text/plain", "\n## Pasted heading\n- one\n  - nested **bold**\n");
    element.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  });
  await serverIs(page, [["text", "Alpha block about hash rings.", 0], ["to_do", "Bravo block on virtual nodes.", 0],
    ["text", "Charlie block for replication.", 0], ["text", "Delta block closes the page.", 0], ["heading_2", "Pasted heading", 0],
    ["bulleted_list", "one", 0], ["bulleted_list", "nested bold", 1]], "the pasted Markdown became blocks");
  assert.equal(await page.locator(".type-bulleted_list strong").textContent(), "bold");
  // Pasting copied kitty blocks keeps their types.
  await block(page, 3).click();
  await page.keyboard.press("Escape");
  await page.evaluate((kitty) => {
    const data = new DataTransfer();
    data.setData("application/x-kitty-blocks", kitty);
    data.setData("text/plain", "ignored");
    document.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  }, copied.kitty);
  await until(async () => (await saved(page)).length === 9, "two blocks pasted after Charlie");
  assert.deepEqual((await saved(page)).slice(3, 5), [["text", "Alpha block about hash rings.", 0], ["to_do", "Bravo block on virtual nodes.", 0]]);
  assert.deepEqual(problems, []);
  await context.close();
});

test("inline bold, italic, code and links are formatting, not characters in the text", async () => {
  const { page, problems, context } = await open();
  await block(page, 1).click();
  await page.keyboard.press("Home");
  await page.keyboard.down("Shift");
  for (let i = 0; i < 5; i += 1) await page.keyboard.press("ArrowRight");
  await page.keyboard.up("Shift");
  await page.keyboard.press("Control+i");
  await page.keyboard.press("Control+k");
  await page.getByRole("textbox", { name: "Link address" }).fill("https://example.test/alpha");
  await page.keyboard.press("Enter");
  const alpha = await page.evaluate(() => window.blockHarness.id(1));
  await until(async () => (await page.evaluate((bid) => window.blockHarness.block(bid).properties.marks?.length ?? 0, alpha)) === 2, "marks saved");
  const saved1 = await page.evaluate((bid) => window.blockHarness.block(bid), alpha);
  assert.equal(saved1.text, "Alpha block about hash rings.");
  assert.deepEqual(saved1.properties.marks, [{ s: 0, e: 5, t: "a", href: "https://example.test/alpha" }, { s: 0, e: 5, t: "i" }]);
  assert.equal(await block(page, 1).locator("a em").textContent(), "Alpha");
  assert.deepEqual(problems, []);
  await context.close();
});

test("another window's change renders while typing here without clobbering the unsaved text", async () => {
  const { page, problems, context } = await open();
  await page.evaluate(() => { window.blockHarness.server.holdAcks = true; });
  await caretAtEnd(page, 2);
  await page.keyboard.type(" Typed here");
  await page.waitForFunction(() => window.blockHarness.server.held.length > 0);
  await page.evaluate(() => window.blockHarness.server.commit([
    { op: "text", id: window.blockHarness.id(3), text: "Charlie rewritten in window two." },
    { op: "text", id: window.blockHarness.id(2), text: "Bravo overwritten in window two." },
  ], "other-device"));
  await until(async () => (await block(page, 3).textContent()) === "Charlie rewritten in window two.", "the other window's edit renders");
  assert.equal(await block(page, 2).textContent(), "Bravo block on virtual nodes. Typed here", "the unsaved local text is kept");
  await page.keyboard.type(" still");
  assert.equal(await block(page, 2).textContent(), "Bravo block on virtual nodes. Typed here still", "the caret stayed in place");
  await page.evaluate(() => window.blockHarness.server.release());
  await serverIs(page, [["text", "Alpha block about hash rings.", 0], ["text", "Bravo block on virtual nodes. Typed here still", 0],
    ["text", "Charlie rewritten in window two.", 0], ["text", "Delta block closes the page.", 0]], "both edits are kept; this window's text wins its block");
  assert.ok((await sent(page)).length >= 2, "the rejected stale transaction was re-sent after a re-sync");
  assert.deepEqual(problems, []);
  await context.close();
});

test("History previews any moment and Restore appends a transaction that brings it back", async () => {
  const { page, problems, context } = await open();
  await caretAtEnd(page, 1);
  await page.keyboard.type(" Version two.");
  await until(async () => (await saved(page))[0][1].endsWith("Version two."), "version two saved");
  await page.keyboard.press("Enter");
  await page.keyboard.type("Version three adds a block.");
  await until(async () => (await saved(page)).length === 5 && (await saved(page))[1][1] === "Version three adds a block.", "version three saved");
  await page.getByRole("button", { name: "Open history" }).click();
  const dialog = page.getByRole("dialog", { name: /History/ });
  await assert.doesNotReject(dialog.waitFor());
  const slider = dialog.getByRole("slider", { name: "Moment in history" });
  await assert.doesNotReject(slider.waitFor());
  await slider.focus();
  await page.keyboard.press("Home");
  await until(async () => !(await dialog.locator(".preview").innerText()).includes("Version two."), "the first moment previews");
  const preview = await dialog.locator(".preview").innerText();
  assert.ok(preview.includes("Alpha block about hash rings.") && !preview.includes("Version three"), preview);
  await dialog.getByRole("button", { name: "Restore this version" }).click();
  await assert.doesNotReject(dialog.getByRole("status").filter({ hasText: "Restored the page" }).waitFor());
  await until(async () => (await texts(page))[0] === "Alpha block about hash rings." && (await texts(page)).length === 4, "the editor shows the restored page");
  const history = await page.evaluate(() => window.blockHarness.server.page(1).log.length);
  assert.ok(history >= 4, "restore appended to the log instead of rewriting it");
  await dialog.getByRole("button", { name: "Close history" }).click();
  assert.deepEqual(problems, []);
  await context.close();
});

test("History names who made each session, filters by actor and undoes one agent session", async () => {
  const { page, problems, context } = await open();
  await page.evaluate(() => {
    const { server, id } = window.blockHarness;
    const agent = { kind: "agent", name: "claude-code", session: "agent-session-1" };
    server.commit([{ op: "text", id: id(1), text: "Alpha, rewritten by the agent." }], undefined, agent);
    server.commit([{ op: "text", id: id(2), text: "Bravo, rewritten by the agent." }], undefined, agent);
  });
  await until(async () => (await texts(page))[1] === "Bravo, rewritten by the agent.", "the agent's edits arrive live");
  await caretAtEnd(page, 2);
  await page.keyboard.type(" Mine.");
  await until(async () => (await saved(page))[1][1] === "Bravo, rewritten by the agent. Mine.", "the person's edit is saved");
  await page.getByRole("button", { name: "Open history" }).click();
  const dialog = page.getByRole("dialog", { name: /History/ });
  const sessions = dialog.getByRole("navigation", { name: "Editing sessions" });
  const who = () => sessions.locator(".who").allTextContents();
  await until(async () => (await who()).includes("Agent · claude-code"), "the agent's session is named");
  assert.deepEqual(await who(), ["You", "Agent · claude-code", "You"]);
  await dialog.getByRole("combobox", { name: "Filter by actor" }).selectOption({ label: "Agent · claude-code" });
  await until(async () => JSON.stringify(await who()) === '["Agent · claude-code"]', "the filter hides the person's sessions");
  await dialog.getByRole("button", { name: "Undo agent session Agent · claude-code" }).click();
  const plan = dialog.getByRole("region", { name: "Undo agent session" });
  await assert.doesNotReject(plan.getByText("Reverts 1 block the agent changed in 2 transactions").waitFor());
  assert.match(await plan.innerText(), /1 block changed by someone else since stay as they are/);
  assert.deepEqual(await page.evaluate(() => window.blockHarness.server.undos), [{ session: "agent-session-1", dry_run: true }]);
  await plan.getByRole("button", { name: "Undo agent session" }).click();
  await assert.doesNotReject(dialog.getByRole("status").filter({ hasText: "Undid the agent session: 1 block reverted" }).waitFor());
  await until(async () => (await texts(page))[0] === "Alpha block about hash rings.", "the agent's untouched edit is reverted");
  assert.equal((await texts(page))[1], "Bravo, rewritten by the agent. Mine.");
  await dialog.getByRole("button", { name: "Close history" }).click();
  assert.deepEqual(problems, []);
  await context.close();
});

test("a deleted block goes to Trash and comes back with Restore", async () => {
  const { page, problems, context } = await open();
  const charlie = await page.evaluate(() => window.blockHarness.id(3));
  await page.locator(`[data-block-id="${charlie}"] .handle`).click();
  await page.getByRole("menu").getByRole("menuitem", { name: "Delete" }).click();
  await until(async () => (await texts(page)).length === 3, "Charlie left the page");
  await page.getByRole("button", { name: "Open trash" }).click();
  const dialog = page.getByRole("dialog", { name: "Trash" });
  const item = dialog.getByRole("listitem").filter({ hasText: "Charlie block for replication." });
  await assert.doesNotReject(item.waitFor());
  assert.match(await item.innerText(), /Block on “Synthetic page”.*30 days left/s);
  await item.getByRole("button", { name: /Restore/ }).click();
  await assert.doesNotReject(dialog.getByRole("status").filter({ hasText: "Restored" }).waitFor());
  await until(async () => (await texts(page)).includes("Charlie block for replication."), "Charlie is back in the editor");
  assert.equal(await dialog.getByText("Trash is empty.").isVisible(), true);
  assert.deepEqual(problems, []);
  await context.close();
});
