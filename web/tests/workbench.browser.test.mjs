// The real app in a real browser against the disposable synthetic backend
// (`kitty-web --demo`); no private workspace is read. Covers the atlas fitting
// the window above the dock, and selected toggles staying readable.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/workbench.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer as createNetServer } from "node:net";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("..", import.meta.url));
const repository = fileURLToPath(new URL("../..", import.meta.url));
// The native window's default and minimum inner sizes (desktop/src/main.rs).
const DEFAULT = { width: 1280, height: 850 };
const MINIMUM = { width: 760, height: 500 };
// The minimum window as WebKitGTK lays it out with slight display scaling
// (measured 730×480 CSS px at devicePixelRatio 1.04 in the native shell).
const SCALED_MINIMUM = { width: 730, height: 480 };
// A light palette: the bundled themes are all dark.
const PAPER = {
  id: "paper-test",
  label: "Paper",
  scheme: "light",
  roles: {
    void: "#f4f1f2", panel: "#ffffff", inset: "#ece8ea", raised: "#ffffff", line: "#c9c2c5", lineHot: "#7d7378",
    ink: "#1f1a1c", mute: "#4f474b", dim: "#6e6569", structure: "#8a3f5f", signal: "#9b3360", go: "#2f6f45",
    reference: "#285d8f", caution: "#7a5a00", alternate: "#5f4b9b", danger: "#a3352c",
  },
  graph: ["#8a3f5f", "#5f4b9b", "#285d8f", "#9b3360", "#2f6f45", "#7a5a00"],
  shadow: "#000000",
};

let backend;
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
      port: pinnedPort ? pinnedPort + 1 : 0,
      strictPort: Boolean(pinnedPort),
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
  browser = await chromium.launch();
});
after(async () => {
  await browser?.close();
  await server?.close();
  backend?.kill();
});

/** Opens a fresh profile on `hash`. `modules: []` is a core-only workspace. The profile is a
 *  returning user: the first-run welcome guide (covered by its own tests) is already done. */
async function open(hash, { viewport = DEFAULT, modules, dock = "shown", theme } = {}) {
  const context = await browser.newContext({ viewport });
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue(),
  );
  if (dock === "hidden")
    await context.addInitScript(() => localStorage.setItem("kitty.dock", JSON.stringify({ mode: "hidden", height: null, shown: "terminal" })));
  if (modules)
    await context.route("**/api/bootstrap", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.modules.enabled = modules;
      body.modules.configured = modules;
      await route.fulfill({ response, json: body });
    });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(`#${hash}`, base).href);
  if (typeof theme === "string")
    await page.evaluate(async (id) => (await import("/src/lib/themes.ts")).setTheme(id), theme);
  else if (theme)
    await page.evaluate(async (palette) => {
      const [{ THEMES }, { setTheme }] = await Promise.all([import("/src/lib/themeCatalog.ts"), import("/src/lib/themes.ts")]);
      THEMES.push(palette);
      setTheme(palette.id);
    }, theme);
  return { page, problems, context };
}

/** Viewport bottom not covered by the fixed dock or its handle. */
const visibleBottom = (page) => page.evaluate(() =>
  innerHeight - (parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--dock-space")) || 0));
const rect = (locator) => locator.evaluate((node) => node.getBoundingClientRect().toJSON());

/**
 * Polls until the 2D atlas canvas fills its stage and nothing is drawn in a
 * band along its edges: every node, label and region is inside the canvas.
 */
async function fitted(page) {
  await page.waitForFunction(() => {
    const stage = document.querySelector(".atlas .stage");
    const canvas = stage?.querySelector("canvas");
    if (!canvas || !canvas.width || !canvas.height) return false;
    const box = canvas.getBoundingClientRect(), outer = stage.getBoundingClientRect();
    if (Math.abs(box.height - outer.height) > 2 || Math.abs(box.width - outer.width) > 2) return false;
    const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    const band = Math.ceil(4 * canvas.width / box.width);
    let drawn = 0;
    for (let y = 0; y < canvas.height; y += 1)
      for (let x = 0; x < canvas.width; x += 1)
        if ((x < band || y < band || x >= canvas.width - band || y >= canvas.height - band) && pixels[(y * canvas.width + x) * 4 + 3]) drawn += 1;
    return drawn === 0 && canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data.some((value, index) => index % 4 === 3 && value);
  }, null, { timeout: 10_000, polling: 200 });
}

/** The atlas ends above the dock, the page does not scroll and the graph is fitted. */
async function assertAtlasFits(page, label) {
  const atlas = page.locator(".atlas");
  await atlas.waitFor();
  await fitted(page).catch(() => assert.fail(`${label}: graph is not fitted to its visible canvas`));
  const bottom = await visibleBottom(page);
  const box = await rect(atlas);
  assert.ok(box.top >= 0 && box.bottom <= bottom + 1, `${label}: atlas ${box.top}–${box.bottom} is not within 0–${bottom}`);
  assert.ok(box.height >= 120, `${label}: atlas collapsed to ${box.height}px`);
  const overflow = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
  assert.ok(overflow <= 1, `${label}: page scrolls ${overflow}px below the window`);
}

/** Every control in the sidebar can be focused and is then visible and hit-testable above the dock. */
async function assertSidebarReachable(page, label) {
  const unreachable = await page.locator(".atlas-panel").evaluate((panel) => {
    const space = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--dock-space")) || 0;
    const missing = [];
    for (const control of panel.querySelectorAll("a[href], button, input, summary")) {
      if (!control.getClientRects().length || control.closest("details:not([open]) > :not(summary)")) continue;
      control.focus();
      const box = control.getBoundingClientRect();
      const x = box.left + box.width / 2, y = box.top + box.height / 2;
      const hit = document.elementFromPoint(x, y);
      if (y < 0 || y > innerHeight - space || !hit || !(control === hit || control.contains(hit)))
        missing.push(control.getAttribute("aria-label") || control.textContent.trim().slice(0, 30));
    }
    return missing;
  });
  assert.deepEqual(unreachable, [], `${label}: sidebar controls cannot be reached`);
}

describe("atlas fits the window above the dock", () => {
  for (const viewport of [DEFAULT, MINIMUM, SCALED_MINIMUM]) {
    const size = `${viewport.width}×${viewport.height}`;
    for (const [name, options] of [
      ["all modules, dock shown", {}],
      ["all modules, dock hidden", { dock: "hidden" }],
      ["core only", { modules: [] }],
    ]) {
      test(`${size}, ${name}`, async () => {
        const { page, problems, context } = await open("today", { viewport, ...options });
        if (!options.modules && options.dock !== "hidden") await page.locator('[data-kitty-panel="dock"]').waitFor();
        await assertAtlasFits(page, `${size} ${name}`);
        await assertSidebarReachable(page, `${size} ${name}`);
        assert.deepEqual(problems, []);
        await context.close();
      });
    }

    test(`${size}, F fullscreens the fitted graph; Escape and the dock toggle refit it`, async () => {
      const { page, problems, context } = await open("today", { viewport });
      const dock = page.locator('[data-kitty-panel="dock"]');
      await dock.waitFor();
      await assertAtlasFits(page, `${size} initial`);
      const stage = page.locator('[data-kitty-panel="graph"]');
      await stage.focus();
      await page.keyboard.press("f");
      await page.waitForFunction(() => document.querySelector('[data-kitty-panel="graph"]')?.hasAttribute("data-panel-fullscreen"));
      await fitted(page);
      const full = await rect(stage);
      assert.deepEqual([full.top, full.left, full.width, full.height], [0, 0, viewport.width, viewport.height]);
      await page.keyboard.press("Escape");
      await assertAtlasFits(page, `${size} after Escape`);
      await assertSidebarReachable(page, `${size} after Escape`);
      await dock.getByRole("button", { name: "Hide dock" }).click();
      await assertAtlasFits(page, `${size} dock hidden`);
      assert.ok((await rect(page.locator(".atlas"))).bottom > (await visibleBottom(page)) - 40, `${size}: atlas did not grow into the hidden dock's space`);
      await page.getByRole("button", { name: "Show dock" }).click();
      await dock.waitFor();
      await assertAtlasFits(page, `${size} dock shown again`);
      assert.deepEqual(problems, []);
      await context.close();
    });
  }
});

/** WCAG contrast of two [r, g, b] colors. */
function contrast(a, b) {
  const luminance = (rgb) => {
    const [r, g, b] = rgb.map((value) => { const c = value / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

/**
 * What is actually painted inside a button (border and focus ring excluded):
 * its dominant fill and the glyph pixel that stands out most from it.
 */
async function painted(page, button) {
  // Theme transitions and view fade-ins (on the button or an ancestor) must finish first.
  await button.evaluate(async (node) => {
    const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
    const moving = () => document.getAnimations().some((animation) =>
      animation.playState === "running" && animation.effect?.getComputedTiming().endTime !== Infinity &&
      animation.effect?.target?.contains(node));
    for (let quiet = 0; quiet < 3; quiet = moving() ? 0 : quiet + 1) await frame();
  });
  const box = await rect(button);
  const png = await page.screenshot({ clip: { x: box.left + 6, y: box.top + 4, width: box.width - 12, height: box.height - 8 } });
  const pixels = await page.evaluate(async (data) => {
    const bitmap = await createImageBitmap(await (await fetch(`data:image/png;base64,${data}`)).blob());
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const context = canvas.getContext("2d");
    context.drawImage(bitmap, 0, 0);
    return Array.from(context.getImageData(0, 0, bitmap.width, bitmap.height).data);
  }, png.toString("base64"));
  const counts = new Map();
  const colors = [];
  for (let index = 0; index < pixels.length; index += 4) {
    const color = pixels.slice(index, index + 3);
    colors.push(color);
    const key = color.join();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const fill = [...counts].sort((a, b) => b[1] - a[1])[0][0].split(",").map(Number);
  const text = colors.reduce((best, color) => (contrast(color, fill) > contrast(best, fill) ? color : best), fill);
  return { fill, text, ratio: contrast(fill, text) };
}

async function assertSelection(page, selected, other, label) {
  assert.equal(await selected.getAttribute("aria-pressed"), "true", `${label}: wrong selected button`);
  assert.equal(await other.getAttribute("aria-pressed"), "false", `${label}: wrong unselected button`);
  const on = await painted(page, selected);
  const off = await painted(page, other);
  assert.ok(on.ratio >= 4.5, `${label}: selected text contrast ${on.ratio.toFixed(2)} (${on.text} on ${on.fill})`);
  assert.ok(off.ratio >= 4.5, `${label}: unselected text contrast ${off.ratio.toFixed(2)} (${off.text} on ${off.fill})`);
  assert.ok(contrast(on.fill, off.fill) >= 3, `${label}: selection is not distinct (${on.fill} vs ${off.fill})`);
}

describe("selected job views stay readable", () => {
  for (const [themeName, theme] of [["Kitty (default)", undefined], ["Liquid Glass", "liquid-glass"], ["a light theme", PAPER]]) {
    for (const viewport of [DEFAULT, MINIMUM]) {
      test(`${themeName} at ${viewport.width}px: Map and List show a readable, distinct selection`, async () => {
        // The dock's first height depends on how much of Jobs loaded; keep it out of the measured area.
        const { page, problems, context } = await open("jobs", { viewport, theme, dock: "hidden" });
        const views = page.getByRole("navigation", { name: "Jobs view" });
        const map = views.getByRole("button", { name: "Map", exact: true });
        const list = views.getByRole("button", { name: "List", exact: true });
        await assertSelection(page, map, list, `${themeName} ${viewport.width} initial`);
        await list.focus();
        await page.keyboard.press("Enter");
        await page.waitForFunction(() => document.querySelector('nav[aria-label="Jobs view"] button:nth-child(2)')?.getAttribute("aria-pressed") === "true");
        assert.equal(await list.evaluate((node) => node === document.activeElement && node.matches(":focus-visible")), true, "keyboard focus left List");
        assert.equal(await list.evaluate((node) => getComputedStyle(node).outlineStyle), "solid", "List lost its focus ring");
        await page.mouse.move(0, 0);
        await assertSelection(page, list, map, `${themeName} ${viewport.width} after choosing List`);
        await map.click();
        await page.mouse.move(0, 0);
        await assertSelection(page, map, list, `${themeName} ${viewport.width} after choosing Map`);
        assert.deepEqual(problems, []);
        await context.close();
      });
    }
  }
});

describe("type system", () => {
  test("titles, headings, labels and the wordmark render in their bundled faces at the compact scale", async () => {
    const { page, problems, context } = await open("settings", { dock: "hidden" });
    await page.locator("h1").first().waitFor();
    const type = await page.evaluate(async () => {
      // Probes for elements Settings does not render; global type rules apply to them as anywhere else.
      document.body.insertAdjacentHTML("beforeend", '<div id="type-probe"><h3>Heading</h3><span class="eyebrow">Label</span></div>');
      void document.body.offsetHeight;
      await new Promise((resolve) => requestAnimationFrame(resolve));
      await document.fonts.ready;
      const face = (selector) => {
        const node = document.querySelector(selector);
        if (!node) return null;
        const style = getComputedStyle(node);
        const family = style.fontFamily.split(",")[0].replace(/["']/g, "").trim();
        return { family, loaded: document.fonts.check(`${style.fontStyle} ${style.fontWeight} 16px "${family}"`), style: style.fontStyle };
      };
      return {
        body: getComputedStyle(document.body).fontSize,
        title: face("h1"),
        heading: face("#type-probe h3"),
        panel: face(".br-panel__header"),
        label: face("#type-probe .eyebrow"),
        wordmark: face(".wordmark .word"),
        loadedFamilies: [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family.replace(/["']/g, "")),
      };
    });
    assert.equal(type.body, "13px");
    assert.equal(type.title?.family, "Fraunces Variable");
    assert.equal(type.heading?.family, "Space Grotesk Variable");
    assert.equal(type.panel?.family, "Space Grotesk Variable");
    assert.equal(type.label?.family, "Newsreader Variable");
    assert.equal(type.label?.style, "italic");
    assert.equal(type.wordmark?.family, "Fredoka Variable");
    for (const family of ["Fraunces Variable", "Space Grotesk Variable", "Newsreader Variable", "Fredoka Variable", "Inter Variable"])
      assert.ok(type.loadedFamilies.includes(family), `${family} was not downloaded and applied (loaded: ${type.loadedFamilies.join(", ")})`);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("flashcard view switcher", () => {
  test("the current flashcard view stays readable and distinct on the default theme", async () => {
    const { page, problems, context } = await open("flashcards", { dock: "hidden" });
    const views = page.getByRole("navigation", { name: "Flashcard views" });
    const current = views.locator('button[aria-current="page"]');
    await current.waitFor();
    const other = views.locator("button:not([aria-current])").first();
    const on = await painted(page, current);
    const off = await painted(page, other);
    assert.ok(on.ratio >= 4.5, `current view text contrast ${on.ratio.toFixed(2)} (${on.text} on ${on.fill})`);
    assert.ok(off.ratio >= 4.5, `other view text contrast ${off.ratio.toFixed(2)} (${off.text} on ${off.fill})`);
    assert.ok(contrast(on.fill, off.fill) >= 3, `current view is not distinct (${on.fill} vs ${off.fill})`);
    const heights = await views.locator("button").evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().height));
    assert.ok(Math.max(...heights) <= 40, `view buttons are stretched to ${Math.max(...heights)}px`);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("command palette", () => {
  test("Spotlight palette: empty until you type, six results with a top hit, Enter opens it, Escape closes", async () => {
    const { page, problems, context } = await open("today", { dock: "hidden" });
    await page.locator(".wordmark").waitFor();
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Search kitty!" });
    await palette.waitFor();
    const field = palette.getByRole("combobox");
    assert.equal(await field.evaluate((node) => node === document.activeElement), true, "search field is not focused");
    assert.equal(await palette.getByRole("option").count(), 0, "results show before typing");
    await field.fill("a");
    await page.waitForFunction(() => document.querySelectorAll('dialog [role="option"]').length > 0);
    assert.ok((await palette.getByRole("option").count()) <= 6, "more than six results");
    await field.fill("calen");
    const top = palette.locator('[role="option"][data-top="true"]');
    await page.waitForFunction(() => document.querySelector('[role="option"][data-top="true"]')?.textContent?.includes("Calendar"));
    assert.equal(await top.getAttribute("aria-selected"), "true");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => location.hash === "#calendar");
    assert.equal(await palette.count(), 0, "palette stayed open after opening a result");
    await page.keyboard.press("Control+k");
    await palette.waitFor();
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.querySelector('dialog[aria-label="Search kitty!"]'));
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("workspace tabs", () => {
  test("underline tabs: icon per tab, the active tab underlined in its group colour, close shown on hover", async () => {
    const { page, problems, context } = await open("calendar", { dock: "hidden" });
    const bar = page.getByRole("navigation", { name: "Workspace tabs" });
    await bar.waitFor();
    await page.keyboard.press("Control+t");
    await page.waitForFunction(() => document.querySelectorAll('[role="tab"]').length === 2);
    const tabs = bar.locator(".tab");
    const [first, second] = [tabs.nth(0), tabs.nth(1)];
    assert.equal(await second.getAttribute("class").then((c) => c.includes("active")), true, "new tab is not active");
    for (const tab of [first, second]) assert.equal(await tab.locator("svg").count(), 1, "tab has no icon");
    const underline = (tab) => tab.evaluate((node) => {
      const after = getComputedStyle(node, "::after");
      return { height: after.height, color: after.backgroundColor, tone: getComputedStyle(node).getPropertyValue("--tone").trim() };
    });
    const active = await underline(second);
    assert.equal(active.height, "2px", "active tab has no underline");
    assert.notEqual(active.tone, "", "active tab has no group tone");
    assert.notEqual((await underline(first)).height, "2px", "inactive tab is underlined");
    const close = first.locator(".close-tab");
    assert.equal(await close.evaluate((node) => getComputedStyle(node).visibility), "hidden", "inactive close button shows at rest");
    await first.hover();
    assert.equal(await close.evaluate((node) => getComputedStyle(node).visibility), "visible", "close button does not show on hover");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("empty states", () => {
  test("an empty Flashcards collection shows the sleeping kitty with its title and actions; motion stops when reduced", async () => {
    const { page, problems, context } = await open("flashcards", { dock: "hidden" });
    const empty = page.locator(".kitty-empty").first();
    await empty.waitFor();
    assert.equal(await empty.locator("svg.sleeping-cat").count(), 1, "no sleeping cat drawn");
    assert.match(await empty.getByRole("heading").first().textContent(), /first card/i);
    assert.ok(await empty.getByRole("button").count() >= 1, "empty state has no action");
    const box = await empty.locator("svg.sleeping-cat").boundingBox();
    assert.ok(box && box.width >= 100 && box.width <= 200, `cat is ${box?.width}px wide`);
    const animated = (page) => page.evaluate(() => getComputedStyle(document.querySelector(".kitty-empty svg.sleeping-cat .zs text")).animationName);
    assert.notEqual(await animated(page), "none", "z's do not drift");
    await page.emulateMedia({ reducedMotion: "reduce" });
    assert.equal(await animated(page), "none", "z's still drift under reduced motion");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("loaders", () => {
  test("lists load behind shimmering skeleton rows; whole views behind blinking cat eyes", async () => {
    const { page, problems, context } = await open("today", { dock: "hidden" });
    await page.locator(".wordmark").waitFor();
    let release;
    const held = new Promise((resolve) => (release = resolve));
    await page.route((url) => url.pathname === "/api/jobs", async (route) => { await held; await route.continue(); });
    await page.evaluate(() => (location.hash = "inbox"));
    const rows = page.locator('[role="status"].kitty-loader[data-kind="rows"]');
    await rows.waitFor();
    assert.ok((await rows.locator(".skeleton-row").count()) >= 3, "too few skeleton rows");
    assert.match(await rows.getAttribute("aria-label"), /loading/i);
    assert.notEqual(await rows.locator(".skeleton-row span").first().evaluate((node) => getComputedStyle(node).animationName), "none");
    release();
    await rows.waitFor({ state: "detached" });
    let releaseAtlas;
    const atlasHeld = new Promise((resolve) => (releaseAtlas = resolve));
    await page.route((url) => url.pathname === "/api/atlas", async (route) => { await atlasHeld; await route.continue(); });
    await page.keyboard.press("Control+t");
    const eyes = page.locator('[role="status"].kitty-loader[data-kind="eyes"]').filter({ hasText: "Drawing your atlas…" });
    await eyes.waitFor();
    assert.equal(await eyes.locator(".eye").count(), 2);
    assert.notEqual(await eyes.locator(".eye").first().evaluate((node) => getComputedStyle(node).animationName), "none", "eyes do not blink");
    releaseAtlas();
    await eyes.waitFor({ state: "detached" });
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("study timer", () => {
  test("a running session shows as a ring in the Study card and in the top bar, whose popover pauses it", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    await page.getByRole("button", { name: /Start session/ }).first().click();
    const card = page.locator(".timer-card .timer-ring");
    await card.waitFor();
    assert.match(await card.getAttribute("aria-label"), /Priming, \d+:\d\d left of 5:00/);
    const offset = await card.locator(".progress").evaluate((node) => Number(getComputedStyle(node).strokeDashoffset.replace("px", "")));
    assert.ok(offset >= 0, "progress arc has no dash offset");
    const top = page.locator(".system-bar").getByRole("button", { name: /^Study timer/ });
    await top.waitFor();
    assert.equal(await top.locator(".timer-ring").count(), 1, "top bar timer has no ring");
    assert.match(await top.textContent(), /\d\d:\d\d/);
    assert.equal(await page.locator("aside.focus-bar").count(), 0, "the full-width focus strip is still shown");
    await top.click();
    const pause = page.getByRole("button", { name: "Pause", exact: true });
    await pause.click();
    await page.waitForFunction(() => /paused/i.test(document.querySelector('.system-bar [aria-label^="Study timer"]')?.getAttribute("aria-label") ?? ""));
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("study guide", () => {
  test("the 14 steps sit in three phase columns; the rail view is one click away and remembered", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    await page.getByRole("button", { name: "Study session", exact: true }).click();
    const guide = page.getByRole("region", { name: "Fourteen-step study guide" });
    await guide.waitFor();
    const columns = guide.locator('[data-guide-view="columns"] > section');
    assert.equal(await columns.count(), 3, "guide is not three phase columns");
    assert.deepEqual(await columns.locator("h4").allTextContents(), ["Prepare & prime", "During the session", "Test, review & return"]);
    assert.equal(await guide.getByRole("checkbox").count(), 14);
    await guide.getByRole("checkbox").first().check();
    await page.waitForFunction(() => document.querySelector('[aria-label="Fourteen-step study guide"] [role="status"]')?.textContent?.startsWith("1/14"));
    await guide.getByRole("radio", { name: "Rail" }).click();
    await guide.locator('[data-guide-view="rail"]').waitFor();
    assert.equal(await guide.locator('[data-guide-view="columns"]').count(), 0);
    assert.equal(await page.evaluate(() => localStorage.getItem("kitty.study.guide-view")), "rail");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

/** Creates deck `name` holding Basic front/back notes through the API (the demo collection starts empty). */
function seedDeck(page, name, cards) {
  return page.evaluate(async ({ name, cards }) => {
    const post = async (path, body) => {
      const response = await fetch(`/api${path}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Kitty-Request": "1" }, body: JSON.stringify(body) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      return data;
    };
    const overview = await (await fetch("/api/flashcards", { cache: "no-store" })).json();
    const basic = overview.types.find((type) => type.kind === "basic");
    const { deck } = await post("/flashcards/deck", { name, settings: {} });
    for (const [front, back] of cards) await post("/flashcards/note", { deck_id: deck.id, type_id: basic.id, fields: { Front: front, Back: back }, tags: [] });
    return deck.id;
  }, { name, cards });
}

/** Opens Flashcards with `deck` seeded and its due review started. */
async function reviewDeck(name, cards) {
  const opened = await open("flashcards", { dock: "hidden" });
  const { page } = opened;
  await page.getByRole("navigation", { name: "Flashcard views" }).waitFor();
  await seedDeck(page, name, cards);
  await page.reload();
  await page.getByRole("combobox", { name: /deck scope/ }).selectOption({ label: name });
  await page.getByRole("button", { name: "Review due" }).first().click();
  return opened;
}

describe("flashcard review", () => {
  test("keyboard strip: keycaps 1–4 and Space with intervals press down when used", async () => {
    const { page, problems, context } = await reviewDeck("Strip deck", [["Capital of Peru?", "Lima"], ["Capital of Chile?", "Santiago"]]);
    const strip = page.getByRole("group", { name: "Grade keys" });
    await strip.waitFor();
    const keys = strip.getByRole("button");
    assert.deepEqual((await keys.allTextContents()).map((text) => text.replace(/\s+/g, " ").trim().split(" ").slice(0, 2).join(" ")),
      ["1 Again", "2 Hard", "3 Good", "4 Easy", "Space Flip"]);
    for (const index of [0, 1, 2, 3]) assert.equal(await keys.nth(index).isDisabled(), true, "grades are enabled before the answer shows");
    await page.keyboard.press("Space");
    await page.waitForFunction(() => document.querySelector('[role="group"][aria-label="Grade keys"] button')?.textContent?.match(/\d+(m|h|d)/));
    for (const index of [0, 1, 2, 3]) assert.match(await keys.nth(index).textContent(), /\d+(m|h|d)/, `key ${index + 1} shows no interval`);
    assert.match(await keys.nth(4).textContent(), /Space\s*Good/);
    // The pressed keycap sinks while the grade is in flight.
    await page.emulateMedia({ reducedMotion: "reduce" });
    let release;
    const held = new Promise((resolve) => (release = resolve));
    await page.route("**/api/flashcards/grade", async (route) => { await held; await route.continue(); });
    const cap = keys.nth(1).locator("kbd");
    const before = (await cap.boundingBox()).y;
    const remaining = await page.getByText(/eligible cards/).textContent();
    await page.keyboard.press("2");
    await page.waitForFunction((y) => document.querySelectorAll('[role="group"][aria-label="Grade keys"] kbd')[1].getBoundingClientRect().y > y, before);
    release();
    await page.waitForFunction((text) => !document.body.innerText.includes(text), remaining.trim());
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("Liquid Glass theme", () => {
  test("panels are clear, neutral, lit glass like macOS/iOS, never rainbow-tinted, and their text stays readable", async () => {
    const { page, problems, context } = await open("settings", { dock: "hidden" });
    await page.locator(".br-panel").first().waitFor();
    await page.evaluate(async () => (await import("/src/lib/themes.ts")).setTheme("liquid-glass"));
    await page.waitForFunction(() => document.documentElement.dataset.theme === "liquid-glass");
    const glass = await page.evaluate(() => {
      const panel = document.querySelector(".br-panel");
      const style = getComputedStyle(panel);
      const filter = style.backdropFilter || style.webkitBackdropFilter || "";
      const blur = Number(/blur\(([\d.]+)px\)/.exec(filter)?.[1] ?? 0);
      const rim = getComputedStyle(panel, "::after");
      // Every colour painted for the glass and the backdrop behind it.
      const colors = (text) => [...text.matchAll(/rgba?\(([^)]+)\)|color\(srgb ([^)]+)\)/g)].map((match) => {
        const parts = (match[1] ?? match[2]).split(/[\s,/]+/).filter(Boolean).map(Number);
        return match[1] ? parts.slice(0, 3) : parts.slice(0, 3).map((v) => v * 255);
      });
      const painted = [
        rim.backgroundImage, style.backgroundImage, style.boxShadow,
        getComputedStyle(document.body).backgroundImage,
        getComputedStyle(document.body, "::before").backgroundImage,
      ].join(" ");
      const chroma = Math.max(0, ...colors(painted).map(([r, g, b]) => Math.max(r, g, b) - Math.min(r, g, b)));
      return {
        blur,
        specular: /inset/.test(style.boxShadow) && /rgba\(255, 255, 255/.test(style.boxShadow),
        rim: rim.content !== "none" && rim.backgroundImage.includes("gradient"),
        chroma,
      };
    });
    assert.ok(glass.blur >= 20, `panel blur ${glass.blur}px`);
    assert.ok(glass.specular, "panel has no inner specular highlight");
    assert.ok(glass.rim, "panel has no light-catching rim");
    // Apple's glass is clear: no rainbow dispersion or coloured aurora, only near-neutral light.
    assert.ok(glass.chroma <= 40, `glass and backdrop are tinted (max channel spread ${glass.chroma.toFixed(0)})`);
    // Readability over the busiest part of the backdrop: panel heading text vs the composited panel.
    const heading = page.locator(".br-panel__header").first();
    const on = await painted(page, heading);
    assert.ok(on.ratio >= 4.5, `panel heading contrast ${on.ratio.toFixed(2)} (${on.text} on ${on.fill})`);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("home rails", () => {
  for (const viewport of [DEFAULT, SCALED_MINIMUM]) {
    test(`home at ${viewport.width}×${viewport.height}: the atlas sits centre between two scrolling widget rails; find and sections stay in reach`, async () => {
      const { page, problems, context } = await open("today", { viewport, dock: "hidden" });
      const left = page.getByRole("complementary", { name: "Home widgets" });
      const right = page.getByRole("complementary", { name: "Workspace" });
      const stage = page.locator('[data-kitty-panel="graph"]');
      await Promise.all([left.waitFor(), right.waitFor(), stage.waitFor()]);
      const [l, s, r] = await Promise.all([rect(left), rect(stage), rect(right)]);
      assert.ok(l.right <= s.left && s.right <= r.left, `rails do not flank the atlas: ${l.left}-${l.right} | ${s.left}-${s.right} | ${r.left}-${r.right}`);
      assert.ok(s.width > l.width && s.width > r.width, `the atlas (${s.width}px) is not the widest column`);
      for (const [name, box] of [["left", l], ["right", r]])
        assert.ok(Math.abs(box.top - s.top) <= 1 && Math.abs(box.bottom - s.bottom) <= 1, `${name} rail is not as tall as the atlas`);
      // Each rail scrolls on its own; the page never does.
      const scrolled = await right.evaluate((rail) => { rail.scrollTop = 200; return rail.scrollTop; });
      assert.ok(scrolled > 0, "the Workspace rail does not scroll");
      assert.ok(["auto", "scroll"].includes(await left.evaluate((rail) => getComputedStyle(rail).overflowY)), "the widget rail does not scroll");
      assert.equal(await page.evaluate(() => document.scrollingElement.scrollTop), 0);
      await right.getByRole("searchbox", { name: /Find a section/ }).fill("Flash");
      await right.getByRole("list", { name: "Matching nodes" }).getByRole("button", { name: /Flashcards/ }).waitFor();
      assert.ok(await right.getByRole("navigation", { name: "Workspace sections" }).getByRole("link", { name: "Calendar" }).isVisible());
      await assertAtlasFits(page, `${viewport.width} rails`);
      assert.deepEqual(problems, []);
      await context.close();
    });
  }
});
