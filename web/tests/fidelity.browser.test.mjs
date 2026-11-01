// The real app's type system (design Q4/Q5) in a real browser against the disposable
// synthetic backend (`kitty-web --demo`); no private workspace is read. On every
// audited screen and on Kitty and Liquid Glass: no italic chrome, at most three faces
// (Fraunces page titles, Inter UI, JetBrains Mono chrome) plus the Fredoka wordmark,
// and the retired faces are not bundled.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/fidelity.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer as createNetServer } from "node:net";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("..", import.meta.url));
const repository = fileURLToPath(new URL("../..", import.meta.url));
const FACES = ["Fraunces Variable", "Inter Variable", "JetBrains Mono Variable"];

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

/** A returning user's profile on `hash` with the dock hidden and `theme` applied. */
async function open(hash, theme) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 } });
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue(),
  );
  await context.addInitScript((id) => {
    localStorage.setItem("kitty.theme", id);
    localStorage.setItem("kitty.dock", JSON.stringify({ mode: "hidden", shown: "terminal", size: null, offset: { right: 16, bottom: 16 } }));
  }, theme);
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(`#${hash}`, base).href);
  return { page, problems, context };
}

/** Every visible element that draws text, with its face and style; editor content and em/i are content, not chrome. */
const inventory = (page) =>
  page.evaluate(async () => {
    await document.fonts.ready;
    const items = [];
    for (const node of document.querySelectorAll("body *")) {
      if (node.closest("[contenteditable], em, i, cite")) continue;
      const drawsText = [...node.childNodes].some((child) => child.nodeType === Node.TEXT_NODE && child.textContent.trim())
        || node.matches("input:not([type=checkbox], [type=radio], [type=range], [type=color], [type=hidden]), textarea, select");
      if (!drawsText) continue;
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      if (!rect.width || !rect.height || style.visibility === "hidden" || node.closest("[hidden], [inert]")) continue;
      items.push({
        text: (node.textContent || node.getAttribute("placeholder") || node.tagName).trim().slice(0, 40),
        tag: node.tagName,
        family: style.fontFamily.split(",")[0].replace(/["']/g, "").trim(),
        italic: style.fontStyle !== "normal",
        wordmark: Boolean(node.closest(".wordmark, [data-wordmark]")),
      });
    }
    return { items, bundled: [...new Set([...document.fonts].map((face) => face.family.replace(/["']/g, "")))] };
  });

const SCREENS = [
  ["notes", (page) => page.locator(".notes-workspace").first().waitFor()],
  ["study", (page) => page.getByRole("heading", { name: "Study", exact: true }).first().waitFor()],
  ["settings", (page) => page.getByRole("heading", { level: 1 }).first().waitFor()],
  ["today", (page) => page.locator(".atlas").first().waitFor()],
];

for (const theme of ["kitty", "liquid-glass"]) {
  describe(`type system on ${theme}`, () => {
    for (const [screen, ready] of SCREENS) {
      test(`${screen}: no italic chrome, at most three faces plus the wordmark`, async () => {
        const { page, problems, context } = await open(screen, theme);
        await ready(page);
        await page.waitForTimeout(600);
        const { items, bundled } = await inventory(page);
        assert.ok(items.length > 10, `only ${items.length} text elements found`);
        const italic = items.filter((item) => item.italic);
        assert.deepEqual(italic, [], "italic chrome");
        const faces = new Set(items.filter((item) => !item.wordmark).map((item) => item.family));
        assert.ok(faces.size <= 3, `${faces.size} faces: ${[...faces].join(", ")}`);
        for (const face of faces) assert.ok(FACES.includes(face), `${face} is not one of the three faces (${items.find((item) => item.family === face && !item.wordmark)?.text})`);
        const wordmark = items.filter((item) => item.wordmark);
        for (const item of wordmark) assert.ok(item.family === "Fredoka Variable" || FACES.includes(item.family));
        assert.ok(items.filter((item) => item.family === "Fredoka Variable").every((item) => item.wordmark), "Fredoka outside the wordmark");
        for (const gone of ["Newsreader Variable", "Space Grotesk Variable"]) assert.ok(!bundled.includes(gone), `${gone} is still bundled`);
        assert.deepEqual(problems, []);
        await context.close();
      });
    }
  });
}

describe("shared chrome controls", () => {
  test("workspace tabs and the system bar hold 28px controls; status reports as a dot on the tab", async () => {
    const { page, problems, context } = await open("notes", "kitty");
    await page.locator(".notes-workspace").first().waitFor();
    const heights = await page.evaluate(() =>
      [...document.querySelectorAll(".system-bar .br-btn, .workspace-tabbar .br-btn, .system-bar .pill, .system-bar .icon-button")]
        .filter((node) => node.getBoundingClientRect().height)
        .map((node) => [node.getAttribute("aria-label") || node.textContent.trim(), node.getBoundingClientRect().height]),
    );
    for (const [label, height] of heights) assert.ok(height === 28 || height === 32, `${label} is ${height}px`);
    // Notes reports its save state to its tab; nothing prints "Saved locally".
    const tab = page.getByRole("tab", { selected: true });
    const dot = tab.getByRole("img", { name: "Saved" });
    await dot.waitFor();
    assert.deepEqual(await dot.evaluate((node) => [node.getBoundingClientRect().width, node.getBoundingClientRect().height]), [6, 6]);
    assert.equal(await page.getByText("Saved locally", { exact: true }).count(), 0);
    // Shared components draw themed dropdowns, never the browser's own select.
    await page.getByText("Import PDF / Markdown", { exact: true }).click();
    const importer = page.getByRole("region", { name: "Import documents" });
    await importer.getByRole("combobox", { name: "Destination notebook" }).click();
    await page.getByRole("searchbox", { name: "Search Destination notebook" }).waitFor();
    assert.equal(await importer.locator("select").count(), 0, "the importer renders a native select");
    await page.keyboard.press("Escape");
    assert.deepEqual(problems, []);
    await context.close();
  });
});
