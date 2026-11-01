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

describe("study cockpit", () => {
  test("the Study hub is three columns (subjects & tasks, timer, guide & notes) with every tool tab visible, and one column in a narrow pane", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    page.on("dialog", (dialog) => dialog.accept());
    const left = page.getByRole("region", { name: "Subjects & tasks" });
    const centre = page.getByRole("region", { name: "Session timer" });
    const right = page.getByRole("region", { name: "Guide & notes" });
    await centre.waitFor();
    const stop = centre.getByRole("button", { name: /^Stop/ });
    if (await stop.isEnabled()) {
      await stop.click();
      await centre.getByRole("button", { name: /Start session/ }).waitFor();
    }
    let [a, b, c] = await Promise.all([rect(left), rect(centre), rect(right)]);
    assert.ok(a.right <= b.left + 1 && b.right <= c.left + 1, "columns are not side by side");
    assert.ok(Math.abs(a.top - b.top) < 2 && Math.abs(b.top - c.top) < 2, "columns do not share a row");
    assert.equal(await page.getByText("Planning & tools").count(), 0, "tools are still behind a disclosure");
    const tabs = page.getByRole("navigation", { name: "Study tools" });
    for (const name of ["Study session", "Progress", "History", "Exams & plans", "Mind map", "Helpers", "Timer settings"])
      assert.ok(await tabs.getByRole("button", { name, exact: true }).isVisible(), `${name} tab is not visible`);
    assert.ok(await right.getByRole("region", { name: "Fourteen-step study guide" }).isVisible(), "guide is not in the right column");
    assert.ok(await right.getByRole("textbox", { name: /Session notes/ }).isVisible(), "session notes are not in the right column");
    const subject = `Cockpit subject ${Date.now()}`;
    await left.getByRole("button", { name: "Add subject" }).click();
    const editor = page.getByRole("region", { name: "Edit subjects" });
    await editor.getByLabel("Name").fill(subject);
    await editor.getByRole("button", { name: "Save" }).click();
    const pick = left.getByRole("button", { name: new RegExp(`^${subject}`) });
    await pick.click();
    assert.equal(await pick.getAttribute("aria-pressed"), "true");
    await centre.getByText(subject).waitFor();
    await page.setViewportSize(MINIMUM);
    await page.waitForFunction(() => {
      const [x, y] = ["Subjects & tasks", "Session timer"].map((name) => document.querySelector(`[aria-label="${name}"]`).getBoundingClientRect());
      return Math.abs(x.left - y.left) < 2;
    });
    [a, b, c] = await Promise.all([rect(left), rect(centre), rect(right)]);
    assert.ok(b.top >= a.bottom - 1 && c.top >= b.bottom - 1, "a narrow pane is not one column");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("study progress", () => {
  test("Progress stacks each day's study in subject colours beside a share ring, tints a month calendar by each day's main subject with weekly-goal checks, and draws goal rings", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    await page.getByRole("region", { name: "Session timer" }).waitFor();
    const stamp = Date.now();
    // A fixed past Wednesday keeps the week and month deterministic.
    const [major, minor] = await page.evaluate(async (stamp) => {
      const post = async (path, body) => {
        const response = await fetch(`/api/study/${path}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Kitty-Request": "1" }, body: JSON.stringify(body) });
        return (await response.json()).item;
      };
      const major = await post("subjects", { name: `Major ${stamp}`, color: "#d05080" });
      const minor = await post("subjects", { name: `Minor ${stamp}`, color: "#3070e0" });
      for (const [subject, minutes, hour] of [[major, 50, 10], [minor, 20, 14]])
        await post("sessions", { request_id: crypto.randomUUID(), started_at: `2026-01-14T${hour}:00:00+00:00`, timezone: "UTC", duration_seconds: minutes * 60, subject_id: subject.id });
      await post("targets", { title: `Daily ${stamp}`, period: "daily", minutes: 30 });
      window.dispatchEvent(new CustomEvent("kitty-study-changed"));
      return [major, minor];
    }, stamp);
    await page.getByRole("navigation", { name: "Study tools" }).getByRole("button", { name: "Progress", exact: true }).click();
    assert.equal(await page.getByLabel("Daily study activity").count(), 0, "the accent-only heatmap is still shown");
    await page.getByLabel("Jump to date").fill("2026-01-14");
    await page.getByLabel("Jump to date").dispatchEvent("change");

    const bars = page.getByRole("region", { name: "Study by subject" });
    const day = bars.getByRole("button").filter({ has: page.locator(`[data-subject="${major.id}"]`) });
    await day.waitFor();
    const colours = await day.locator("[data-subject]").evaluateAll((parts) => parts.map((part) => getComputedStyle(part).backgroundColor));
    assert.deepEqual(colours, ["rgb(208, 80, 128)", "rgb(48, 112, 224)"], "the day is not stacked in subject colours, largest first");
    assert.match(await day.getAttribute("aria-label"), new RegExp(`70 min, mostly Major ${stamp}`));
    const legend = bars.getByRole("button", { name: new RegExp(`^Minor ${stamp}`) });
    assert.match(await legend.textContent(), /20 min/);
    await legend.hover();
    await page.waitForFunction((id) => Number(getComputedStyle(document.querySelector(`[aria-label="Study by subject"] [data-subject="${id}"]`)).opacity) < 0.5, major.id);

    const calendar = page.getByRole("region", { name: "Study calendar" });
    assert.equal(await calendar.getByRole("button", { name: new RegExp(`: 70 min, mostly Major ${stamp}$`) }).count(), 1, "the calendar day is not tinted by its main subject");
    const checks = calendar.getByRole("img", { name: /^(Week of|This week)/ });
    assert.ok((await checks.count()) >= 4, "weeks have no goal checks");
    assert.equal(await calendar.getByRole("img", { name: /^Week of .*: 70 min, studied$/ }).count(), 1, "the studied week is not checked");

    const goals = page.getByRole("region", { name: "Goal rings" });
    for (const name of ["Today", "This week", "Flashcard reviews"]) await goals.getByText(name, { exact: true }).waitFor();
    assert.match(await goals.getByRole("listitem").filter({ hasText: "Today" }).textContent(), /\d+ min\s*\/\s*30 min/);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("study plans", () => {
  test("Exams & plans shows countdown cards, a timeline to each exam, a 12-day strip to drag blocks with a load warning, and a returns queue", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    await page.getByRole("region", { name: "Session timer" }).waitFor();
    const stamp = Date.now();
    await page.evaluate(async (stamp) => {
      const call = async (path, body) => {
        const response = await fetch(`/api/study${path}`, body ? { method: "POST", headers: { "Content-Type": "application/json", "X-Kitty-Request": "1" }, body: JSON.stringify(body) } : undefined);
        const result = await response.json();
        if (!response.ok) throw new Error(`${path}: ${JSON.stringify(result)}`);
        return result;
      };
      const { stats } = await call("");
      const day = (offset) => new Date(Date.parse(`${stats.today}T12:00:00Z`) + offset * 86_400_000).toISOString().slice(0, 10);
      let { timer } = await call("/timer");
      if (timer.status !== "idle") ({ timer } = await call("/timer", { action: "stop", revision: timer.revision, request_id: crypto.randomUUID() }));
      const subject = (await call("/subjects", { name: `Planner ${stamp}`, color: "#40a080" })).item;
      const exam = (await call("/exams", { title: `Exam ${stamp}`, subject_id: subject.id, exam_date: day(5), timezone: "UTC" })).item;
      await call("/generate-plan", { exam_id: exam.id, exam_revision: exam.revision, start_date: day(1), minutes: 120, block_minutes: 60, local_time: "17:00", replace: false });
      const responses = Array(14).fill(""); responses[4] = `Return ${stamp}`;
      ({ timer } = await call("/timer"));
      ({ timer } = await call("/timer", { action: "start", revision: timer.revision, request_id: crypto.randomUUID(), subject_id: subject.id, guide: { checked: Array(14).fill(false), responses, reflection: "", next_review: day(0) } }));
      await call("/timer", { action: "stop", revision: timer.revision, request_id: crypto.randomUUID() });
      window.dispatchEvent(new CustomEvent("kitty-study-changed"));
    }, stamp);
    await page.getByRole("navigation", { name: "Study tools" }).getByRole("button", { name: "Exams & plans", exact: true }).click();

    const card = page.getByRole("region", { name: "Exam countdowns" }).getByRole("article").filter({ hasText: `Exam ${stamp}` });
    await card.waitFor();
    assert.match(await card.textContent(), /5\s*days/);
    assert.match(await card.textContent(), /0\/2 blocks/);
    assert.equal(await card.getByRole("button", { name: "Study now" }).count(), 1, "the countdown card has no single action");

    const timeline = page.getByRole("region", { name: "Timeline to exams" });
    const blocks = timeline.getByRole("button", { name: new RegExp(`^Study: Exam ${stamp}`) });
    assert.equal(await blocks.count(), 2);
    assert.equal(await timeline.getByRole("img", { name: new RegExp(`^Exam ${stamp} in 5 days`) }).count(), 1, "the exam is not a blossom on its lane");
    assert.equal(await timeline.getByRole("img", { name: new RegExp(`^Spaced return: Return ${stamp}`) }).count(), 1, "the return is not on the returns lane");
    await blocks.first().click();
    await timeline.getByRole("button", { name: "Mark done" }).click();
    await page.waitForFunction((stamp) => /done/.test(document.querySelector(`[aria-label="Timeline to exams"] [aria-label^="Study: Exam ${stamp}"]`)?.getAttribute("aria-label") ?? ""), stamp);
    await card.getByText("1/2 blocks").waitFor();

    await page.getByRole("radio", { name: "Days" }).click();
    const strip = page.getByRole("region", { name: "Next 12 days" });
    const days = strip.getByRole("group");
    assert.equal(await days.count(), 12);
    const mine = (index) => days.nth(index).getByRole("button", { name: new RegExp(`^Study: Exam ${stamp}`) });
    assert.equal(await mine(1).count(), 1);
    // Keep the whole strip on screen: scrolling mid-drag cancels a native drag.
    await strip.evaluate((node) => node.scrollIntoView({ block: "start" }));
    await mine(1).dragTo(days.nth(2), { targetPosition: { x: 20, y: 40 } });
    await mine(2).waitFor();
    assert.equal(await mine(1).count(), 0, "the dragged block stayed on its day");
    await mine(3).dragTo(days.nth(2), { targetPosition: { x: 20, y: 40 } });
    await page.waitForFunction(() => document.querySelectorAll('[aria-label="Next 12 days"] [role="group"]')[2]?.getAttribute("aria-label")?.includes("heavy"));
    assert.equal(await mine(2).count(), 2);
    await days.nth(2).getByText(/Heavy day/).waitFor();

    const returns = page.getByRole("region", { name: "Spaced returns" });
    const due = returns.getByRole("region", { name: "Due" }).getByRole("article").filter({ hasText: `Return ${stamp}` });
    await due.getByRole("button", { name: "Recalled" }).click();
    const later = returns.getByRole("region", { name: "This week" }).getByRole("article").filter({ hasText: `Return ${stamp}` });
    await later.waitFor();
    assert.equal(await later.getByRole("img", { name: /^Rung 2 of 5/ }).count(), 1, "recalling did not climb the ladder");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("study streak", () => {
  test("manual logging fills persisted streak segments and cancel does not log", async () => {
    const { page, context, problems } = await open("study", { dock: "hidden" });
    await page.getByRole("region", { name: "Session timer" }).waitFor();
    await page.getByRole("navigation", { name: "Study tools" }).getByRole("button", { name: "Progress", exact: true }).click();
    const ring = page.getByRole("region", { name: "Study streak" });
    const before = await page.evaluate(async () => (await (await fetch("/api/study")).json()).stats);
    await ring.getByRole("button").click();
    const editor = page.getByRole("region", { name: "Edit completed session" });
    await editor.getByRole("button", { name: "Cancel", exact: true }).click();
    assert.equal(await page.evaluate(async () => (await (await fetch("/api/study")).json()).stats.session_count), before.session_count);
    await ring.getByRole("button").click();
    await editor.getByLabel("Start timestamp with explicit offset").fill(new Date(Date.now() - 20 * 60_000).toISOString());
    await editor.getByLabel("IANA timezone").fill("UTC");
    await editor.getByLabel("Duration in minutes").fill("15");
    await editor.getByRole("button", { name: "Save completed session" }).click();
    await ring.getByText("Today's study is logged.", { exact: true }).waitFor();
    const saved = await page.evaluate(async () => (await (await fetch("/api/study")).json()).stats);
    assert.equal(saved.session_count, before.session_count + 1);
    assert.ok(saved.daily_streak >= 1);
    assert.equal(await ring.locator("path.filled").count(), saved.daily_streak);
    await page.reload();
    await page.getByRole("navigation", { name: "Study tools" }).getByRole("button", { name: "Progress", exact: true }).click();
    assert.match(await ring.getByRole("img").getAttribute("aria-label"), new RegExp(`^${saved.daily_streak} day streak`));
    await ring.screenshot({ path: "/tmp/kitty-shots/q21-streak.png" });
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

describe("home widgets", () => {
  const minutes = (offset) => new Date(Date.now() + offset * 60_000).toISOString();
  const day = new Date().toISOString().slice(0, 10);
  const CALENDAR = {
    calendars: [{ id: "personal", name: "Personal", color: "#6d5dfc", visible: 1 }],
    events: [
      { id: "e-past", title: "Morning review", start: minutes(-180), end: minutes(-150), all_day: false, calendar_id: "personal", location: "" },
      { id: "e-live", title: "Thesis writing", start: minutes(-30), end: minutes(30), all_day: false, calendar_id: "personal", location: "Library" },
      { id: "e-next", title: "Seminar", start: minutes(90), end: minutes(150), all_day: false, calendar_id: "personal", location: "Room 4" },
    ],
    tasks: [], activity: [], reminders: [],
  };
  const ACTIVITY = {
    running: true,
    workspaces: [
      { id: "w1", label: "thesis", status: "working", focused: true, agents: [{ pane: "p1", name: "omp", status: "working" }] },
      { id: "w2", label: "export", status: "blocked", focused: false, agents: [{ pane: "p2", name: "claude", status: "blocked" }] },
    ],
  };

  test("home widgets: hairline Today agenda with now-line, progress and countdown; agents with Approve; Ctrl+Enter quick notes", async () => {
    const { page, problems, context } = await open("today", { dock: "hidden" });
    await page.route((url) => url.pathname === "/api/calendar", (route) => route.fulfill({ json: CALENDAR }));
    await page.route((url) => url.pathname === "/api/terminal/activity", (route) => route.fulfill({ json: ACTIVITY }));
    await page.reload();
    const left = page.getByRole("complementary", { name: "Home widgets" });
    const agenda = left.getByRole("region", { name: "Today" });
    const note = left.getByRole("region", { name: "Quick note" });
    const agents = page.getByRole("complementary", { name: "Workspace" }).getByRole("region", { name: "Agents" });
    await Promise.all([agenda.waitFor(), note.waitFor(), agents.waitFor()]);

    // Q19 hairline minimal: no fill or box, a rule above, a small Space Grotesk title, the eyebrow on the right.
    for (const widget of [agenda, note, agents]) {
      const frame = await widget.evaluate((node) => {
        const style = getComputedStyle(node);
        const title = node.querySelector("h3"), eyebrow = node.querySelector("header .eyebrow");
        return {
          fill: style.backgroundColor, top: style.borderTopWidth, sides: [style.borderRightWidth, style.borderBottomWidth, style.borderLeftWidth],
          font: getComputedStyle(title).fontFamily, size: parseFloat(getComputedStyle(title).fontSize),
          titleRight: title.getBoundingClientRect().right, eyebrowLeft: eyebrow?.getBoundingClientRect().left ?? -1,
          sameRow: eyebrow ? Math.abs(eyebrow.getBoundingClientRect().bottom - title.getBoundingClientRect().bottom) < 6 : false,
        };
      });
      assert.equal(frame.fill, "rgba(0, 0, 0, 0)", "widget has a fill");
      assert.equal(frame.top, "1px");
      assert.deepEqual(frame.sides, ["0px", "0px", "0px"], "widget is boxed");
      assert.match(frame.font, /Space Grotesk/);
      assert.ok(frame.size <= 14, `title is ${frame.size}px`);
      assert.ok(frame.eyebrowLeft > frame.titleRight && frame.sameRow, "eyebrow is not on the right of the title");
    }

    // Today agenda: past, live with progress, now-line, then the next event with a countdown.
    const rows = agenda.getByRole("listitem");
    await agenda.getByText("Seminar").waitFor();
    const labels = await rows.evaluateAll((items) => items.map((item) => item.getAttribute("aria-label") || item.textContent.trim()));
    const live = labels.findIndex((label) => /Thesis writing/.test(label));
    const line = labels.findIndex((label) => /^Now/.test(label));
    assert.ok(labels.findIndex((label) => /Morning review/.test(label)) < live && live < line && line < labels.findIndex((label) => /Seminar/.test(label)), `agenda order: ${labels}`);
    const progress = agenda.getByRole("progressbar", { name: /Thesis writing/ });
    const value = Number(await progress.getAttribute("aria-valuenow"));
    assert.ok(value >= 45 && value <= 55, `live progress ${value}`);
    assert.match(await agenda.getByRole("listitem").filter({ hasText: "Seminar" }).textContent(), /in 1h (29|30)m/);

    // Agent activity: per-workspace state, Approve opens herdr for the blocked one.
    assert.match(await agents.textContent(), /1 working · 1 needs you/);
    assert.match(await agents.getByRole("listitem").filter({ hasText: "thesis" }).textContent(), /omp/);
    assert.equal(await agents.getByRole("button", { name: /Approve/ }).count(), 1);

    // Quick note: Ctrl+Enter captures; the last three stay visible.
    const box = note.getByRole("textbox", { name: "Quick note" });
    for (const text of ["first thought", "second thought", "third thought", "fourth thought"]) {
      await box.fill(text);
      await box.press("Control+Enter");
      await note.getByRole("listitem").filter({ hasText: text }).waitFor();
      assert.equal(await box.inputValue(), "");
    }
    const notes = await note.getByRole("listitem").allTextContents();
    assert.equal(notes.length, 3);
    assert.match(notes[0], /fourth thought/);
    const saved = await page.evaluate(() => fetch("/api/quick-notes").then((r) => r.json()));
    assert.ok(saved.notes.some((item) => item.body === "first thought"), "quick note was not saved");

    // Job-search and study readouts no longer sit on home.
    assert.equal(await page.getByRole("button", { name: /Jobs to explore|Cards due/ }).count(), 0);
    assert.equal(await page.getByRole("heading", { name: "Reminders & deadlines" }).count(), 0);

    await agents.getByRole("button", { name: /Approve/ }).click();
    await page.waitForFunction(() => location.hash === "#herdr");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Find jobs holds the reminders brief and How to apply in one collapsible section that remembers it is open", async () => {
    const { page, problems, context } = await open("jobs", { dock: "hidden" });
    const brief = page.locator("details").filter({ has: page.locator("summary", { hasText: "Reminders, deadlines & goals" }) });
    await brief.waitFor();
    assert.equal(await brief.evaluate((node) => node.open), false, "brief starts open");
    await brief.locator("summary").click();
    await brief.getByRole("heading", { name: "Reminders & deadlines" }).waitFor();
    assert.ok(await brief.getByRole("button", { name: "How to apply" }).isVisible());
    await page.reload();
    await brief.getByRole("heading", { name: "Reminders & deadlines" }).waitFor();
    assert.equal(await brief.evaluate((node) => node.open), true, "brief forgot it was open");
    await page.evaluate(() => (location.hash = "inbox"));
    await page.getByRole("heading", { name: /New jobs/ }).first().waitFor();
    assert.equal(await page.locator("summary", { hasText: "Reminders, deadlines & goals" }).count(), 0, "brief shows outside Find jobs");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("appearance settings", () => {
  test("Settings → Appearance: one panel with a live preview, presets that set everything, single changes after, saved and applied before mount", async () => {
    const { page, problems, context } = await open("settings", { dock: "hidden" });
    await context.addInitScript(() => {
      new MutationObserver((_, observer) => {
        if (!document.getElementById("app")?.firstElementChild) return;
        window.__atMount = { ...document.documentElement.dataset };
        observer.disconnect();
      }).observe(document, { childList: true, subtree: true });
    });
    const panel = page.getByRole("region", { name: "Appearance" });
    await panel.waitFor();
    const preview = panel.getByRole("img", { name: /preview/i });
    const radio = (name) => panel.getByRole("radio", { name, exact: true });
    const root = (key) => page.evaluate((key) => document.documentElement.dataset[key], key);
    const saved = () => page.evaluate(() => JSON.parse(localStorage.getItem("kitty.appearance") ?? "null"));
    const titleFont = () => page.getByRole("heading", { level: 1, name: "Settings" }).evaluate((node) => getComputedStyle(node).fontFamily);
    const bodySize = () => page.evaluate(() => getComputedStyle(document.body).fontSize);

    // Defaults: Kitty, Fraunces titles, compact 13px, full motion, 2D, blossoms, nebula.
    for (const name of ["Kitty", "Fraunces", "Compact · 13px", "Full", "2D", "Nebula"]) assert.equal(await radio(name).isChecked(), true, `${name} is not the default`);
    assert.equal(await panel.getByRole("checkbox", { name: /Blossom motif/ }).isChecked(), true);
    assert.match(await titleFont(), /Fraunces/);
    assert.equal(await bodySize(), "13px");

    // A preset sets every control at once, the app and the preview follow.
    await panel.getByRole("button", { name: /^Minimal/ }).click();
    for (const name of ["Harness", "Plain", "Compact · 13px", "Reduced", "2D", "Nebula"]) assert.equal(await radio(name).isChecked(), true, `Minimal did not set ${name}`);
    assert.equal(await panel.getByRole("checkbox", { name: /Blossom motif/ }).isChecked(), false);
    assert.equal(await panel.getByRole("button", { name: /^Minimal/ }).getAttribute("aria-pressed"), "true");
    assert.equal(await root("theme"), "harness");
    assert.doesNotMatch(await titleFont(), /Fraunces/);
    assert.equal(await root("motion"), "reduced");
    assert.equal(await preview.getAttribute("data-title-font"), "plain");
    assert.deepEqual(await saved(), { titleFont: "plain", density: "compact", motion: "reduced", motif: false, background: "nebula" });

    // Any single setting still changes afterwards; the preset no longer matches.
    await radio("Comfortable · 14px").check();
    assert.equal(await bodySize(), "14px");
    assert.equal(await preview.getAttribute("data-density"), "comfortable");
    assert.equal(await panel.getByRole("button", { name: /^Minimal/ }).getAttribute("aria-pressed"), "false");
    assert.equal((await saved()).density, "comfortable");
    assert.equal((await saved()).titleFont, "plain", "changing density reset another setting");

    // Cozy: petal drift behind the app and a 3D atlas; reduced motion pauses the petals.
    await panel.getByRole("button", { name: /^Cozy/ }).click();
    assert.equal(await radio("3D").isChecked(), true);
    assert.equal(await page.evaluate(() => localStorage.getItem("kitty.graph.look")), "3d");
    const petals = page.locator(".petal-drift");
    await petals.waitFor({ state: "attached" });
    assert.ok((await petals.locator("svg").count()) >= 8, "too few petals");
    const falling = () => petals.locator(".lane").first().evaluate((node) => getComputedStyle(node).animationName);
    assert.notEqual(await falling(), "none", "petals do not drift");
    await radio("Reduced").check();
    assert.equal(await falling(), "none", "petals still drift under reduced motion");
    await radio("Full").check();
    await page.emulateMedia({ reducedMotion: "reduce" });
    assert.equal(await falling(), "none", "petals drift although the system asks for reduced motion");
    await page.emulateMedia({ reducedMotion: "no-preference" });

    // Saved choices are applied before the app mounts.
    const cozy = await saved();
    await page.reload();
    await panel.waitFor();
    const atMount = await page.evaluate(() => window.__atMount);
    assert.equal(atMount.background, "petals");
    assert.equal(atMount.density, cozy.density);
    assert.equal(atMount.titleFont, cozy.titleFont);
    assert.equal(await panel.getByRole("button", { name: /^Cozy/ }).getAttribute("aria-pressed"), "true");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("home agenda empty", () => {
  test("an empty Today agenda says so and offers the calendar on one line", async () => {
    const { page, problems, context } = await open("today", { dock: "hidden" });
    await page.route((url) => url.pathname === "/api/calendar", (route) => route.fulfill({ json: { calendars: [], events: [], tasks: [], activity: [], reminders: [] } }));
    await page.reload();
    const agenda = page.getByRole("region", { name: "Today" });
    await agenda.getByText("Nothing on the calendar today.").waitFor();
    const link = agenda.getByRole("button", { name: "Open the calendar" });
    const lines = await link.evaluate((node) => {
      const range = document.createRange();
      range.selectNodeContents(node);
      return new Set([...range.getClientRects()].map((box) => Math.round(box.top))).size;
    });
    assert.equal(lines, 1, `the calendar link wraps onto ${lines} lines`);
    await link.click();
    await page.waitForFunction(() => location.hash === "#calendar");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("calendar redesign", () => {
  /** Local wall time `hh:mm` on `day` offset from today, as an ISO instant. */
  const at = (dayOffset, hour, minute = 0) => {
    const d = new Date();
    d.setDate(d.getDate() + dayOffset);
    d.setHours(hour, minute, 0, 0);
    return d.toISOString();
  };
  /** Creates an event through the real API; returns it. */
  const addEvent = (page, body) => page.evaluate(async (event) => {
    const response = await fetch("/api/calendar/events", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Kitty-Request": "1" },
      body: JSON.stringify({ calendar_id: "personal", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, ...event }),
    });
    return response.json();
  }, body);
  const setTheme = (page, id) => page.evaluate(async (theme) => (await import("/src/lib/themes.ts")).setTheme(theme), id);
  /** WCAG contrast of an element's text colour against its own background, plus that background's alpha. */
  const textContrast = (locator) => locator.evaluate((node) => {
    const ctx = Object.assign(document.createElement("canvas"), { width: 1, height: 1 }).getContext("2d", { willReadFrequently: true });
    const rgba = (value) => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1); return [...ctx.getImageData(0, 0, 1, 1).data]; };
    const lum = (c) => { const [r, g, b] = c.slice(0, 3).map((v) => v / 255).map((v) => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
    const style = getComputedStyle(node);
    const back = rgba(style.backgroundColor);
    const [a, b] = [lum(rgba(style.color)), lum(back)].sort((x, y) => y - x);
    return { ratio: (a + 0.05) / (b + 0.05), alpha: back[3] / 255, fill: back.slice(0, 3).join(",") };
  });
  /** The six theme palette colours as "r,g,b". */
  const palette = (page) => page.evaluate(() => [1, 2, 3, 4, 5, 6].map((n) => {
    const value = getComputedStyle(document.documentElement).getPropertyValue(`--c${n}`).trim();
    return [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16)).join(",");
  }));

  test("week events are filled palette blocks with readable text and a blossom now-marker", async () => {
    const { page, problems, context } = await open("calendar", { dock: "hidden" });
    await page.getByRole("region", { name: "Week calendar" }).waitFor();
    await addEvent(page, { title: "Pastel standup", start: at(0, 10), end: at(0, 11) });
    await page.reload();
    const block = page.getByRole("group", { name: "Pastel standup" }).first();
    await block.waitFor();
    for (const [theme, minimum] of [["kitty", 4.5], ["gruvbox", 3], ["one-dark", 3]]) {
      await setTheme(page, theme);
      const { ratio, alpha } = await textContrast(block);
      assert.equal(alpha, 1, `${theme}: block is not a solid fill`);
      assert.ok(ratio >= minimum, `${theme}: block text contrast ${ratio.toFixed(2)} < ${minimum}`);
    }
    const { fill } = await textContrast(block);
    assert.ok((await palette(page)).includes(fill), `block fill ${fill} is not a theme palette colour`);
    const now = page.getByRole("img", { name: /^Now / });
    assert.equal(await now.count(), 1, "today has no labelled now-marker");
    assert.match(await now.textContent(), /✿/, "now-marker has no blossom");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("existing interactions still work: keyboard slots, drag-create, drag-move and resize", async () => {
    const { page, problems, context } = await open("calendar", { dock: "hidden" });
    await page.getByRole("region", { name: "Week calendar" }).waitFor();
    await addEvent(page, { title: "Move me", start: at(0, 9), end: at(0, 10) });
    await page.reload();
    const d = new Date();
    const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const slotButton = (time) => page.getByRole("button", { name: `Create event ${day} ${time}`, exact: true });
    const editor = page.getByRole("dialog");
    await slotButton("14:00").focus();
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await editor.getByRole("heading", { name: "New event" }).waitFor();
    await page.keyboard.press("Escape");
    await editor.waitFor({ state: "detached" });
    await slotButton("16:00").scrollIntoViewIfNeeded();
    const from = await slotButton("15:00").boundingBox();
    const to = await slotButton("16:00").boundingBox();
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 4 });
    await page.mouse.up();
    await editor.getByRole("heading", { name: "New event" }).waitFor();
    await page.keyboard.press("Escape");
    await editor.waitFor({ state: "detached" });
    const block = page.getByRole("group", { name: "Move me" });
    await block.dragTo(slotButton("12:00"));
    await block.getByText("12:00–13:00").waitFor();
    // Chromium cancels a native drag that starts on the tiny ↔ handle under automation; dispatch the same events.
    const transfer = await page.evaluateHandle(() => new DataTransfer());
    const handle = block.getByRole("button", { name: "Resize Move me" });
    await handle.dispatchEvent("dragstart", { dataTransfer: transfer });
    await slotButton("14:30").dispatchEvent("dragover", { dataTransfer: transfer });
    await slotButton("14:30").dispatchEvent("drop", { dataTransfer: transfer });
    await handle.dispatchEvent("dragend", { dataTransfer: transfer });
    await block.getByText("12:00–14:30").waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("month cells show up to four calendar dots and clicking a date fills the side agenda", async () => {
    const { page, problems, context } = await open("calendar", { dock: "hidden" });
    await page.getByRole("region", { name: "Week calendar" }).waitFor();
    const now = new Date();
    const on20 = (hour) => new Date(now.getFullYear(), now.getMonth(), 20, hour).toISOString();
    for (const hour of [8, 10, 12, 14, 16])
      await addEvent(page, { title: `Dot ${hour}`, start: on20(hour), end: on20(hour + 1), location: hour === 12 ? "Room 5" : "" });
    await page.reload();
    await page.getByRole("toolbar", { name: "Calendar range" }).getByRole("button", { name: "Month", exact: true }).click();
    const grid = page.getByRole("grid", { name: "Month calendar" });
    const cell = grid.getByRole("gridcell", { name: / 20, 5 events$/ }).first();
    await cell.waitFor();
    assert.match(await cell.getAttribute("aria-label"), /5 events/);
    assert.equal(await cell.locator(".dots > *").count(), 4, "a busy day should show four dots");
    assert.equal(await grid.getByText("Dot 8").count(), 0, "month cells still list event titles");
    await cell.click();
    const agenda = page.getByRole("complementary", { name: "Day agenda" });
    await agenda.getByRole("heading", { name: "5 events" }).waitFor();
    const items = agenda.getByRole("button", { name: /Dot \d+/ });
    assert.equal(await items.count(), 5);
    assert.match(await items.nth(2).textContent(), /12:00[\s\S]*Dot 12[\s\S]*Room 5/);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("a floating range pill switches views and D/W/M/A, J/K and T work while the calendar has focus", async () => {
    const { page, problems, context } = await open("calendar", { dock: "hidden" });
    const week = page.getByRole("region", { name: "Week calendar" });
    await week.waitFor();
    assert.equal(await page.getByRole("combobox", { name: "Calendar view" }).count(), 0, "native view select is still shown");
    const pill = page.getByRole("toolbar", { name: "Calendar range" });
    await page.locator(".calendar-main").evaluate((node) => node.scrollIntoView());
    const box = await pill.boundingBox();
    const main = await page.locator(".calendar-main").boundingBox();
    const floor = Math.min(main.y + main.height, 850);
    assert.ok(box.y + box.height <= floor && box.y >= floor - 70,
      `range pill (${box.y}) does not float over the visible bottom of the calendar (${floor})`);
    assert.ok(Math.abs(box.x + box.width / 2 - (main.x + main.width / 2)) < 4, "range pill is not centred over the calendar");
    const views = ["Day", "Week", "Month", "Agenda"];
    for (const name of views) assert.equal(await pill.getByRole("button", { name, exact: true }).getAttribute("aria-keyshortcuts"), name[0]);
    await pill.getByRole("button", { name: "Month", exact: true }).click();
    await page.getByRole("grid", { name: "Month calendar" }).waitFor();
    assert.equal(await pill.getByRole("button", { name: "Month", exact: true }).getAttribute("aria-pressed"), "true");
    const range = pill.getByRole("button", { name: /Back to today/ });
    await page.getByRole("button", { name: /^Select \d{4}-/ }).first().focus();
    await page.keyboard.press("w");
    await week.waitFor();
    const thisWeek = await range.textContent();
    await page.keyboard.press("k");
    await page.waitForFunction((old) => document.querySelector('[aria-label="Calendar range"] .range')?.textContent !== old, thisWeek);
    assert.notEqual(await range.textContent(), thisWeek, "K did not step forward");
    await page.keyboard.press("j");
    await page.keyboard.press("k");
    await page.keyboard.press("t");
    await page.waitForFunction((old) => document.querySelector('[aria-label="Calendar range"] .range')?.textContent === old, thisWeek);
    await page.keyboard.press("d");
    await page.getByRole("region", { name: "Day calendar" }).waitFor();
    await page.keyboard.press("a");
    await page.getByRole("region", { name: "Agenda" }).waitFor();
    await page.getByRole("searchbox").fill("dwm");
    assert.equal(await page.getByRole("region", { name: "Agenda" }).count(), 1, "typing in search switched views");
    await page.getByText("Shortcuts", { exact: true }).click();
    assert.match(await page.locator(".interaction-help").textContent(), /D\/W\/M\/A[\s\S]*J\/K[\s\S]*T/);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("year heatmap, mini-month navigator and a today plan that drops a task into the next free gap", async () => {
    const { page, problems, context } = await open("calendar", { dock: "hidden" });
    await page.getByRole("region", { name: "Week calendar" }).waitFor();
    await addEvent(page, { title: "Plan busy 1", start: at(1, 8), end: at(1, 9) });
    await addEvent(page, { title: "Plan busy 2", start: at(1, 9), end: at(1, 10, 30) });
    await page.evaluate(() => fetch("/api/calendar/tasks", { method: "POST", headers: { "Content-Type": "application/json", "X-Kitty-Request": "1" },
      body: JSON.stringify({ title: "Plan me", priority: "normal", status: "todo" }) }));
    await page.reload();
    const pill = page.getByRole("toolbar", { name: "Calendar range" });
    const tomorrow = new Date(at(1, 12));
    const iso = `${tomorrow.getFullYear()}-${String(tomorrow.getMonth() + 1).padStart(2, "0")}-${String(tomorrow.getDate()).padStart(2, "0")}`;

    // Mini-month navigator: ISO week numbers, a hover preview and click-to-jump.
    const mini = page.getByRole("grid", { name: /^Mini month/ });
    await mini.waitFor();
    assert.ok(await mini.getByText(/^W\d{1,2}$/).count() >= 4, "no ISO week numbers");
    await mini.getByRole("row").nth(2).hover();
    await page.getByRole("status", { name: "Week preview" }).getByText(/^week \d+ · \d+ events/).waitFor();
    await mini.getByRole("gridcell").filter({ hasText: /^15$/ }).first().click();
    await page.getByRole("region", { name: "Week calendar" }).getByRole("button", { name: /^Select \w+, \w+ 15$/ }).waitFor();
    assert.equal(await mini.getByRole("gridcell", { selected: true }).textContent(), "15");

    // Year overview: twelve months; a day reads out its booked time on hover and pins on click.
    await pill.getByRole("button", { name: "Year", exact: true }).click();
    const year = page.getByRole("region", { name: "Year overview" });
    await year.waitFor();
    assert.equal(await year.getByRole("group").count(), 12);
    const day = year.getByRole("button", { name: `${iso}: 2h 30m booked` });
    await day.hover();
    await year.getByRole("status").getByText("2h 30m booked").waitFor();
    await day.click();
    assert.equal(await day.getAttribute("aria-pressed"), "true");

    // Today plan: Plan drops the task into the first free half hour after the busy morning.
    await pill.getByRole("button", { name: "Plan", exact: true }).click();
    await page.getByRole("region", { name: "Day plan" }).waitFor();
    await pill.getByRole("button", { name: /Back to today/ }).click();
    await pill.getByRole("button", { name: "Next day" }).click();
    const plan = page.getByRole("region", { name: "Day plan" });
    await plan.getByText("Plan busy 2").waitFor();
    await plan.getByRole("button", { name: "Plan Plan me" }).click();
    await plan.getByRole("button", { name: "Unplan Plan me (10:30)" }).waitFor();
    const block = plan.getByRole("listitem").filter({ hasText: "Plan me" });
    assert.ok(await block.count() >= 1);
    assert.equal(await plan.locator(".timeline").getByText("Plan me").count(), 1, "planned task is not drawn on the timeline");
    assert.deepEqual(problems, []);
    await context.close();
  });
});
