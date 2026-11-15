// Page edit mode (Ctrl + Shift + Space) in a real browser against the disposable synthetic
// backend (`kitty-web --demo`); no private workspace is read. A page's blocks move and
// resize, the build keeps where they were left, and home opens its own edit mode. The first
// test saves to the demo's build file, so KITTY_RIKA_SERVER must be a front built with this schema.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/pageEdit.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer as createNetServer } from "node:net";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";
import { routeBuild } from "./buildRoutes.mjs";
import { announcement, authorize, exchange } from "./kittyServer.js";

const root = fileURLToPath(new URL("..", import.meta.url));
const repository = fileURLToPath(new URL("../..", import.meta.url));
const VIEWPORT = { width: 1280, height: 850 };

let backend;
let secret;
let server;
let browser;
let base;

// KITTY_TEST_PORT pins the demo backend (and Vite, one above) for shared machines.
const pinnedPort = Number(process.env.KITTY_TEST_PORT) || 0;

function freePort() {
  if (pinnedPort) return Promise.resolve(pinnedPort);
  return new Promise((resolve, reject) => {
    const probe = createNetServer().once("error", reject).listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

before(async () => {
  backend = spawn("uv", ["run", "--quiet", "kitty-web", "--demo", "--port", String(await freePort())], {
    cwd: repository,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const { origin, code } = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error(`demo backend did not start:\n${output}`)), 120_000);
    const read = (chunk) => {
      output += chunk;
      const found = announcement(output);
      if (found) { clearTimeout(timer); resolve(found); }
    };
    backend.stdout.on("data", read);
    backend.stderr.on("data", read);
    backend.once("exit", (code) => { clearTimeout(timer); reject(new Error(`demo backend exited (${code}):\n${output}`)); });
  });
  // The link's one-time code, spent as a browser tab spends it; pages get the secret as rika!'s windows do.
  secret = await exchange(origin, code);
  server = await createServer({
    root,
    logLevel: "error",
    server: {
      host: "127.0.0.1",
      port: pinnedPort ? pinnedPort + 1 : 0,
      strictPort: Boolean(pinnedPort),
      proxy: {
        "/api": {
          target: origin,
          changeOrigin: true,
          ws: true,
          // Rika's stream upgrades through the same proxy; the backend compares Origin exactly.
          configure: (proxy) => { for (const event of ["proxyReq", "proxyReqWs"]) proxy.on(event, (request) => request.setHeader("Origin", origin)); },
        },
      },
    },
  });
  await server.listen();
  base = server.resolvedUrls.local[0];
  browser = await chromium.launch({ args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
});
after(async () => {
  await browser?.close();
  await server?.close();
  backend?.kill();
});

/** Opens a fresh returning-user profile on `hash`. `ownBuild`: preferences stay in this context instead of the demo's build file. */
async function open(hash, { ownBuild = true } = {}) {
  const context = await browser.newContext({ viewport: VIEWPORT });
  await authorize(context, secret);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue(),
  );
  if (ownBuild) await routeBuild(context);
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(`#${hash}`, base).href);
  return { page, problems, context };
}

const bar = (page) => page.getByRole("toolbar", { name: "Page editing" });
const rounded = (box) => ({ x: Math.round(box.x), y: Math.round(box.y), width: Math.round(box.width), height: Math.round(box.height) });
/** Press at one point, travel to another in steps (as a hand does), let go. */
async function dragBy(page, from, dx, dy) {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + dx, from.y + dy, { steps: 6 });
  await page.mouse.up();
}
async function settled(page, locator, wanted, message) {
  await page.waitForFunction(([selector, box]) => {
    const node = document.querySelector(selector);
    if (!node) return false;
    const now = node.getBoundingClientRect();
    return Math.round(now.x) === box.x && Math.round(now.y) === box.y && Math.round(now.width) === box.width;
  }, [locator, wanted], { timeout: 10_000 }).catch(() => {});
  assert.deepEqual(rounded(await page.locator(locator).boundingBox()), wanted, message);
}

describe("Page edit mode", () => {
  test("a block on Find jobs is moved and resized, saved by Done, back after a reload, and put back by Reset page", async () => {
    // The demo's real build file takes the moves, so the server's schema is part of the test.
    const { page, problems, context } = await open("jobs", { ownBuild: false });
    const FILTERS = '[data-kitty-panel="view"]:not([hidden]) form.filters';
    const filters = page.locator(FILTERS);
    await page.getByRole("region", { name: "Job results" }).locator("[data-job-id]").first().waitFor();
    const start = rounded(await filters.boundingBox());

    await page.keyboard.press("Control+Shift+Space");
    await bar(page).waitFor();
    assert.match(await bar(page).innerText(), /Editing this page/);
    assert.deepEqual(await bar(page).getByRole("button").allInnerTexts(), ["Widgets", "Parent", "Reset block", "Reset page", "Done"]);

    // While arranging, a job row does not open.
    await page.locator("[data-job-id]").first().click();
    assert.equal(await page.locator(".content-grid.with-detail").count(), 0, "a job opened in edit mode");
    assert.equal(await page.locator("[data-job-id][aria-pressed=true]").count(), 0);

    // Dragging moves the block, on the 8px grid.
    await dragBy(page, { x: start.x + 4, y: start.y + 4 }, 43, 22);
    const moved = { ...start, x: start.x + 40, y: start.y + 24 };
    assert.deepEqual(rounded(await filters.boundingBox()), moved, "the block did not follow the drag");
    assert.equal(await filters.evaluate((node) => node.style.translate), "40px 24px");

    // The right edge's handle sets the width.
    const handle = await page.locator('.page-edit [data-resize="width"]').boundingBox();
    await dragBy(page, { x: handle.x + handle.width / 2, y: handle.y + handle.height / 2 }, -200, 0);
    const width = Math.round((await filters.boundingBox()).width);
    assert.equal(width % 8, 0, "the width is off the grid");
    assert.ok(Math.abs(width - (start.width - 200)) <= 4, `width ${width}, from ${start.width}`);
    const left = { ...moved, width, height: Math.round((await filters.boundingBox()).height) };

    // Done saves once, to the build.
    const saved = page.waitForResponse((response) => response.url().endsWith("/api/build/preferences"));
    await bar(page).getByRole("button", { name: "Done" }).click();
    const response = await saved;
    assert.equal(response.status(), 200, await response.text());
    const entry = response.request().postDataJSON().ui.pages.moves;
    assert.equal(entry.length, 1);
    assert.match(entry[0], new RegExp(`^jobs\\|40,24,${width},\\|.*form\\.filters$`));
    assert.equal(await bar(page).count(), 0);
    // Out of edit mode the page answers again.
    await page.locator("[data-job-id]").first().click();
    await page.locator(".content-grid.with-detail").waitFor();

    await page.reload();
    await page.getByRole("region", { name: "Job results" }).locator("[data-job-id]").first().waitFor();
    await settled(page, FILTERS, left, "the block is not where it was left after a reload");

    // Reset page puts it back, and that is saved too.
    await page.keyboard.press("Control+Shift+Space");
    const cleared = page.waitForResponse((response) => response.url().endsWith("/api/build/preferences"));
    await bar(page).getByRole("button", { name: "Reset page" }).click();
    await settled(page, FILTERS, start, "Reset page left the block moved");
    await bar(page).getByRole("button", { name: "Done" }).click();
    assert.deepEqual((await cleared).request().postDataJSON().ui.pages, { moves: [], widgets: [] });
    assert.equal(await filters.getAttribute("style") ?? "", "");
    await page.reload();
    await page.getByRole("region", { name: "Job results" }).locator("[data-job-id]").first().waitFor();
    await page.waitForTimeout(400);
    await settled(page, FILTERS, start, "the reset did not survive a reload");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("the innermost block is outlined, Arrow Up takes its parent, Esc leaves, and search offers the mode", async () => {
    const { page, problems, context } = await open("jobs");
    await page.getByRole("region", { name: "Job results" }).locator("[data-job-id]").first().waitFor();
    await page.keyboard.press("Control+Shift+Space");
    await bar(page).waitFor();
    const inner = rounded(await page.locator("form.filters").boundingBox());
    await page.mouse.move(inner.x + 4, inner.y + 4);
    const outline = page.locator(".page-edit .outline");
    await outline.waitFor();
    await page.waitForFunction((width) => Math.round(document.querySelector(".page-edit .outline")?.getBoundingClientRect().width) === width, inner.width);
    assert.deepEqual(rounded(await outline.boundingBox()), inner);
    assert.equal(await outline.evaluate((node) => getComputedStyle(node).borderTopStyle), "dashed");
    await page.keyboard.press("ArrowUp");
    const outer = rounded(await page.locator("section.opportunities").boundingBox());
    await page.locator(".page-edit .grip").waitFor();
    assert.deepEqual(rounded(await page.locator(".page-edit .outline:has(.grip)").boundingBox()), outer, "Arrow Up did not take the parent block");
    await page.keyboard.press("Escape");
    await bar(page).waitFor({ state: "detached" });

    await page.keyboard.press("Control+k");
    await page.getByRole("dialog").locator("input").first().fill("edit this");
    await page.getByRole("dialog").getByText("Edit this page").first().click();
    await bar(page).waitFor();
    // Another page ends it.
    await page.evaluate(() => { location.hash = "calendar"; });
    await bar(page).waitFor({ state: "detached" });
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("a Clock dragged out of the Widgets menu stands on Find jobs after Done and a reload; Remove widget takes it off", async () => {
    // The demo's real build file takes the widget, so the server's schema is part of the test.
    const { page, problems, context } = await open("jobs", { ownBuild: false });
    const PANEL = '[data-kitty-panel="view"]:not([hidden])';
    const CLOCK = `${PANEL} > .page-widgets > [data-page-widget="clock"]`;
    const clock = page.locator(CLOCK);
    const rows = page.getByRole("region", { name: "Job results" }).locator("[data-job-id]");
    await rows.first().waitFor();
    await page.keyboard.press("Control+Shift+Space");
    const trigger = bar(page).getByRole("button", { name: "Widgets", exact: true });
    assert.equal(await trigger.getAttribute("aria-haspopup"), "menu");
    assert.equal(await trigger.getAttribute("aria-expanded"), "false");
    await trigger.click();
    const menu = bar(page).getByRole("menu", { name: "Widgets" });
    await menu.waitFor();
    assert.equal(await trigger.getAttribute("aria-expanded"), "true");
    const titles = await menu.getByRole("menuitem").allInnerTexts();
    assert.ok(titles.includes("Clock") && titles.includes("Quick note") && titles.includes("Today"), `the menu lists ${titles}`);
    // The menu is drawn flat, above its bar, and does not cover it.
    const look = await menu.evaluate((node) => { const style = getComputedStyle(node); return { blur: style.backdropFilter, shadow: style.boxShadow, radius: style.borderRadius, bottom: node.getBoundingClientRect().bottom }; });
    assert.deepEqual({ blur: look.blur, shadow: look.shadow, radius: look.radius }, { blur: "none", shadow: "none", radius: "14px" });
    assert.ok(look.bottom <= (await bar(page).boundingBox()).y, "the menu covers its bar");
    // Escape closes the menu and leaves the mode on.
    await page.keyboard.press("Escape");
    await menu.waitFor({ state: "detached" });
    assert.equal(await bar(page).count(), 1, "Escape in the menu left edit mode");

    // Dragging a row onto the page puts its widget where it is dropped. Places are read
    // against the page (the view panel), since the drag may scroll the window.
    const place = () => page.evaluate(([panel, pane]) => {
      const a = document.querySelector(panel).getBoundingClientRect();
      const b = document.querySelector(pane).getBoundingClientRect();
      return { x: Math.round(b.x - a.x), y: Math.round(b.y - a.y), width: Math.round(b.width) };
    }, [PANEL, CLOCK]);
    await trigger.click();
    const at = { x: 680, y: 340 };
    await menu.getByRole("menuitem", { name: "Clock" }).dragTo(page.locator(PANEL), { targetPosition: at });
    await clock.waitFor();
    await menu.waitFor({ state: "detached" });
    const dropped = await place();
    assert.ok(dropped.x <= at.x && at.x <= dropped.x + dropped.width && dropped.y <= at.y && at.y <= dropped.y + 100, `dropped at ${JSON.stringify(at)}, standing at ${JSON.stringify(dropped)}`);
    assert.equal(dropped.width, 360);
    assert.deepEqual([dropped.x % 8, dropped.y % 8], [0, 0], "the widget is off the grid");
    // It comes selected: Remove widget stands where Reset block was, and only its width can be set.
    await bar(page).getByRole("button", { name: "Remove widget" }).waitFor();
    assert.deepEqual(await bar(page).getByRole("button").allInnerTexts(), ["Widgets", "Parent", "Remove widget", "Reset page", "Done"]);
    assert.deepEqual(await page.locator(".page-edit [data-resize]").evaluateAll((nodes) => nodes.map((node) => node.dataset.resize)), ["width"]);
    assert.equal(await clock.locator("[inert]").count(), 1, "the widget's content answers while the page is arranged");
    // The pane is the page's colour with a hairline round it, and no shadow.
    const pane = await clock.evaluate((node) => { const style = getComputedStyle(node); return { shadow: style.boxShadow, radius: style.borderRadius, border: style.borderTopWidth, same: style.backgroundColor === getComputedStyle(document.body).backgroundColor || style.backgroundColor === "rgb(0, 0, 0)" }; });
    assert.deepEqual(pane, { shadow: "none", radius: "16px", border: "1px", same: true });

    // It moves by its pane and takes a width from the handle on its right edge.
    const standing = await clock.boundingBox();
    await dragBy(page, { x: standing.x + 30, y: standing.y + 40 }, 82, 38);
    const moved = { ...dropped, x: dropped.x + 80, y: dropped.y + 40 };
    assert.deepEqual(await place(), moved, "the widget did not follow the drag");
    const handle = await page.locator('.page-edit [data-resize="width"]').boundingBox();
    await dragBy(page, { x: handle.x + handle.width / 2, y: handle.y + handle.height / 2 }, 80, 0);
    const left = { ...moved, width: 440 };
    assert.deepEqual(await place(), left, "the handle did not set the width");

    const saved = page.waitForResponse((response) => response.url().endsWith("/api/build/preferences"));
    await bar(page).getByRole("button", { name: "Done" }).click();
    const response = await saved;
    assert.equal(response.status(), 200, await response.text());
    const pages = response.request().postDataJSON().ui.pages;
    assert.deepEqual(pages.moves, []);
    assert.equal(pages.widgets.length, 1);
    assert.equal(pages.widgets[0], `jobs|clock|${left.x},${left.y},440`);

    await page.reload();
    await rows.first().waitFor();
    await clock.waitFor();
    assert.deepEqual(await place(), left, "the widget is not where it was left after a reload");
    // Out of edit mode it is a working widget: it shows the time.
    assert.match(await clock.locator("time").innerText(), /\d{1,2}[:.]\d{2}/);
    assert.equal(await clock.locator("[inert]").count(), 0);

    // Edit again: the menu no longer offers it; selected, Remove widget takes it off, and that is saved too.
    await page.keyboard.press("Control+Shift+Space");
    await trigger.click();
    assert.equal((await menu.getByRole("menuitem").allInnerTexts()).includes("Clock"), false, "a widget on the page is still in the menu");
    // The open menu may lie over the widget: closed first, then the widget is pressed.
    await page.keyboard.press("Escape");
    await menu.waitFor({ state: "detached" });
    const again = await clock.boundingBox();
    await page.mouse.click(again.x + 30, again.y + 40);
    const cleared = page.waitForResponse((response) => response.url().endsWith("/api/build/preferences"));
    await bar(page).getByRole("button", { name: "Remove widget" }).click();
    await clock.waitFor({ state: "detached" });
    await bar(page).getByRole("button", { name: "Done" }).click();
    assert.deepEqual((await cleared).request().postDataJSON().ui.pages, { moves: [], widgets: [] });
    await page.reload();
    await rows.first().waitFor();
    await page.waitForTimeout(400);
    assert.equal(await clock.count(), 0, "the removed widget came back after a reload");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("a row pressed in the Widgets menu lands at the top right of the page; Reset page takes the widgets off too", async () => {
    const { page, problems, context } = await open("calendar");
    await page.getByRole("region", { name: "Calendar workspace" }).waitFor();
    await page.keyboard.press("Control+Shift+Space");
    await bar(page).getByRole("button", { name: "Widgets", exact: true }).click();
    await bar(page).getByRole("menuitem", { name: "Clock" }).click();
    const clock = page.locator('[data-kitty-panel="view"]:not([hidden]) [data-page-widget="clock"]');
    await clock.waitFor();
    assert.equal(await bar(page).getByRole("menu").count(), 0, "the menu stayed open after a pick");
    const box = await clock.boundingBox();
    assert.ok(box.x + box.width > VIEWPORT.width - 120 && box.y < 200, `a pressed widget landed at ${JSON.stringify(box)}`);
    await bar(page).getByRole("button", { name: "Reset page" }).click();
    await clock.waitFor({ state: "detached" });
    await page.keyboard.press("Escape");
    await bar(page).waitFor({ state: "detached" });
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("home's edit mode is a bar at the bottom: the Widgets menu, the colours, Hide Rika and Done; a Clock dragged in stays", async () => {
    const { page, problems, context } = await open("today");
    const board = page.getByRole("region", { name: "Home widgets" });
    await board.waitFor();
    const home = page.getByRole("toolbar", { name: "Home editing" });
    assert.equal(await home.count(), 0);
    // The page edit shortcut opens home's own edit mode.
    await page.keyboard.press("Control+Shift+Space");
    await home.waitFor();
    assert.equal(await bar(page).count(), 0, "the page editor opened on home");
    assert.match(await home.innerText(), /^Editing home/);
    const names = await home.getByRole("button").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label") || node.textContent.trim()));
    assert.equal(names[0], "Widgets");
    assert.equal(names[1], "None");
    assert.deepEqual(names.slice(-2), ["Hide Rika", "Done"]);
    const swatches = home.getByRole("group", { name: "Colours" }).getByRole("button");
    assert.equal(await swatches.count(), 7);
    assert.deepEqual(await swatches.first().evaluate((node) => [node.offsetWidth, node.offsetHeight, node.getAttribute("aria-pressed")]), [24, 24, "false"]);
    // No panel beside the board: Rika keeps its column, still, with the bar under both.
    assert.equal(await page.getByRole("complementary", { name: "Widget library" }).count(), 0);
    assert.equal(await page.locator(".rika-panel").evaluate((node) => node.closest("[inert]") !== null && node.getBoundingClientRect().width > 200), true, "Rika's panel left its column or still answers");
    assert.ok((await home.boundingBox()).y > VIEWPORT.height - 120, "the bar is not at the bottom");

    // A row dragged out of the menu lands in the column it is dropped on.
    await home.getByRole("button", { name: "Widgets", exact: true }).click();
    const menu = home.getByRole("menu", { name: "Widgets" });
    await menu.waitFor();
    const clock = board.locator('section[aria-labelledby$="-clock"]');
    assert.equal(await clock.count(), 0, "the demo's home already has a clock");
    await menu.getByRole("menuitem", { name: "Clock" }).dragTo(board.locator(".column").first(), { targetPosition: { x: 60, y: 30 } });
    await board.locator(".column").first().locator('section[aria-labelledby$="-clock"]').waitFor();
    await menu.waitFor({ state: "detached" });
    await home.getByRole("button", { name: "Widgets", exact: true }).click();
    assert.equal((await menu.getByRole("menuitem").allInnerTexts()).includes("Clock"), false, "a widget on home is still in the menu");
    await page.keyboard.press("Escape");
    await menu.waitFor({ state: "detached" });
    assert.equal(await home.count(), 1, "Escape in the menu left edit mode");

    // Hide Rika takes the panel off home, Show Rika brings it back.
    await home.getByRole("button", { name: "Hide Rika" }).click();
    assert.equal(await page.locator(".rika-panel").count(), 0);
    await home.getByRole("button", { name: "Show Rika" }).click();
    await page.locator(".rika-panel").waitFor();

    await home.getByRole("button", { name: "Done" }).click();
    await home.waitFor({ state: "detached" });
    assert.match(await clock.locator("time").innerText(), /\d{1,2}[:.]\d{2}/);
    await page.reload();
    await clock.waitFor();
    // The shortcut closes the mode it opened.
    await page.keyboard.press("Control+Shift+Space");
    await home.waitFor();
    await page.keyboard.press("Control+Shift+Space");
    await home.waitFor({ state: "detached" });
    assert.deepEqual(problems, []);
    await context.close();
  });
});
