// Q21: Opportunities & projects is map-first and full-bleed. The map fills the page's one
// surface, the list slides in as a side drawer, and Filters and Add (a quick-add popover)
// sit in one floating control row over the map. Runs the real app against the disposable
// synthetic backend (`kitty-web --demo`); no private workspace is read.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/opportunities.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer as createNetServer } from "node:net";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("..", import.meta.url));
const repository = fileURLToPath(new URL("../..", import.meta.url));
const VIEWPORT = { width: 1440, height: 900 };
const SEED = [
  { title: "Brazos Hack 2027", category: "hackathon", location: "College Station", deadline: "2027-01-24" },
  { title: "Tidewater Trading Challenge", category: "quant", remote: true, deadline: "2027-01-12" },
  { title: "Open Atlas data challenge", category: "data science", remote: true, deadline: "2027-02-03" },
  { title: "Campus robotics build", category: "engineering", location: "College Station" },
];

let backend;
let server;
let browser;
let base;

function freePort() {
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
  const origin = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error(`demo backend did not start:\n${output}`)), 120_000);
    const read = (chunk) => {
      output += chunk;
      const match = output.match(/website: (http:\/\/\S+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    };
    backend.stdout.on("data", read);
    backend.stderr.on("data", read);
    backend.once("exit", (code) => { clearTimeout(timer); reject(new Error(`demo backend exited (${code}):\n${output}`)); });
  });
  for (const item of SEED) {
    const response = await fetch(`${origin}/api/opportunities`, {
      method: "POST",
      body: JSON.stringify(item),
      headers: { "Content-Type": "application/json", Origin: origin, "X-Kitty-Request": "1" },
    });
    assert.equal(response.status, 200, `seeding ${item.title}: ${await response.text()}`);
  }
  server = await createServer({
    root,
    logLevel: "error",
    server: {
      host: "127.0.0.1",
      port: 0,
      proxy: {
        "/api": {
          target: origin,
          changeOrigin: true,
          configure: (proxy) => proxy.on("proxyReq", (request) => request.setHeader("Origin", origin)),
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

async function open({ theme } = {}) {
  const context = await browser.newContext({ viewport: VIEWPORT });
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue());
  await context.addInitScript(() => localStorage.setItem("kitty.dock", JSON.stringify({
    mode: "hidden", shown: "terminal", float: false, step: "m", size: null, offset: { right: 16, bottom: 16 },
  })));
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("#opportunities", base).href);
  if (theme) await page.evaluate(async (id) => (await import("/src/lib/themes/index.ts")).setTheme(id), theme);
  await page.getByRole("toolbar", { name: "Opportunities controls" }).waitFor({ timeout: 60_000 });
  return { page, problems, context };
}

const rect = (locator) => locator.evaluate((node) => node.getBoundingClientRect().toJSON());
const controls = (page) => page.getByRole("toolbar", { name: "Opportunities controls" });
const nativeSelects = (page) => page.locator("main select").count();

describe("Opportunities & projects: map first", () => {
  test("the map fills one full-bleed surface; nothing inside it draws a second box", async () => {
    const { page, problems, context } = await open();
    const map = page.getByRole("region", { name: "Opportunities map" });
    const canvas = map.locator("canvas").first();
    await canvas.waitFor({ timeout: 60_000 });
    const surface = page.locator("section.opportunities");
    const outer = await rect(surface);
    const box = await rect(canvas);
    assert.ok(outer.height >= VIEWPORT.height * 0.6, `the map surface is only ${outer.height}px tall`);
    assert.ok(Math.abs(box.width - outer.width) <= 2 && Math.abs(box.height - outer.height) <= 2,
      `the canvas ${box.width}×${box.height} does not fill its surface ${outer.width}×${outer.height}`);
    // Every box between the canvas and the page: at most one draws a border (the surface).
    const boxed = await canvas.evaluate((node) => {
      const found = [];
      for (let el = node.parentElement; el && el.tagName !== "MAIN"; el = el.parentElement) {
        const style = getComputedStyle(el);
        const border = parseFloat(style.borderTopWidth) > 0 && style.borderTopColor !== "rgba(0, 0, 0, 0)" && style.borderTopStyle !== "none";
        if (border) found.push(el.className || el.tagName);
      }
      return found;
    });
    assert.ok(boxed.length <= 1, `nested boxes around the map: ${boxed.join(" > ")}`);
    assert.equal(await page.locator("main details").count(), 0, "a boxed fold-out is left on the page");
    assert.equal(await page.locator("main").getByText(/^Demo:/).count(), 0, "the demo paragraph is still shown");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Filters, List and Add sit in one floating row over the map, on the control height", async () => {
    const { page, problems, context } = await open();
    const row = controls(page);
    await row.waitFor();
    assert.equal(await row.evaluate((node) => getComputedStyle(node).position), "absolute", "the control row is not floating");
    const outer = await rect(page.locator("section.opportunities"));
    const bar = await rect(row);
    assert.ok(bar.top >= outer.top && bar.bottom <= outer.top + 80, `the row is not over the top of the map: ${bar.top}–${bar.bottom}`);
    for (const name of ["Filters", "List", "Add", "More actions"]) {
      const button = row.getByRole("button", { name, exact: name !== "List" });
      assert.ok(await button.first().isVisible(), `${name} is not in the floating row`);
      const height = (await rect(button.first())).height;
      assert.ok([28, 32].includes(Math.round(height)), `${name} is ${height}px tall`);
    }
    assert.ok(await row.getByRole("searchbox", { name: "Search opportunities" }).isVisible());
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("the list slides in as a side drawer on the right and opening a row edits it in the drawer", async () => {
    const { page, problems, context } = await open();
    const list = page.getByRole("complementary", { name: "Opportunities list" });
    assert.equal(await list.count(), 0, "the list drawer is open before asking");
    const toggle = controls(page).getByRole("button", { name: /^List/ });
    assert.equal(await toggle.getAttribute("aria-pressed"), "false");
    await toggle.click();
    await list.waitFor();
    assert.equal(await toggle.getAttribute("aria-pressed"), "true");
    const outer = await rect(page.locator("section.opportunities"));
    const drawer = await rect(list);
    assert.ok(Math.abs(drawer.right - outer.right) <= 2, "the list drawer is not docked to the right edge");
    assert.ok(drawer.width <= 380, `the list drawer is ${drawer.width}px wide`);
    for (const { title } of SEED) await list.getByRole("button", { name: new RegExp(title) }).waitFor();
    await list.getByRole("button", { name: /Tidewater Trading Challenge/ }).click();
    const editor = page.getByRole("dialog", { name: "Tidewater Trading Challenge" });
    await editor.waitFor();
    assert.equal(await editor.getByRole("textbox", { name: "Title" }).inputValue(), "Tidewater Trading Challenge");
    assert.ok(await editor.getByRole("combobox", { name: "Status" }).isVisible(), "status is not a themed dropdown");
    assert.equal(await nativeSelects(page), 0, "a native select is left in the editor");
    await page.keyboard.press("Escape");
    await editor.waitFor({ state: "detached" });
    // The choice is remembered.
    await page.reload();
    await list.waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Add opens a quick-add popover: type a title, Enter saves it into the list", async () => {
    const { page, problems, context } = await open();
    await controls(page).getByRole("button", { name: "Add", exact: true }).click();
    const popover = page.getByRole("dialog", { name: "Add" });
    await popover.waitFor();
    assert.ok(await popover.getByRole("combobox", { name: "Category" }).isVisible(), "no category chip");
    const created = page.waitForResponse((response) => response.url().endsWith("/api/opportunities") && response.request().method() === "POST");
    await popover.getByRole("textbox", { name: "Add" }).fill("Robotics build night");
    await page.keyboard.press("Enter");
    const response = await created;
    assert.equal(response.status(), 200);
    assert.equal(JSON.parse(response.request().postData()).title, "Robotics build night");
    await popover.waitFor({ state: "detached" });
    const list = page.getByRole("complementary", { name: "Opportunities list" });
    if (!(await list.count())) await controls(page).getByRole("button", { name: /^List/ }).click();
    await list.getByRole("button", { name: /Robotics build night/ }).waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Filters is a popover of themed dropdowns that narrows the list", async () => {
    const { page, problems, context } = await open();
    await controls(page).getByRole("button", { name: "Filters", exact: true }).click();
    const filters = page.getByRole("dialog", { name: "Filters" });
    await filters.waitFor();
    for (const name of ["Source", "Review status", "Dates"]) assert.ok(await filters.getByRole("combobox", { name }).isVisible(), `${name} is not a themed dropdown`);
    assert.ok(await filters.getByRole("group", { name: "Remote" }).isVisible(), "Remote is not segmented");
    assert.equal(await nativeSelects(page), 0, "a native select is left in Filters");
    const filtered = page.waitForRequest((request) => /\/api\/opportunities\?.*remote=true/.test(request.url()));
    await filters.getByRole("group", { name: "Remote" }).getByRole("button", { name: "Remote", exact: true }).click();
    await filtered;
    const list = page.getByRole("complementary", { name: "Opportunities list" });
    await list.waitFor();
    await list.getByRole("button", { name: /Tidewater Trading Challenge/ }).waitFor();
    await list.getByRole("button", { name: /Brazos Hack 2027/ }).waitFor({ state: "detached" });
    assert.match(await controls(page).getByRole("button", { name: /^Filters/ }).textContent(), /1/, "the active filter is not counted");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("⋯ holds Interests & monitor and Import; each opens in the side drawer", async () => {
    const { page, problems, context } = await open();
    const more = controls(page).getByRole("button", { name: "More actions" });
    await more.click();
    const menu = page.getByRole("menu");
    await menu.getByRole("menuitem", { name: "Interests & monitor" }).click();
    const monitor = page.getByRole("dialog", { name: "Interests & monitor" });
    await monitor.waitFor();
    assert.ok(await monitor.getByRole("textbox", { name: "Major", exact: true }).isVisible());
    await page.keyboard.press("Escape");
    await monitor.waitFor({ state: "detached" });
    await more.click();
    await page.getByRole("menu").getByRole("menuitem", { name: /Import/ }).click();
    const importer = page.getByRole("dialog", { name: "Import opportunities" });
    await importer.waitFor();
    assert.ok(await importer.getByRole("textbox", { name: /JSON/ }).isVisible());
    assert.equal(await nativeSelects(page), 0);
    assert.deepEqual(problems, []);
    await context.close();
  });
});
