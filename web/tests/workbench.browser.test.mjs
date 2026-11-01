// The real app in a real browser against the disposable synthetic backend
// (`kitty-web --demo`); no private workspace is read. Covers the atlas fitting
// the window under the floating dock, and selected toggles staying readable.
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
 *  returning user: the first-run welcome guide (covered by its own tests) is already done.
 *  `setup(context)` adds routes or init scripts before the first page load. */
async function open(hash, { viewport = DEFAULT, modules, dock = "shown", theme, setup } = {}) {
  const context = await browser.newContext({ viewport });
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue(),
  );
  if (dock === "hidden")
    await context.addInitScript(() => localStorage.setItem("kitty.dock", JSON.stringify({ mode: "hidden", shown: "terminal", size: null, offset: { right: 16, bottom: 16 } })));
  if (modules)
    await context.route("**/api/bootstrap", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.modules.enabled = modules;
      body.modules.configured = modules;
      await route.fulfill({ response, json: body });
    });
  await setup?.(context);
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

/** The atlas fills the window (the floating dock takes no page space), the page does not scroll and the graph is fitted. */
async function assertAtlasFits(page, label) {
  const atlas = page.locator(".atlas");
  await atlas.waitFor();
  await fitted(page).catch(() => assert.fail(`${label}: graph is not fitted to its visible canvas`));
  const bottom = await page.evaluate(() => innerHeight);
  const box = await rect(atlas);
  assert.ok(box.top >= 0 && box.bottom <= bottom + 1, `${label}: atlas ${box.top}–${box.bottom} is not within 0–${bottom}`);
  assert.ok(box.height >= 120, `${label}: atlas collapsed to ${box.height}px`);
  const overflow = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
  assert.ok(overflow <= 1, `${label}: page scrolls ${overflow}px below the window`);
}

/**
 * Every control in the sidebar can be focused and is then inside the window and
 * hit-testable; only the floating dock card, which the user can move or hide, may cover it.
 */
async function assertSidebarReachable(page, label) {
  const unreachable = await page.locator(".atlas-panel").evaluate((panel) => {
    const dock = document.querySelector('[data-kitty-panel="dock"], .dock-pill');
    const missing = [];
    for (const control of panel.querySelectorAll("a[href], button, input, summary")) {
      if (!control.getClientRects().length || control.closest("details:not([open]) > :not(summary)")) continue;
      control.focus();
      const box = control.getBoundingClientRect();
      const x = box.left + box.width / 2, y = box.top + box.height / 2;
      const hit = document.elementFromPoint(x, y);
      if (y < 0 || y > innerHeight || !hit || !(control === hit || control.contains(hit) || dock?.contains(hit)))
        missing.push(control.getAttribute("aria-label") || control.textContent.trim().slice(0, 30));
    }
    return missing;
  });
  assert.deepEqual(unreachable, [], `${label}: sidebar controls cannot be reached`);
}

describe("atlas fits the window under the floating dock", () => {
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

    test(`${size}, F fullscreens the fitted graph and Escape refits it; hiding, showing or dragging the dock never re-lays out the atlas`, async () => {
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
      const atlas = await rect(page.locator(".atlas"));
      await dock.getByRole("button", { name: "Hide dock" }).click();
      await page.getByRole("button", { name: "Show dock" }).waitFor();
      assert.deepEqual(await rect(page.locator(".atlas")), atlas, `${size}: hiding the dock re-laid out the atlas`);
      await page.getByRole("button", { name: "Show dock" }).click();
      await dock.waitFor();
      assert.deepEqual(await rect(page.locator(".atlas")), atlas, `${size}: showing the dock re-laid out the atlas`);
      // Moving the card by its header and resizing it by its grip leave the page alone mid-drag and after.
      await page.evaluate(() => {
        const atlas = document.querySelector(".atlas");
        const first = JSON.stringify(atlas.getBoundingClientRect());
        window.atlasWatch = { changed: 0 };
        const sample = () => {
          if (!window.atlasWatch) return;
          if (JSON.stringify(atlas.getBoundingClientRect()) !== first) window.atlasWatch.changed += 1;
          requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
      });
      const drag = async (from, dx, dy) => {
        await page.mouse.move(from.x, from.y);
        await page.mouse.down();
        for (let step = 1; step <= 10; step += 1) await page.mouse.move(from.x + (dx * step) / 10, from.y + (dy * step) / 10);
        await page.mouse.up();
      };
      const tabs = await dock.getByRole("group", { name: "Dock contents" }).boundingBox();
      const before = await rect(dock);
      await drag({ x: tabs.x + tabs.width + 12, y: tabs.y + tabs.height / 2 }, -40, -30);
      const moved = await rect(dock);
      assert.ok(moved.x < before.x && moved.y < before.y, `${size}: the header drag did not move the card`);
      const grip = await dock.getByRole("button", { name: "Resize dock" }).boundingBox();
      await drag({ x: grip.x + grip.width / 2, y: grip.y + grip.height / 2 }, 60, 40);
      assert.ok((await rect(dock)).width < moved.width, `${size}: the grip did not resize the card`);
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await page.evaluate(() => { const { changed } = window.atlasWatch; window.atlasWatch = null; return changed; }), 0,
        `${size}: dragging the dock re-laid out the atlas`);
      assert.deepEqual(await rect(page.locator(".atlas")), atlas);
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
  // Liquid Glass gives ghost buttons its own control fill, which must not split the current view's accent pair.
  for (const [themeName, theme] of [["the default theme", undefined], ["Liquid Glass", "liquid-glass"]]) {
    test(`the current flashcard view stays readable and distinct on ${themeName}`, async () => {
      const { page, problems, context } = await open("flashcards", { theme, dock: "hidden" });
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
  }
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

  test("stacked deck: flip the top card, then a swipe or 1–4 sends it off in the grade's direction", async () => {
    const { page, problems, context } = await reviewDeck("Stack deck", [["Capital of Peru?", "Lima"], ["Capital of Chile?", "Santiago"], ["Capital of Bolivia?", "Sucre"], ["Capital of Ecuador?", "Quito"]]);
    const stack = page.getByRole("region", { name: /Card stack/ });
    await stack.waitFor();
    const top = stack.getByRole("button");
    // A pile: cards wait underneath and peek out below the top card.
    const pile = await stack.evaluate((node) => {
      const cards = [...node.querySelectorAll(":scope > *")].map((card) => card.getBoundingClientRect());
      return { count: cards.length, peeking: cards.slice(0, -1).every((card) => card.bottom > cards.at(-1).bottom) };
    });
    assert.ok(pile.count >= 3, `only ${pile.count} cards in the pile`);
    assert.ok(pile.peeking, "cards underneath do not peek out below the top card");
    assert.equal(await top.getAttribute("aria-label"), "Flip card");
    await top.click();
    await stack.getByText(/Lima|Santiago|Sucre|Quito/).waitFor();
    const latest = () => page.evaluate(async () => (await (await fetch("/api/flashcards/history", { cache: "no-store" })).json()).reviews[0] ?? null);
    /** The grade of the next recorded review, once the following card is on the pile. */
    const gradedAfter = async (before, count) => {
      await page.waitForFunction((text) => !document.body.innerText.includes(text), count);
      const review = await latest();
      assert.notEqual(review?.id, before?.id, "no review was recorded");
      return review.rating;
    };
    const eligible = async () => (await page.getByText(/eligible cards/).textContent()).trim();
    // Dragging right past the threshold grades Good and the card leaves to the right.
    let before = await latest(), count = await eligible();
    const box = await top.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 220, box.y + box.height / 2 + 10, { steps: 8 });
    await page.mouse.up();
    assert.equal(await gradedAfter(before, count), 3, "a right swipe did not grade Good");
    // Key 1 sends the next card off to the left as Again.
    await top.click();
    await stack.getByText(/Lima|Santiago|Sucre|Quito/).waitFor();
    let release;
    const held = new Promise((resolve) => (release = resolve));
    await page.route("**/api/flashcards/grade", async (route) => { await held; await route.continue(); });
    before = await latest();
    count = await eligible();
    const start = (await top.boundingBox()).x;
    await page.keyboard.press("1");
    await page.waitForFunction(([x]) => document.querySelector('[role="region"][aria-label^="Card stack"] [role="button"]').getBoundingClientRect().x < x - 100, [start]);
    release();
    assert.equal(await gradedAfter(before, count), 1, "key 1 did not grade Again");
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
  // Built per request, not when the suite loads: the events sit relative to the moment it is read.
  const calendar = () => ({
    calendars: [{ id: "personal", name: "Personal", color: "#6d5dfc", visible: 1 }],
    events: [
      { id: "e-past", title: "Morning review", start: minutes(-180), end: minutes(-150), all_day: false, calendar_id: "personal", location: "" },
      { id: "e-live", title: "Thesis writing", start: minutes(-30), end: minutes(30), all_day: false, calendar_id: "personal", location: "Library" },
      { id: "e-next", title: "Seminar", start: minutes(90), end: minutes(150), all_day: false, calendar_id: "personal", location: "Room 4" },
    ],
    tasks: [], activity: [], reminders: [],
  });
  const ACTIVITY = {
    running: true,
    workspaces: [
      { id: "w1", label: "thesis", status: "working", focused: true, agents: [{ pane: "p1", name: "omp", status: "working" }] },
      { id: "w2", label: "export", status: "blocked", focused: false, agents: [{ pane: "p2", name: "claude", status: "blocked" }] },
    ],
  };

  test("home widgets: hairline Today agenda with now-line, progress and countdown; agents with Approve; Ctrl+Enter quick notes", async () => {
    const { page, problems, context } = await open("today", { dock: "hidden" });
    await page.route((url) => url.pathname === "/api/calendar", (route) => route.fulfill({ json: calendar() }));
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

describe("flashcard decks", () => {
  test("deck tiles: one due ring with the name and today's count, details on hover, and a remembered Graph look", async () => {
    const { page, problems, context } = await open("flashcards", { dock: "hidden" });
    const views = page.getByRole("navigation", { name: "Flashcard views" });
    await views.waitFor();
    await seedDeck(page, "Tile deck", [["One?", "1"], ["Two?", "2"], ["Three?", "3"]]);
    await page.reload();
    assert.equal(await views.getByRole("button", { name: "Map" }).count(), 0, "the graph is still a separate Map view");
    assert.equal(await views.getByRole("button", { name: "Decks" }).getAttribute("aria-current"), "page");
    const looks = page.getByRole("radiogroup", { name: "Deck overview look" });
    assert.equal(await looks.getByRole("radio", { name: "Tiles" }).getAttribute("aria-checked"), "true");
    const tile = page.getByRole("button", { name: "Tile deck, 3 due today" });
    await tile.waitFor();
    assert.equal(await tile.locator("svg").count(), 1, "a tile draws more than its one ring");
    assert.match((await tile.textContent()).replace(/\s+/g, " "), /3.*Tile deck|Tile deck.*3/);
    await page.emulateMedia({ reducedMotion: "reduce" });
    const details = page.getByRole("group", { name: "Tile deck details" });
    assert.match(await details.textContent(), /3 new/);
    assert.match(await details.textContent(), /no recall yet/i);
    const opacity = () => details.evaluate((node) => getComputedStyle(node).opacity);
    assert.equal(await opacity(), "0", "details show before hover");
    await tile.hover();
    assert.equal(await opacity(), "1", "details stay hidden on hover");
    await tile.click();
    await page.getByRole("heading", { name: "Tile deck", level: 2 }).waitFor();
    await looks.getByRole("radio", { name: "Graph" }).click();
    await page.getByRole("region", { name: "Flashcards", exact: true }).first().waitFor();
    assert.equal(await tile.count(), 0, "tiles stay beside the graph");
    await page.reload();
    await page.getByRole("region", { name: "Flashcards", exact: true }).first().waitFor();
    assert.equal(await looks.getByRole("radio", { name: "Graph" }).getAttribute("aria-checked"), "true", "the Graph look is not remembered");
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
    // Native automated drags are flaky in Chromium (and cancel on the tiny ↔ handle); dispatch the same DOM events.
    const dragOnto = async (source, target) => {
      const transfer = await page.evaluateHandle(() => new DataTransfer());
      await source.dispatchEvent("dragstart", { dataTransfer: transfer });
      await target.dispatchEvent("dragover", { dataTransfer: transfer });
      await target.dispatchEvent("drop", { dataTransfer: transfer });
      await source.dispatchEvent("dragend", { dataTransfer: transfer });
    };
    await dragOnto(block, slotButton("12:00"));
    await block.getByText("12:00–13:00").waitFor();
    await dragOnto(block.getByRole("button", { name: "Resize Move me" }), slotButton("14:30"));
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

  test("a side drawer edits events live and its title understands 'lunch with Mira fri 12:30'", async () => {
    const { page, problems, context } = await open("calendar", { dock: "hidden" });
    await page.getByRole("region", { name: "Week calendar" }).waitFor();
    await page.getByRole("button", { name: "Add event", exact: true }).first().click();
    const drawer = page.getByRole("dialog", { name: "New event" });
    await drawer.waitFor();
    assert.notEqual(await drawer.getAttribute("aria-modal"), "true", "the drawer should leave the calendar usable");
    const box = await drawer.boundingBox();
    assert.ok(box.x + box.width >= 1280 - 24 && box.width <= 440 && box.height >= 400, `drawer is not a right-hand side drawer: ${JSON.stringify(box)}`);
    await drawer.getByRole("textbox", { name: "Title" }).fill("lunch with Mira fri 12:30 at Café Rose");
    const understood = drawer.getByRole("status", { name: "Understood" });
    await understood.getByText("Lunch with Mira", { exact: true }).waitFor();
    assert.match(await understood.textContent(), /Fri[\s\S]*12:30–13:30[\s\S]*Café Rose/);
    for (const section of ["Repeat & time settings", "Notes, privacy & custom reminders", "Attendees, attachments & Google properties"])
      assert.equal(await drawer.getByText(section, { exact: false }).count() >= 1, true, `${section} is not reachable`);
    assert.equal(await drawer.getByRole("button", { name: "10 minutes before" }).getAttribute("aria-pressed"), "true");
    await drawer.getByRole("button", { name: "Add event" }).click();
    await drawer.waitFor({ state: "detached" });
    const saved = await page.evaluate(async () => {
      const from = new Date(Date.now() - 864e5).toISOString(), to = new Date(Date.now() + 9 * 864e5).toISOString();
      const snap = await fetch(`/api/calendar?start=${from}&end=${to}&timezone=UTC`).then((r) => r.json());
      const event = snap.events.find((e) => e.title === "Lunch with Mira" && e.location === "Café Rose");
      if (!event) return null;
      const start = new Date(event.start);
      return { day: start.getDay(), time: `${start.getHours()}:${start.getMinutes()}` };
    });
    assert.deepEqual(saved, { day: 5, time: "12:30" });

    await addEvent(page, { title: "Live edit", start: at(0, 10), end: at(0, 11) });
    await page.reload();
    const block = page.getByRole("group", { name: "Live edit" });
    await block.getByRole("button", { name: /^Edit Live edit/ }).click();
    const details = page.getByRole("dialog", { name: "Event details" });
    await details.getByRole("button", { name: "Later start" }).click();
    await block.getByText("10:15–11:15").waitFor();
    await details.getByRole("textbox", { name: "Title" }).fill("Live edit");
    assert.equal(await details.getByRole("status", { name: "Understood" }).count(), 0, "a plain title should not show a parse");
    await page.keyboard.press("Escape");
    await details.waitFor({ state: "detached" });
    await block.getByText("10:00–11:00").waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("notes tabs and floating outline", () => {
  test("browser-style page tabs, the library slides over the page, and 'On this page' jumps to blocks", async () => {
    const { page, problems, context } = await open("notes", { dock: "hidden" });
    const title = page.getByRole("textbox", { name: "Page title" });
    await title.waitFor();
    const tabs = page.getByRole("navigation", { name: "Open note pages" });
    assert.equal(await tabs.locator('[aria-current="page"]').count(), 1, "the open page is not the current tab");
    assert.equal(await tabs.getByRole("button", { name: "New page" }).count(), 1, "tab strip has no + new page");
    // The library is a drawer: hidden at rest, sliding over the page without moving it.
    const library = page.getByRole("navigation", { name: "Folders, notebooks and pages" });
    assert.equal(await library.isVisible(), false, "the library is open at rest");
    const before = await rect(title);
    const toggle = page.getByRole("button", { name: "Open library" });
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    await toggle.click();
    await library.waitFor();
    assert.equal(await toggle.getAttribute("aria-expanded"), "true");
    assert.deepEqual(await rect(title), before, "opening the library moved the page");
    await page.keyboard.press("Escape");
    await library.waitFor({ state: "hidden" });
    // Write three blocks; the floating outline lists them and jumps to one.
    await page.getByRole("button", { name: "Start writing a text note" }).click();
    await page.keyboard.type("Alpha: the first idea");
    await page.keyboard.press("Enter");
    await page.keyboard.type("Beta topic, second");
    await page.keyboard.press("Enter");
    await page.keyboard.type("Gamma closing words");
    const outline = page.getByRole("navigation", { name: "On this page" });
    await outline.waitFor();
    const entries = outline.getByRole("button", { name: /^(Alpha|Beta topic|Gamma closing words)$/ });
    await page.waitForFunction(() => document.querySelectorAll('nav[aria-label="On this page"] li button').length === 3);
    assert.equal(await entries.count(), 3, "outline does not list the three blocks");
    assert.match(await outline.textContent(), /10 words/);
    const box = await rect(outline), editor = await rect(page.getByRole("textbox", { name: "Block 1", exact: true }));
    assert.ok(box.left >= editor.right - 1 || box.top >= editor.bottom, "outline overlaps the writing");
    await outline.getByRole("button", { name: "Beta topic" }).click();
    await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Block 2");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

/** Opens the Study hub's Mind map tab (the cockpit opens on the session timer) and waits until
 *  the editor has loaded the map list and reopened the last map, so later steps are not undone. */
async function openMindMaps(page) {
  const listed = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/schema");
  await page.getByRole("navigation", { name: "Study tools" }).getByRole("button", { name: "Mind map", exact: true }).click();
  const { maps } = await (await listed).json();
  const schema = page.getByRole("region", { name: "Mental schema maps" });
  if (maps.length) await schema.getByRole("button", { name: "New map", exact: true }).waitFor();
  else await schema.waitFor();
  return schema;
}
/** Opens Study → Mind map and creates a fresh map from `template`; returns its node locator. */
async function newMindMap(page, title, template = "Lecture") {
  const schema = await openMindMaps(page);
  const fresh = schema.getByRole("button", { name: "New map", exact: true });
  const titleField = schema.getByRole("textbox", { name: "New map title" });
  if (await fresh.isVisible()) await fresh.click();
  await titleField.fill(title);
  await schema.locator("summary", { hasText: "Advanced options" }).click();
  await schema.getByRole("combobox", { name: "Starting template" }).selectOption(template);
  await schema.getByRole("button", { name: "Create map" }).click();
  await page.waitForFunction((label) => [...document.querySelectorAll("svg.stage [data-node]")].some((node) => node.textContent.includes(label)), title);
  return { schema, nodes: schema.locator("svg.stage [data-node]") };
}
/** Resolves a CSS colour expression (e.g. `var(--ink)`) to the computed rgb() string. */
const resolveColor = (page, value) => page.evaluate((color) => {
  const probe = document.createElement("i");
  probe.style.color = color;
  document.body.append(probe);
  const result = getComputedStyle(probe).color;
  probe.remove();
  return result;
}, value);

describe("mind-map pills", () => {
  test("nodes are compact capsules with a branch-coloured dot and an inverted ink root pill", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    const { nodes } = await newMindMap(page, "Pipelines");
    assert.equal(await nodes.count(), 4);
    const shapes = await nodes.evaluateAll((list) => list.map((node) => {
      const pill = node.querySelector("rect.pill"), dot = node.querySelector("circle.dot"), label = node.querySelector("text.label");
      const box = pill?.getBBox();
      return {
        text: label?.textContent, root: node.classList.contains("root"), height: box?.height, width: box?.width,
        rx: Number(pill?.getAttribute("rx")), fill: pill && getComputedStyle(pill).fill, ink: label && getComputedStyle(label).fill,
        dot: dot && getComputedStyle(dot).fill,
      };
    }));
    const root = shapes.find((shape) => shape.root);
    assert.ok(root, "no root pill");
    assert.equal(root.text, "Pipelines");
    assert.equal(root.fill, await resolveColor(page, "var(--ink)"), "root pill is not filled with ink");
    assert.equal(root.ink, await resolveColor(page, "var(--solid-void)"), "root label is not inverted");
    const branches = shapes.filter((shape) => !shape.root);
    assert.equal(branches.length, 3);
    const palette = await Promise.all([1, 2, 3, 4, 5, 6].map((n) => resolveColor(page, `var(--c${n})`)));
    for (const shape of [root, ...branches]) {
      assert.ok(shape.height <= 40, `${shape.text} is ${shape.height}px tall`);
      assert.equal(shape.rx, shape.height / 2, `${shape.text} is not a capsule`);
    }
    for (const shape of branches) {
      assert.ok(shape.width < 160, `${shape.text} is ${shape.width}px wide`);
      assert.ok(palette.includes(shape.dot), `${shape.text} has no branch-coloured dot`);
    }
    assert.equal(new Set(branches.map((shape) => shape.dot)).size, 3, "branches do not get distinct colours");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

/** Label of the focused mind-map node. */
const focusedNode = (page) => page.evaluate(() => document.activeElement?.closest("[data-node]")?.querySelector("text.label")?.textContent ?? null);
/** Map positions (top-left, world units) of the nodes whose labels are given. */
const nodeBoxes = (page) => page.evaluate(() => Object.fromEntries([...document.querySelectorAll("svg.stage [data-node]")].map((node) => {
  const [, x, y] = /translate\(([-\d.e]+),([-\d.e]+)\)/.exec(node.getAttribute("transform"));
  return [node.querySelector("text.label").textContent, { x: Number(x), y: Number(y) }];
})));
const mapNode = (page, label) => page.locator("svg.stage [data-node]").filter({ has: page.locator("text.label", { hasText: new RegExp(`^${label}$`) }) });
const centre = async (locator) => { const box = await locator.boundingBox(); return { x: box.x + box.width / 2, y: box.y + box.height / 2 }; };
/** Waits until the map editor takes input again: it ignores keys and drags while it saves, splits or undoes. */
const editable = (page) => page.waitForFunction(() => document.querySelector(".schema .mapbar input")?.disabled === false);

describe("mind-map editing", () => {
  test("keyboard building: arrows move, Ctrl+arrows reorder, Tab child, Enter sibling, Ctrl+Enter edits", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    await newMindMap(page, "Keyboard map");
    await page.waitForFunction(() => document.activeElement?.closest("[data-node]"));
    assert.equal(await focusedNode(page), "Keyboard map");
    await page.keyboard.press("ArrowRight");
    assert.equal(await focusedNode(page), "Key ideas");
    await page.keyboard.press("ArrowDown");
    assert.equal(await focusedNode(page), "Examples");
    await page.keyboard.press("ArrowUp");
    assert.equal(await focusedNode(page), "Key ideas");
    await page.keyboard.press("Control+ArrowDown");
    let boxes = await nodeBoxes(page);
    assert.ok(boxes["Key ideas"].y > boxes.Examples.y && boxes["Key ideas"].y < boxes.Questions.y, "Ctrl+Down did not move Key ideas below Examples");
    assert.equal(await focusedNode(page), "Key ideas");
    await page.keyboard.press("Control+ArrowUp");
    boxes = await nodeBoxes(page);
    assert.ok(boxes["Key ideas"].y < boxes.Examples.y, "Ctrl+Up did not move Key ideas back");
    await page.keyboard.press("Tab");
    await page.keyboard.type("Alpha");
    await page.keyboard.press("Enter");
    await page.keyboard.type("Beta");
    await page.keyboard.press("Escape");
    assert.equal(await focusedNode(page), "Beta");
    boxes = await nodeBoxes(page);
    for (const label of ["Alpha", "Beta"]) assert.ok(boxes[label].x > boxes["Key ideas"].x, `${label} is not a child of Key ideas`);
    assert.ok(boxes.Beta.y > boxes.Alpha.y, "Beta is not Alpha's next sibling");
    await page.keyboard.press("ArrowLeft");
    assert.equal(await focusedNode(page), "Key ideas");
    await page.keyboard.press("Control+Enter");
    const editor = page.getByRole("textbox", { name: "Node label" });
    await editor.waitFor();
    assert.equal(await editor.inputValue(), "Key ideas");
    await page.keyboard.press("End");
    await page.keyboard.type(" core");
    await page.keyboard.press("Escape");
    assert.equal(await focusedNode(page), "Key ideas core");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("dragging lights the drop target and reparents; D mid-drag or Detach breaks a branch off", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    const { schema } = await newMindMap(page, "Drag map");
    const pill = (label) => mapNode(page, label).locator("rect.pill");
    await mapNode(page, "Key ideas").click();
    await page.keyboard.press("Tab");
    await page.keyboard.type("Leaf");
    await page.keyboard.press("Escape");
    // Drag Leaf onto Questions: Questions lights up, and Leaf becomes its child.
    const from = await centre(pill("Leaf")), to = await centre(pill("Questions"));
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 4 });
    await page.mouse.move(to.x, to.y, { steps: 4 });
    assert.match(await mapNode(page, "Questions").getAttribute("class"), /drop-target/, "the drop target is not highlighted");
    assert.equal(await page.getByRole("status").filter({ hasText: /Drop on/ }).count(), 1, "no drag hint");
    await page.mouse.up();
    assert.doesNotMatch(await mapNode(page, "Questions").getAttribute("class"), /drop-target/);
    let boxes = await nodeBoxes(page);
    assert.ok(boxes.Leaf.x > boxes.Questions.x, "Leaf did not move under Questions");
    assert.ok(Math.abs((await centre(pill("Leaf"))).y - (await centre(pill("Questions"))).y) < 4, "Leaf is not Questions' only child");
    // Drag Examples to empty space and press D: it breaks off as a floating root where it was dropped.
    const start = await centre(pill("Examples"));
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(start.x + 40, start.y + 120, { steps: 5 });
    await page.keyboard.press("d");
    await page.getByRole("status").filter({ hasText: /^Detached/ }).waitFor();
    await page.mouse.move(start.x + 60, start.y + 150, { steps: 3 });
    await page.mouse.up();
    assert.match(await mapNode(page, "Examples").getAttribute("class"), /\broot\b/, "Examples was not detached");
    const dropped = await centre(pill("Examples"));
    assert.ok(Math.abs(dropped.x - (start.x + 60)) < 6 && Math.abs(dropped.y - (start.y + 150)) < 6, "the detached branch did not stay where it was dropped");
    // The inspector's Detach breaks Questions (with Leaf) off too.
    await mapNode(page, "Questions").click();
    await schema.getByRole("button", { name: "Detach branch" }).click();
    assert.match(await mapNode(page, "Questions").getAttribute("class"), /\broot\b/);
    boxes = await nodeBoxes(page);
    assert.ok(boxes.Leaf.x > boxes.Questions.x, "Leaf did not stay with Questions");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

/** Opens Notes on its daily page and switches the page to Canvas mode. */
async function openCanvas(page) {
  await page.getByRole("textbox", { name: "Page title" }).waitFor();
  await page.getByRole("group", { name: "Page mode" }).getByRole("button", { name: "Canvas", exact: true }).click();
  const canvas = page.getByLabel(/^Infinite drawing canvas/);
  await canvas.waitFor();
  return canvas;
}

describe("notes canvas pill", () => {
  test("the canvas tools are one icon pill at the stage bottom that collapses from its bottom edge and stays collapsed after reload", async () => {
    const { page, problems, context } = await open("notes", { dock: "hidden" });
    const canvas = await openCanvas(page);
    const tools = page.getByRole("toolbar", { name: "Drawing tools" });
    await tools.waitFor();
    assert.equal(await page.getByRole("toolbar", { name: /drawing tools/i }).count(), 1, "more than one drawing toolbar");
    // Tools are drawn icons with names and pressed state, not Unicode glyphs.
    for (const name of ["Select", "Pen", "Highlight", "Eraser", "Text", "Sticky"]) {
      const button = tools.getByRole("button", { name, exact: true });
      assert.equal(await button.locator("svg").count(), 1, `${name} has no drawn icon`);
      assert.equal((await button.textContent()).trim(), "", `${name} still shows a glyph`);
      assert.match(await button.getAttribute("aria-pressed"), /^(true|false)$/);
    }
    assert.equal(await tools.getByRole("group", { name: "Ink colours" }).count(), 1, "no ink colours in the pill");
    const size = tools.getByRole("button", { name: /^Stroke size/ });
    const before = await size.getAttribute("aria-label");
    await size.click();
    assert.notEqual(await size.getAttribute("aria-label"), before, "the stroke-size toggle does not change size");
    for (const name of ["Undo", "Redo"]) assert.equal(await tools.getByRole("button", { name, exact: true }).count(), 1, `no ${name}`);
    // Less-used tools and options stay reachable in the pill's overflow.
    await tools.getByLabel("More canvas tools").click();
    await tools.getByRole("button", { name: "Lasso", exact: true }).waitFor();
    assert.equal(await tools.getByRole("checkbox", { name: "Pen mode" }).isVisible(), true);
    await page.keyboard.press("Escape");
    // One floating pill, centred at the bottom of the canvas stage.
    const stage = await rect(canvas), pill = await rect(tools);
    assert.ok(Math.abs(pill.left + pill.width / 2 - (stage.left + stage.width / 2)) <= 2, "pill is not centred");
    assert.ok(pill.top > stage.top + stage.height / 2 && pill.bottom <= stage.bottom, `pill ${pill.top}–${pill.bottom} is not at the stage bottom ${stage.bottom}`);
    // Its bottom edge is the labelled handle; it collapses to a small handle and back.
    const handle = page.getByRole("button", { name: "Hide drawing tools" });
    assert.equal(await handle.getAttribute("aria-expanded"), "true");
    const edge = await rect(handle);
    assert.ok(edge.top >= pill.bottom - 1 && edge.bottom <= stage.bottom + 1, "the handle is not the pill's bottom edge");
    await handle.click();
    await tools.waitFor({ state: "hidden" });
    const show = page.getByRole("button", { name: "Show drawing tools" });
    assert.equal(await show.getAttribute("aria-expanded"), "false");
    const small = await rect(show);
    assert.ok(small.width <= 80 && small.height <= 24 && small.bottom <= stage.bottom + 1 && small.bottom >= stage.bottom - 24, "collapsed handle is not a small tab at the stage bottom");
    await page.reload();
    await openCanvas(page);
    await page.getByRole("button", { name: "Show drawing tools" }).waitFor();
    assert.equal(await tools.count(), 0, "the pill did not stay collapsed after reload");
    await page.getByRole("button", { name: "Show drawing tools" }).click();
    await tools.waitFor();
    assert.notEqual(await tools.evaluate((node) => getComputedStyle(node).animationName), "none", "the pill does not ease in");
    await page.emulateMedia({ reducedMotion: "reduce" });
    assert.equal(await tools.evaluate((node) => getComputedStyle(node).animationName), "none", "the pill still animates under reduced motion");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("canvas fullscreen keeps the same bottom pill instead of a second toolbar", async () => {
    const { page, problems, context } = await open("notes", { dock: "hidden" });
    await openCanvas(page);
    await page.getByRole("button", { name: "Open canvas fullscreen" }).click();
    await page.getByRole("button", { name: "Exit canvas fullscreen" }).waitFor();
    const tools = page.getByRole("toolbar", { name: "Drawing tools" });
    await tools.waitFor();
    assert.equal(await page.getByRole("toolbar", { name: /drawing tools/i }).count(), 1, "fullscreen adds a second toolbar");
    const pill = await rect(tools);
    const height = await page.evaluate(() => innerHeight);
    assert.ok(pill.bottom <= height && pill.bottom >= height - 48, `fullscreen pill ends at ${pill.bottom} of ${height}`);
    await page.getByRole("button", { name: "Hide drawing tools" }).click();
    await tools.waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "Show drawing tools" }).click();
    await tools.waitFor();
    assert.equal(await page.getByRole("button", { name: /^Save note/ }).count(), 1, "fullscreen lost its save control");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("notes canvas colours", () => {
  test("the pill shows the last 5 colours as round swatches; + opens all 12 named colours and a hex field; recents persist", async () => {
    const { page, problems, context } = await open("notes", { dock: "hidden" });
    await openCanvas(page);
    const inks = page.getByRole("toolbar", { name: "Drawing tools" }).getByRole("group", { name: "Ink colours" });
    await inks.waitFor();
    const recents = inks.getByRole("button", { name: /^Ink: / });
    assert.equal(await recents.count(), 5, "the recents row is not five swatches");
    const round = await recents.first().evaluate((node) => { const box = node.getBoundingClientRect(); return { round: getComputedStyle(node).borderRadius, w: box.width, h: box.height }; });
    assert.ok(round.w === round.h && round.w <= 28 && /50%|999|9999/.test(round.round), `swatches are not round dots: ${JSON.stringify(round)}`);
    const plus = inks.getByRole("button", { name: "All ink colours" });
    assert.equal(await plus.getAttribute("aria-expanded"), "false");
    const palette = page.getByRole("dialog", { name: "Ink palette" });
    const names = ["Paper", "Graphite", "Lavender", "Violet", "Blue", "Teal", "Sage", "Yellow", "Peach", "Orange", "Rose", "Red"];
    await plus.click();
    await palette.waitFor();
    assert.equal(await plus.getAttribute("aria-expanded"), "true");
    assert.deepEqual(await palette.getByRole("button").allTextContents().then((texts) => texts.map((text) => text.trim())), names, "the palette does not show the 12 colour names");
    assert.equal(await palette.getByRole("textbox", { name: "Ink hex" }).count(), 1, "the hex field is gone");
    await page.keyboard.press("Escape");
    await palette.waitFor({ state: "hidden" });
    // Pick six colours: the row keeps the last five, newest first, and the newest is the ink.
    const picks = ["Violet", "Teal", "Sage", "Peach", "Orange", "Red"];
    for (const name of picks) {
      await plus.click();
      await palette.getByRole("button", { name: `Ink: ${name}` }).click();
      await palette.waitFor({ state: "hidden" });
    }
    const expected = picks.slice(1).reverse().map((name) => `Ink: ${name}`);
    const labels = async () => recents.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label")));
    assert.deepEqual(await labels(), expected, "recents are not the last five picks");
    assert.equal(await recents.first().getAttribute("aria-pressed"), "true", "the newest pick is not the current ink");
    await recents.nth(2).click();
    assert.equal(await recents.nth(2).getAttribute("aria-pressed"), "true", "a recent swatch does not set the ink");
    await page.reload();
    await openCanvas(page);
    await inks.waitFor();
    assert.deepEqual(await labels(), expected, "recents did not persist");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("notes canvas background", () => {
  test("an empty page is the theme's flat void colour with no grid, and follows a theme switch", async () => {
    const { page, problems, context } = await open("notes", { dock: "hidden" });
    const canvas = await openCanvas(page);
    /** The canvas's top-left 240×120 CSS px: the distinct colours drawn there and the theme's --solid-void. */
    const sample = () => canvas.evaluate((node) => {
      const scale = node.width / node.getBoundingClientRect().width;
      const data = node.getContext("2d").getImageData(0, 0, Math.round(240 * scale), Math.round(120 * scale)).data;
      const seen = new Set();
      for (let i = 0; i < data.length; i += 4) seen.add(`#${[data[i], data[i + 1], data[i + 2]].map((c) => c.toString(16).padStart(2, "0")).join("")}`);
      return { colours: [...seen], void: getComputedStyle(document.documentElement).getPropertyValue("--solid-void").trim().toLowerCase() };
    });
    // While the page was in text mode the hidden stage measured nothing and the canvas kept its
    // 200px minimum; sample once it has been resized to fill the shown stage.
    await page.waitForFunction(() => {
      const node = document.querySelector("canvas[aria-label^='Infinite drawing canvas']");
      return node?.width > 0 && Math.abs(node.getBoundingClientRect().width - node.closest(".canvas-stage").clientWidth) < 1;
    });
    const kitty = await sample();
    assert.equal(kitty.void, "#1b151a", "the default theme is not Kitty");
    assert.deepEqual(kitty.colours, [kitty.void], "the empty canvas is not one flat void colour (grid, dots or nebula showed)");
    await page.evaluate(async () => (await import("/src/lib/themes.ts")).setTheme("harness"));
    await page.waitForFunction(() => getComputedStyle(document.documentElement).getPropertyValue("--solid-void").trim() === "#141414");
    await page.waitForFunction(() => {
      const node = document.querySelector("canvas[aria-label^='Infinite drawing canvas']");
      const [r, g, b] = node.getContext("2d").getImageData(2, 2, 1, 1).data;
      return r === 0x14 && g === 0x14 && b === 0x14;
    }, null, { timeout: 5_000 }).catch(() => {});
    const harness = await sample();
    assert.deepEqual(harness.colours, [harness.void], "the canvas did not repaint in the new theme's void colour");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("documents side-by-side", () => {
  test("numbered source sits beside the preview; the separator drags, arrow-keys and remembers; source scroll drives the preview", async () => {
    const { page, problems, context } = await open("documents", { dock: "hidden" });
    const source = page.getByRole("textbox", { name: "Markdown source" });
    await source.waitFor();
    assert.equal(await page.getByRole("button", { name: "Export PDF" }).count(), 1, "no Export PDF in the bar");
    assert.equal(await page.getByRole("button", { name: "Switch document" }).count(), 1, "no document switcher");
    const lines = Array.from({ length: 160 }, (_, i) => (i % 16 === 0 ? `## Section ${i / 16 + 1}` : `Line ${i + 1} of the cheat sheet.`));
    await source.fill(lines.join("\n"));
    const preview = page.getByRole("region", { name: "Document preview" });
    await page.waitForFunction(() => document.querySelector('[aria-label="Document preview"]')?.textContent?.includes("Section 10"));

    const [left, right] = [await rect(source), await rect(preview)];
    assert.ok(right.left >= left.right - 1, `preview (${right.left}) does not sit right of the source (${left.right})`);
    assert.ok(Math.abs(right.top - left.top) < 40, `source and preview are not side by side (${left.top} vs ${right.top})`);
    const gutter = page.locator('[data-testid="line-numbers"]');
    const numbers = await gutter.locator("span").evaluateAll((nodes) => nodes.map((node) => node.textContent).filter(Boolean));
    assert.equal(numbers.length, 160, "line numbers do not match the source lines");
    assert.deepEqual([numbers[0], numbers.at(-1)], ["1", "160"]);

    const separator = page.getByRole("separator", { name: "Resize source and preview" });
    const value = async () => Number(await separator.getAttribute("aria-valuenow"));
    const start = await value();
    await separator.focus();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    assert.ok((await value()) > start, "ArrowRight did not widen the source");
    assert.ok((await rect(source)).width > left.width + 10, "source pane did not grow with the keyboard");
    const keyed = await value();
    const handle = await separator.boundingBox();
    await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
    await page.mouse.down();
    await page.mouse.move(handle.x - 220, handle.y + handle.height / 2, { steps: 6 });
    await page.mouse.up();
    const dragged = await value();
    assert.ok(dragged < keyed - 5, `dragging left did not shrink the source (${keyed} → ${dragged})`);
    assert.ok((await rect(source)).width < left.width, "source pane did not shrink with the drag");

    await source.evaluate((node) => { node.scrollTop = (node.scrollHeight - node.clientHeight) / 2; });
    await page.waitForFunction(() => {
      const pane = document.querySelector('[aria-label="Document preview"]');
      return pane && Math.abs(pane.scrollTop / (pane.scrollHeight - pane.clientHeight) - 0.5) < 0.05;
    });
    const offsets = await page.evaluate(() => [
      document.querySelector('[data-testid="line-numbers"]').scrollTop,
      document.querySelector('textarea[aria-label="Markdown source"]').scrollTop,
    ]);
    assert.ok(Math.abs(offsets[0] - offsets[1]) <= 1, `line numbers drifted from the source (${offsets})`);

    await page.reload();
    await separator.waitFor();
    assert.equal(await value(), dragged, "split ratio was not remembered");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("mind-map free positioning", () => {
  test("Alt-drag places a node anywhere and keeps its parent; Reset position snaps it back into the layout", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    const { schema } = await newMindMap(page, "Free map");
    const pill = (label) => mapNode(page, label).locator("rect.pill");
    const stage = schema.locator("svg.stage");
    await stage.evaluate((svg) => svg.scrollIntoView({ block: "end" }));
    const before = await nodeBoxes(page);
    // Alt-drag Examples across Questions (no snap while Alt is held) into empty space.
    const start = await centre(pill("Examples")), over = await centre(pill("Questions")), origin = await rect(stage);
    await page.keyboard.down("Alt");
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(over.x, over.y, { steps: 5 });
    assert.doesNotMatch(await mapNode(page, "Questions").getAttribute("class"), /drop-target/, "Alt-drag still snaps into a target");
    await page.getByRole("status").filter({ hasText: /^Placing freely/ }).waitFor();
    await page.mouse.move(start.x + 180, start.y + 140, { steps: 5 });
    await page.mouse.up();
    await page.keyboard.up("Alt");
    // Positions are taken within the stage: the map toolbar above it rewraps when its status
    // text changes (more so with many merge targets), which moves the whole stage, not the node.
    const dropped = await centre(pill("Examples")), shifted = await rect(stage);
    const [dx, dy] = [dropped.x - shifted.left - (start.x - origin.left), dropped.y - shifted.top - (start.y - origin.top)];
    assert.ok(Math.abs(dx - 180) < 6 && Math.abs(dy - 140) < 6, "Examples did not stay where it was dropped");
    assert.doesNotMatch(await mapNode(page, "Examples").getAttribute("class"), /\broot\b/, "Alt-drag detached the node from its parent");
    assert.match(await mapNode(page, "Examples").getAttribute("aria-label"), /placed freely/);
    // A child added to the free node is laid out beside it, not back in the tree.
    await mapNode(page, "Examples").click();
    await page.keyboard.press("Tab");
    await page.keyboard.type("Case");
    await page.keyboard.press("Escape");
    let boxes = await nodeBoxes(page);
    assert.ok(boxes.Case.x > boxes.Examples.x && Math.abs(boxes.Case.y - boxes.Examples.y) < 4, "the free node's child is not laid out beside it");
    // Reset position puts Examples back between its siblings.
    await mapNode(page, "Examples").click();
    await schema.getByRole("button", { name: "Reset position" }).click();
    boxes = await nodeBoxes(page);
    assert.equal(boxes.Examples.x, before.Examples.x, "Examples is not back in its column");
    assert.ok(boxes.Examples.y > boxes["Key ideas"].y && boxes.Examples.y < boxes.Questions.y, "Examples is not back between its siblings");
    assert.doesNotMatch(await mapNode(page, "Examples").getAttribute("aria-label"), /placed freely/);
    assert.equal(await schema.getByRole("button", { name: "Reset position" }).isDisabled(), true);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("mind-map recolour", () => {
  test("a swatch row on the selection recolours a node or its whole branch; style copies and pastes; colours survive a reload", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    const { schema } = await newMindMap(page, "Colour map");
    const dot = (label) => mapNode(page, label).locator("circle.dot").evaluate((node) => getComputedStyle(node).fill);
    const colour = (n) => resolveColor(page, `var(--c${n})`);
    await mapNode(page, "Key ideas").click();
    await page.keyboard.press("Tab");
    await page.keyboard.type("Alpha");
    await page.keyboard.press("Escape");
    const alpha = await dot("Alpha");
    await mapNode(page, "Key ideas").click();
    const swatches = schema.getByRole("toolbar", { name: "Node style" });
    await swatches.getByRole("button", { name: "Colour 4" }).click();
    assert.equal(await dot("Key ideas"), await colour(4), "the node was not recoloured");
    assert.equal(await dot("Alpha"), alpha, "node-only recolour changed its child");
    await swatches.getByRole("button", { name: "Whole branch" }).click();
    await swatches.getByRole("button", { name: "Colour 5" }).click();
    assert.equal(await dot("Key ideas"), await colour(5));
    assert.equal(await dot("Alpha"), await colour(5), "branch recolour skipped the child");
    // Copy the style from Key ideas and paste it onto Questions (button) and Examples (Alt+V).
    await swatches.getByRole("button", { name: "Copy style" }).click();
    await mapNode(page, "Questions").click();
    await swatches.getByRole("button", { name: "Paste style" }).click();
    assert.equal(await dot("Questions"), await colour(5));
    await mapNode(page, "Examples").click();
    await page.keyboard.press("Alt+v");
    assert.equal(await dot("Examples"), await colour(5));
    await schema.getByRole("button", { name: /^Save map/ }).click();
    await schema.getByRole("button", { name: "Map saved" }).waitFor();
    await page.reload();
    await openMindMaps(page);
    await mapNode(page, "Questions").waitFor();
    assert.equal(await dot("Questions"), await colour(5), "the colour was not saved");
    await mapNode(page, "Questions").click();
    await swatches.getByRole("button", { name: "Automatic colour" }).click();
    assert.notEqual(await dot("Questions"), await colour(5), "Automatic colour did not clear the colour");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("mind-map cross connections", () => {
  test("Connect to… links any two topics with a labelled curve that stays out of the tree layout and folds with its ends", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    const { schema } = await newMindMap(page, "Link map");
    await mapNode(page, "Key ideas").click();
    await page.keyboard.press("Tab");
    await page.keyboard.type("Alpha");
    await page.keyboard.press("Escape");
    const before = await nodeBoxes(page);
    await mapNode(page, "Alpha").click();
    await schema.getByRole("button", { name: "Connect to…" }).click();
    await page.getByRole("status").filter({ hasText: /^Click a topic to connect “Alpha”/ }).waitFor();
    await mapNode(page, "Questions").click();
    const label = page.getByRole("textbox", { name: "Connection label" });
    await label.waitFor();
    await label.fill("explains");
    await label.press("Enter");
    const text = page.locator("svg.stage text.edge-label", { hasText: "explains" });
    await text.waitFor();
    assert.deepEqual(await nodeBoxes(page), before, "linking moved topics");
    assert.doesNotMatch(await mapNode(page, "Questions").getAttribute("class"), /\broot\b/);
    // The link is a curve, not a straight chord.
    const bend = await page.locator("svg.stage path.edge.cross").evaluate((path) => {
      const length = path.getTotalLength(), a = path.getPointAtLength(0), b = path.getPointAtLength(length), m = path.getPointAtLength(length / 2);
      return Math.abs((b.x - a.x) * (a.y - m.y) - (a.x - m.x) * (b.y - a.y)) / Math.hypot(b.x - a.x, b.y - a.y);
    });
    assert.ok(bend > 10, `the connection is not curved (${bend}px)`);
    await schema.getByRole("button", { name: "Tree layout" }).click();
    const relaid = await nodeBoxes(page);
    assert.equal(relaid.Questions.x, relaid.Examples.x, "the link pulled Questions out of its tree column");
    // Folding Key ideas hides Alpha and its link, but not Questions.
    await mapNode(page, "Key ideas").click();
    await page.keyboard.press(" ");
    assert.equal(await text.count(), 0, "the link stayed visible with its end folded away");
    assert.equal(await mapNode(page, "Questions").count(), 1);
    await page.keyboard.press(" ");
    await schema.getByRole("button", { name: /^Save map/ }).click();
    await schema.getByRole("button", { name: "Map saved" }).waitFor();
    await page.reload();
    await openMindMaps(page);
    await text.waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("mind-map multi-select", () => {
  test("Shift+arrows and a Shift-drag box select several topics to recolour, move and delete together", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    const { schema } = await newMindMap(page, "Bulk map");
    const pill = (label) => mapNode(page, label).locator("rect.pill");
    const dot = (label) => mapNode(page, label).locator("circle.dot").evaluate((node) => getComputedStyle(node).fill);
    const picked = () => page.locator("svg.stage [data-node].selected text.label").allTextContents();
    await schema.locator("svg.stage").evaluate((svg) => svg.scrollIntoView({ block: "end" }));
    await mapNode(page, "Key ideas").click();
    await page.keyboard.press("Shift+ArrowDown");
    await page.keyboard.press("Shift+ArrowDown");
    assert.deepEqual((await picked()).sort(), ["Examples", "Key ideas", "Questions"]);
    const toolbar = schema.getByRole("toolbar", { name: "Node style" });
    await toolbar.getByText("3 selected").waitFor();
    await toolbar.getByRole("button", { name: "Colour 6" }).click();
    const six = await resolveColor(page, "var(--c6)");
    for (const label of ["Key ideas", "Examples", "Questions"]) assert.equal(await dot(label), six, `${label} was not recoloured`);
    // A plain click selects one topic again; a Shift-drag box from empty space picks Examples and Questions.
    await mapNode(page, "Key ideas").click();
    assert.deepEqual(await picked(), ["Key ideas"]);
    const a = await pill("Examples").boundingBox(), b = await pill("Questions").boundingBox();
    await page.keyboard.down("Shift");
    await page.mouse.move(b.x + b.width + 40, b.y + b.height + 12);
    await page.mouse.down();
    await page.mouse.move(a.x + 10, a.y - 6, { steps: 6 });
    assert.equal(await page.locator("svg.stage rect.marquee").count(), 1, "no selection box is drawn");
    await page.mouse.up();
    await page.keyboard.up("Shift");
    assert.deepEqual((await picked()).sort(), ["Examples", "Questions"]);
    // Alt-dragging one of them moves both by the same amount.
    const before = await nodeBoxes(page), from = await centre(pill("Examples"));
    await page.keyboard.down("Alt");
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(from.x + 150, from.y + 60, { steps: 6 });
    await page.mouse.up();
    await page.keyboard.up("Alt");
    const after = await nodeBoxes(page);
    for (const label of ["Examples", "Questions"]) {
      assert.ok(after[label].x - before[label].x > 80 && after[label].y - before[label].y > 30, `${label} did not move with the selection`);
    }
    assert.ok(Math.abs((after.Examples.x - before.Examples.x) - (after.Questions.x - before.Questions.x)) < 2, "the selection did not move together");
    assert.deepEqual((await picked()).sort(), ["Examples", "Questions"], "the drag dropped the selection");
    // Delete removes both.
    await page.keyboard.press("Delete");
    assert.equal(await mapNode(page, "Examples").count(), 0);
    assert.equal(await mapNode(page, "Questions").count(), 0);
    assert.equal(await mapNode(page, "Key ideas").count(), 1);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("mind-map history", () => {
  test("undo and redo restore drags and colours across saving, and a new edit clears redo", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    const { schema } = await newMindMap(page, "History map");
    await mapNode(page, "Examples").click();
    const before = await nodeBoxes(page);
    const p = await centre(mapNode(page, "Examples").locator("rect.pill"));
    await page.keyboard.down("Alt");
    await page.mouse.move(p.x, p.y); await page.mouse.down();
    await page.mouse.move(p.x + 100, p.y + 70, { steps: 4 }); await page.mouse.up();
    await page.keyboard.up("Alt");
    const moved = await nodeBoxes(page);
    await page.keyboard.press("Control+z");
    assert.deepEqual((await nodeBoxes(page)).Examples, before.Examples);
    await page.keyboard.press("Control+Shift+z");
    assert.deepEqual((await nodeBoxes(page)).Examples, moved.Examples);
    await mapNode(page, "Examples").click();
    await schema.getByRole("button", { name: "Colour 4", exact: true }).click();
    await schema.getByRole("button", { name: /^Save map/ }).click();
    await schema.getByRole("button", { name: "Map saved" }).waitFor();
    await editable(page);
    await page.keyboard.press("Control+z");
    const dot = () => mapNode(page, "Examples").locator("circle.dot").evaluate(n => getComputedStyle(n).fill);
    assert.notEqual(await dot(), await resolveColor(page, "var(--c4)"));
    await page.keyboard.press("Control+Shift+z");
    assert.equal(await dot(), await resolveColor(page, "var(--c4)"));
    await page.keyboard.press("Control+z");
    await mapNode(page, "Questions").click();
    await schema.getByRole("button", { name: "Colour 5", exact: true }).click();
    assert.equal(await schema.getByRole("button", { name: "Redo", exact: true }).isDisabled(), true);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("mind-map split and merge", () => {
  test("split creates linked maps, paired undo/redo works, and dropping the root merges it back", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    const { schema } = await newMindMap(page, "Transfer map");
    await mapNode(page, "Key ideas").click(); await page.keyboard.press("Tab");
    await page.keyboard.type("Leaf"); await page.keyboard.press("Escape");
    await mapNode(page, "Key ideas").click();
    await schema.getByRole("button", { name: "Split branch into map", exact: true }).click();
    await page.waitForFunction(() => document.querySelector('select') && [...document.querySelectorAll('svg.stage text.label')].length === 2);
    await mapNode(page, "Leaf").waitFor();
    await editable(page);
    assert.equal(await mapNode(page, "Transfer map").count(), 0);
    await page.keyboard.press("Control+z");
    await page.waitForFunction(() => document.querySelectorAll('svg.stage [data-node]').length === 0);
    await editable(page);
    await page.keyboard.press("Control+Shift+z");
    await mapNode(page, "Key ideas").waitFor();
    await editable(page);
    const root = await centre(mapNode(page, "Key ideas").locator("rect.pill"));
    const target = schema.getByRole("group", { name: "Merge map drop targets" }).getByRole("button", { name: "Transfer map", exact: true });
    const to = await centre(target);
    await page.mouse.move(root.x, root.y); await page.mouse.down();
    await page.mouse.move(to.x, to.y, { steps: 8 }); await page.mouse.up();
    await mapNode(page, "Transfer map").waitFor();
    await mapNode(page, "Leaf").waitFor();
    assert.equal(await mapNode(page, "Key ideas").count(), 1);
    await page.reload();
    await openMindMaps(page);
    await mapNode(page, "Leaf").waitFor();
    assert.equal(await mapNode(page, "Key ideas").count(), 1);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("pipeline swimlanes", () => {
  test("rows are tracks and columns open stages; dragging or Shift+arrows moves a card, and Undo puts it back", async () => {
    const { page, problems, context } = await open("pipeline", { dock: "hidden" });
    const board = page.getByRole("grid", { name: "Applications by track and stage" });
    await board.waitFor();
    const heads = await board.getByRole("columnheader").allTextContents();
    assert.deepEqual(heads.map((text) => text.trim()).filter(Boolean), ["Applied", "Assessment", "Interview", "Offer", "Closed"]);
    const lanes = await board.getByRole("rowheader").allTextContents();
    assert.ok(lanes.some((text) => text.includes("Quant")) && lanes.some((text) => text.includes("Gpu")), `lanes: ${lanes}`);
    const cell = (lane, stage) => board.getByRole("gridcell", { name: `${lane} · ${stage}` });
    const card = page.getByRole("button", { name: /^Tidewater Markets \(demo\)/ });
    assert.equal(await cell("Quant", "Applied").getByRole("button", { name: /^Tidewater/ }).count(), 1);
    // Drag into another track's Assessment cell: stage and track change together.
    const from = await card.boundingBox();
    const to = await cell("Gpu", "Assessment").boundingBox();
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 8 });
    await page.mouse.up();
    await cell("Gpu", "Assessment").getByRole("button", { name: /^Tidewater/ }).waitFor();
    const pill = page.getByRole("status").filter({ hasText: "Tidewater Markets (demo) moved to Assessment · Gpu" });
    await pill.waitFor();
    await pill.getByRole("button", { name: "Undo" }).click();
    await cell("Quant", "Applied").getByRole("button", { name: /^Tidewater/ }).waitFor();
    // Keyboard: Shift+→ advances the stage, Shift+↓ moves to the next track's lane.
    await cell("Quant", "Applied").getByRole("button", { name: /^Tidewater/ }).focus();
    await page.keyboard.press("Shift+ArrowRight");
    await cell("Quant", "Assessment").getByRole("button", { name: /^Tidewater/ }).waitFor();
    await page.keyboard.press("Shift+ArrowDown");
    await cell("Gpu", "Assessment").getByRole("button", { name: /^Tidewater/ }).waitFor();
    // The board reloads once the queued saves land and then refocuses the card.
    await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label")?.startsWith("Tidewater"), null, { timeout: 5_000 })
      .catch(() => assert.fail("focus did not follow the card"));
    await page.getByRole("status").filter({ hasText: "Tidewater Markets (demo) moved to Gpu" }).getByRole("button", { name: "Undo" }).click();
    await cell("Quant", "Assessment").getByRole("button", { name: /^Tidewater/ }).waitFor();
    // The status history is append-only: the move and its undo are both recorded.
    const jobId = await page.evaluate(async () => {
      const board = await (await fetch("/api/pipeline")).json();
      return Object.values(board.columns).flat().find((card) => card.company_name.startsWith("Tidewater")).job_id;
    });
    const history = await page.evaluate(async (id) => (await (await fetch(`/api/jobs/${id}`)).json()).application.history, jobId);
    assert.ok(history.some((event) => event.undoes_event_id), "undo did not append a reverting event");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("agent status cards", () => {
  test("the dock's Agents view shows one live card per herdr agent; a blocked one offers Open pane", async () => {
    const { page, problems, context } = await open("today", { dock: "hidden" });
    await context.addInitScript(() => localStorage.setItem("kitty.dock", JSON.stringify({ mode: "activity", shown: "activity", size: null, offset: { right: 16, bottom: 16 } })));
    let polls = 0;
    const started = Date.now() - 75_000;
    await context.route("**/api/terminal/activity", (route) => {
      polls += 1;
      route.fulfill({ json: { running: true, workspaces: [
        { id: "w1", label: "synthetic", status: "blocked", focused: true, agents: [
          { pane: "w1:p1", name: "omp", status: "working", task: "Synthetic task: tidy fixtures", line: "run pytest · 4 passed", revision: 10 + polls * 7, since: started },
          { pane: "w1:p2", name: "codex", status: "blocked", task: "Synthetic review", line: "needs permission: read notes", revision: 3, since: started },
        ] },
      ] } });
    });
    await context.route("**/api/terminal/session", (route) => route.fulfill({ status: 503, json: { error: "No terminal in this test" } }));
    const focused = [];
    await context.route("**/api/terminal/focus", (route) => {
      focused.push(route.request().postDataJSON());
      route.fulfill({ json: { ok: true } });
    });
    await page.reload();
    const dock = page.getByRole("region", { name: "Agent activity" });
    const working = dock.getByRole("article", { name: /^omp/ });
    const blocked = dock.getByRole("article", { name: /^codex/ });
    await working.waitFor();
    assert.match(await working.textContent(), /Synthetic task: tidy fixtures/);
    assert.match(await working.textContent(), /run pytest · 4 passed/);
    assert.match(await working.textContent(), /synthetic/i, "workspace label missing");
    const timer = working.locator("time");
    assert.match(await timer.textContent(), /^1:1\d$/);
    await page.waitForFunction(() => document.querySelectorAll('[aria-label="Agent activity"] svg[role="img"] path.line').length === 2);
    assert.match(await working.getByRole("img").getAttribute("aria-label"), /output over the last 30 seconds/);
    assert.equal(await working.getByRole("button", { name: "Open pane" }).count(), 0, "a working agent offers Open pane");
    await blocked.getByRole("button", { name: "Open pane" }).click();
    await page.waitForFunction(() => document.querySelector('[aria-label="Dock contents"] button[aria-pressed="true"]')?.textContent === "Terminal");
    assert.deepEqual(focused, [{ pane: "w1:p2" }]);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("flashcard memory stats", () => {
  test("Stats shows today's queue, a 30-day forecast, retention curves, maturity and a 26-week heatmap for the scope", async () => {
    const { page, problems, context } = await open("flashcards", { dock: "hidden" });
    const views = page.getByRole("navigation", { name: "Flashcard views" });
    await views.waitFor();
    await seedDeck(page, "Stats deck", [["Sun?", "Star"], ["Moon?", "Satellite"], ["Mars?", "Planet"], ["Io?", "Moon"]]);
    // One card graduates straight to review (Easy), one stays in learning (Again).
    await page.evaluate(async () => {
      const post = async (path, body) => (await fetch(`/api${path}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Kitty-Request": "1" }, body: JSON.stringify(body) })).json();
      const deck = (await (await fetch("/api/flashcards", { cache: "no-store" })).json()).decks.find((item) => item.name === "Stats deck");
      for (const rating of [4, 1]) {
        const { card } = await post("/flashcards/review", { deck_id: deck.id, search: "", mode: "regular" });
        await post("/flashcards/reveal", { token: card.token });
        await post("/flashcards/grade", { token: card.token, rating, duration_ms: 6000 });
      }
    });
    await page.reload();
    await page.getByRole("combobox", { name: /deck scope/ }).selectOption({ label: "Stats deck" });
    await views.getByRole("button", { name: "Stats" }).click();
    const panel = (name) => page.getByRole("region", { name });
    const queue = panel("Today's queue");
    await queue.waitFor();
    assert.match((await queue.textContent()).replace(/\s+/g, " "), /2 cards today/);
    for (const [count, label] of [["0", "Learning"], ["0", "Review"], ["2", "New"]])
      assert.equal(await queue.getByRole("listitem").filter({ hasText: label }).locator("b").textContent(), count, `${label} count`);
    for (const chip of ["0 suspended", "0 leeches", "0 buried"]) await queue.getByText(chip).waitFor();
    const forecast = panel("Due forecast · 30 days");
    assert.equal(await forecast.locator("svg title").count(), 30, "forecast does not cover 30 days");
    assert.match(await forecast.locator("svg title").first().textContent(), /today · 1 due \(1 learning\)/);
    const retention = panel("Retention curve");
    assert.match(await retention.getByRole("img").getAttribute("aria-label"), /weak.*medium.*strong.*90% target/);
    const maturity = panel("Maturity");
    for (const [label, count] of [["New", "2"], ["Learning", "1"], ["Young", "1"], ["Mature", "0"]])
      assert.equal(await maturity.getByRole("listitem").filter({ hasText: label }).locator("b").textContent(), count, `${label} maturity`);
    const heatmap = panel("Reviews per day · 26 weeks");
    assert.equal(await heatmap.locator("[title$='reviews'], [title$='review']").count(), 182, "heatmap does not show 182 days");
    assert.match(await heatmap.textContent(), /1-day streak/);
    assert.equal(await page.getByText(/last 90 active days/).count(), 0, "the 90-day bar chart is still shown");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("reminder actions", () => {
  test("hover reveals edit, snooze and delete; swiping right snoozes and left deletes, each with Undo", async () => {
    const { page, problems, context } = await open("reminders", { dock: "hidden" });
    const form = page.locator("form").filter({ has: page.getByRole("button", { name: "Add reminder" }) });
    await form.getByLabel("When").selectOption("+7");
    await form.getByLabel("Reminder").fill("Synthetic swipe check");
    await form.getByRole("button", { name: "Add reminder" }).click();
    const row = page.getByRole("group", { name: /Synthetic swipe check/ });
    await row.waitFor();
    const snooze = row.getByRole("button", { name: "Snooze a day" });
    const shown = () => snooze.evaluate((node) => getComputedStyle(node.parentElement).opacity);
    await page.mouse.move(0, 0);
    assert.equal(await shown(), "0", "actions show before hover");
    await row.hover();
    await page.waitForFunction(() => getComputedStyle(document.querySelector('[aria-label*="Synthetic swipe check"] .acts')).opacity === "1");
    const due = () => row.locator(".due").textContent();
    const before = await due();
    await snooze.click();
    const pill = page.getByRole("status").filter({ hasText: "Snoozed a day: Synthetic swipe check" });
    await pill.waitFor();
    await page.waitForFunction((old) => document.querySelector('[aria-label*="Synthetic swipe check"] .due')?.textContent !== old, before);
    await pill.getByRole("button", { name: "Undo" }).click();
    await page.waitForFunction((old) => document.querySelector('[aria-label*="Synthetic swipe check"] .due')?.textContent === old, before);
    // Edit in place.
    await row.hover();
    await row.getByRole("button", { name: "Edit" }).click();
    await page.getByRole("textbox", { name: "Reminder text" }).fill("Synthetic swipe check, edited");
    await page.keyboard.press("Enter");
    const edited = page.getByRole("group", { name: /Synthetic swipe check, edited/ });
    await edited.waitFor();
    const swipe = async (dx) => {
      await edited.scrollIntoViewIfNeeded();
      const box = await edited.boundingBox();
      // Start left of centre: the undo pill floats over the middle of the list.
      const y = box.y + box.height / 2;
      const x = box.x + 180;
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x + dx, y, { steps: 10 });
      await page.mouse.up();
    };
    await swipe(160);
    await page.getByRole("status").filter({ hasText: "Snoozed a day" }).waitFor();
    await swipe(-160);
    const deleted = page.getByRole("status").filter({ hasText: "Deleted: Synthetic swipe check, edited" });
    await deleted.waitFor();
    await edited.waitFor({ state: "detached" });
    await deleted.getByRole("button", { name: "Undo" }).click();
    await edited.waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("flashcard card browser", () => {
  test("split browser: filter the list, flip the preview, read memory meters and last reviews, suspend and bury", async () => {
    const { page, problems, context } = await open("flashcards", { dock: "hidden" });
    const views = page.getByRole("navigation", { name: "Flashcard views" });
    await views.waitFor();
    await seedDeck(page, "Browser deck", [["Capital of Peru?", "Lima"], ["Capital of Kenya?", "Nairobi"], ["Capital of Japan?", "Tokyo"]]);
    await page.evaluate(async () => {
      const post = async (path, body) => (await fetch(`/api${path}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Kitty-Request": "1" }, body: JSON.stringify(body) })).json();
      const deck = (await (await fetch("/api/flashcards", { cache: "no-store" })).json()).decks.find((item) => item.name === "Browser deck");
      const { card } = await post("/flashcards/review", { deck_id: deck.id, search: "", mode: "regular" });
      await post("/flashcards/reveal", { token: card.token });
      await post("/flashcards/grade", { token: card.token, rating: 4, duration_ms: 5000 });
    });
    await page.reload();
    await page.getByRole("combobox", { name: /deck scope/ }).selectOption({ label: "Browser deck" });
    await views.getByRole("button", { name: "Cards" }).click();
    const list = page.getByRole("list", { name: "Cards" });
    await list.getByRole("listitem").nth(2).waitFor();
    assert.equal(await list.getByRole("listitem").count(), 3);
    const filter = page.getByRole("searchbox", { name: "Filter cards" });
    await filter.fill("Kenya");
    await filter.press("Enter");
    await page.waitForFunction(() => document.querySelectorAll('[aria-label="Cards"] li').length === 1);
    await filter.fill("");
    await filter.press("Enter");
    await list.getByRole("listitem").nth(2).waitFor();
    // The detail pane sits beside the list.
    const detail = page.getByRole("region", { name: "Card details" });
    await list.getByRole("button", { name: /Peru/ }).click();
    const [listBox, detailBox] = [await list.boundingBox(), await detail.boundingBox()];
    assert.ok(detailBox.x >= listBox.x + listBox.width - 1, "the detail pane is not beside the list");
    const preview = detail.getByRole("button", { name: "Flip card preview" });
    await page.waitForFunction(() => document.querySelector('[aria-label="Flip card preview"]')?.textContent.includes("Peru"));
    await preview.click();
    await page.waitForFunction(() => document.querySelector('[aria-label="Flip card preview"]')?.textContent.includes("Lima"));
    assert.match(await detail.getByRole("meter", { name: "Stability" }).getAttribute("aria-valuetext"), /^\d+(\.\d)? ?d/);
    assert.match(await detail.getByRole("meter", { name: "Difficulty" }).getAttribute("aria-valuetext"), /^\d+(\.\d)? of 10$/);
    assert.match(await detail.getByRole("meter", { name: "Recall now" }).getAttribute("aria-valuetext"), /^\d+%$/);
    const reviews = detail.getByRole("list", { name: "Last reviews" }).getByRole("listitem");
    assert.equal(await reviews.count(), 1);
    assert.match(await reviews.first().getAttribute("aria-label"), /Easy/);
    await detail.getByRole("button", { name: "Suspend" }).click();
    await detail.getByRole("button", { name: "Unsuspend" }).waitFor();
    await detail.getByText("suspended", { exact: true }).waitFor();
    await detail.getByRole("button", { name: "Bury today" }).click();
    await detail.getByRole("button", { name: "Unbury" }).waitFor();
    await list.getByRole("button", { name: /Kenya/ }).click();
    await page.waitForFunction(() => document.querySelector('[aria-label="Flip card preview"]')?.textContent.includes("Kenya"));
    assert.equal(await detail.getByRole("meter", { name: "Recall now" }).getAttribute("aria-valuetext"), "not studied yet");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

/** The fronts saved in deck `name`, read back through the API. */
const deckFronts = (page, name) => page.evaluate(async (name) => {
  const deck = (await (await fetch("/api/flashcards", { cache: "no-store" })).json()).decks.find((item) => item.name === name);
  return (await (await fetch(`/api/flashcards/fronts?deck_id=${deck.id}`, { cache: "no-store" })).json()).fronts;
}, name);
const focused = (locator) => locator.evaluate((node) => node === document.activeElement);

describe("adding flashcards", () => {
  test("inline add row: type the front, Tab, the back, Enter saves and the next row is ready", async () => {
    const { page, problems, context } = await open("flashcards", { dock: "hidden" });
    const views = page.getByRole("navigation", { name: "Flashcard views" });
    await views.waitFor();
    await seedDeck(page, "Inline deck", [["Capital of Peru?", "Lima"]]);
    await page.reload();
    await views.getByRole("button", { name: "Cards" }).click();
    const row = page.getByRole("form", { name: "Add a card" });
    // With All decks in scope the row asks which deck to save into.
    await row.getByRole("combobox", { name: "Deck for the new card" }).waitFor();
    await page.getByRole("combobox", { name: /deck scope/ }).selectOption({ label: "Inline deck" });
    await page.waitForFunction(() => !document.querySelector('[aria-label="Deck for the new card"]'));
    const list = page.getByRole("list", { name: "Cards" });
    await list.getByRole("listitem").first().waitFor();
    const front = row.getByRole("textbox", { name: "New card front" });
    const back = row.getByRole("textbox", { name: "New card back" });
    await front.click();
    await page.keyboard.type("Capital of Chile?");
    await page.keyboard.press("Tab");
    assert.equal(await focused(back), true, "Tab does not move to the back");
    await page.keyboard.type("Santiago");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.querySelectorAll('[aria-label="Cards"] li').length === 2);
    assert.equal(await front.inputValue(), "");
    assert.equal(await back.inputValue(), "");
    assert.equal(await focused(front), true, "the next row is not ready in the front field");
    await row.getByText(/Added to Inline deck/).waitFor();
    // The next card straight away; Enter in the front with no back moves to the back.
    await page.keyboard.type("Capital of Japan?");
    await page.keyboard.press("Enter");
    assert.equal(await focused(back), true, "Enter with an empty back does not move to the back");
    await page.keyboard.type("Tokyo");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.querySelectorAll('[aria-label="Cards"] li').length === 3);
    // A front already in the deck is flagged and needs a second Enter.
    await page.keyboard.type("capital of  PERU?");
    await row.getByText(/already in this deck/).waitFor();
    await page.keyboard.press("Tab");
    await page.keyboard.type("Lima again");
    await page.keyboard.press("Enter");
    await page.waitForTimeout(400);
    assert.equal(await list.getByRole("listitem").count(), 3, "a duplicate was saved without confirmation");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.querySelectorAll('[aria-label="Cards"] li').length === 4);
    assert.deepEqual((await deckFronts(page, "Inline deck")).sort(),
      ["Capital of Chile?", "Capital of Japan?", "Capital of Peru?", "capital of  PERU?"].sort());
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("quick add: N on Flashcards and Ctrl K 'New card' open a front/back popover with the deck preselected", async () => {
    const { page, problems, context } = await open("flashcards", { dock: "hidden" });
    const views = page.getByRole("navigation", { name: "Flashcard views" });
    await views.waitFor();
    await seedDeck(page, "Other deck", [["Unrelated", "x"]]);
    await seedDeck(page, "Quick deck", [["Capital of Peru?", "Lima"]]);
    await page.reload();
    await page.getByRole("combobox", { name: /deck scope/ }).selectOption({ label: "Quick deck" });
    await page.locator("h1").click();
    await page.keyboard.press("n");
    const popover = page.getByRole("dialog", { name: "Quick add card" });
    await popover.waitFor();
    await popover.getByRole("combobox", { name: "Deck" }).waitFor();
    await page.waitForFunction(() => document.querySelector('dialog[aria-label="Quick add card"] select')?.selectedOptions[0]?.textContent === "Quick deck");
    const front = popover.getByRole("textbox", { name: "Front" });
    const back = popover.getByRole("textbox", { name: "Back" });
    assert.equal(await focused(front), true, "the front is not focused");
    await page.keyboard.type("Capital of Peru?");
    await popover.getByText(/already in this deck/).waitFor();
    await front.fill("Capital of Spain?");
    await page.waitForFunction(() => !document.querySelector('dialog[aria-label="Quick add card"]')?.textContent.includes("already in this deck"));
    await page.keyboard.press("Tab");
    assert.equal(await focused(back), true, "Tab does not move to the back");
    await page.keyboard.type("Madrid");
    await page.keyboard.press("Enter");
    await popover.getByText(/Added to Quick deck/).waitFor();
    assert.equal(await front.inputValue(), "");
    assert.equal(await focused(front), true);
    await page.keyboard.press("Escape");
    await popover.waitFor({ state: "detached" });
    // N while typing in a field is just a letter.
    await views.getByRole("button", { name: "Cards" }).click();
    const filter = page.getByRole("searchbox", { name: "Filter cards" });
    await filter.click();
    await page.keyboard.press("n");
    assert.equal(await filter.inputValue(), "n");
    assert.equal(await popover.count(), 0, "N opened the popover while typing");
    // Ctrl K 'New card' works from another page, preselecting the last-used deck.
    await page.evaluate(() => { location.hash = "#calendar"; });
    await page.waitForFunction(() => location.hash === "#calendar");
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Search kitty!" });
    await palette.getByRole("combobox").fill("new card");
    await page.waitForFunction(() => document.querySelector('[role="option"][data-top="true"]')?.textContent?.includes("New card"));
    await page.keyboard.press("Enter");
    await popover.waitFor();
    assert.equal(await page.evaluate(() => location.hash), "#calendar", "quick add left the current page");
    await page.waitForFunction(() => document.querySelector('dialog[aria-label="Quick add card"] select')?.selectedOptions[0]?.textContent === "Quick deck");
    await page.keyboard.type("Capital of Spain?");
    await popover.getByText(/already in this deck/).waitFor();
    await front.fill("Capital of Italy?");
    await back.fill("Rome");
    await page.keyboard.press("Control+Enter");
    await popover.getByText(/Added to Quick deck/).waitFor();
    // Empty faces are refused by the same check.
    await page.keyboard.press("Control+Enter");
    await popover.getByText("Write the front (question) first.").waitFor();
    await page.keyboard.press("Escape");
    await popover.waitFor({ state: "detached" });
    assert.deepEqual((await deckFronts(page, "Quick deck")).sort(), ["Capital of Italy?", "Capital of Peru?", "Capital of Spain?"]);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

/** Dispatches a synthetic PointerEvent at a viewport point, on whatever is drawn there (like a real pointer). */
const pointer = (page, type, x, y, init = {}) => page.evaluate(({ type, x, y, init }) => {
  const stage = document.querySelector("svg.stage"), hit = document.elementFromPoint(x, y);
  // Leaving fires on the stage itself; everything else lands on what is under the pointer.
  const target = type === "pointerleave" || !hit ? stage : hit;
  target.dispatchEvent(new PointerEvent(type, { bubbles: type !== "pointerleave", cancelable: true, composed: true, clientX: x, clientY: y, isPrimary: true, ...init }));
}, { type, x, y, init });
/** Synthetic pointers are not active device pointers, so capturing one is a no-op in these tests. */
const capturable = (page) => page.evaluate(() => {
  const capture = Element.prototype.setPointerCapture;
  Element.prototype.setPointerCapture = function (id) { try { capture.call(this, id); } catch { /* not a device pointer */ } };
});
/** A pen (or other pointer) press, path and lift; `init` sets pointerType, pressure, button and buttons. */
async function stroke(page, points, { pressure = () => 0.5, ...init } = {}) {
  const pressed = { pointerId: 7, pointerType: "pen", button: 0, buttons: 1, ...init };
  await pointer(page, "pointerdown", points[0].x, points[0].y, { ...pressed, pressure: pressure(0) });
  for (const [index, point] of points.slice(1).entries()) await pointer(page, "pointermove", point.x, point.y, { ...pressed, button: -1, pressure: pressure(index + 1) });
  const last = points.at(-1);
  await pointer(page, "pointerup", last.x, last.y, { ...pressed, buttons: 0, pressure: 0 });
}

describe("mind-map pen", () => {
  test("ink sticks to the nearest topic, moves, folds and saves with it; the barrel lasso selects; the eraser removes only ink; palms are ignored", async () => {
    const { page, problems, context } = await open("study", { dock: "hidden" });
    const { schema } = await newMindMap(page, "Pen map");
    const pill = (label) => mapNode(page, label).locator("rect.pill");
    const strokes = page.locator("svg.stage g.ink path[data-stroke]");
    const picked = () => page.locator("svg.stage [data-node].selected text.label").allTextContents();
    await schema.locator("svg.stage").evaluate((svg) => svg.scrollIntoView({ block: "end" }));
    await capturable(page);
    const examplesId = await mapNode(page, "Examples").getAttribute("data-node");
    // Ink mode: a pressure-varying wavy stroke across Examples attaches to Examples.
    const ink = schema.getByRole("button", { name: "Ink", exact: true });
    await ink.click();
    assert.equal(await ink.getAttribute("aria-pressed"), "true");
    const e = await pill("Examples").boundingBox();
    const wave = Array.from({ length: 40 }, (_, i) => ({ x: e.x + 8 + i * (e.width - 16) / 39, y: e.y + e.height / 2 + Math.sin(i / 3) * 0.4 }));
    await stroke(page, wave, { pressure: (i) => 0.2 + (0.7 * i) / 39 });
    assert.equal(await strokes.count(), 1, "no ink stroke was drawn");
    assert.equal(await strokes.getAttribute("data-ink-node"), examplesId, "the stroke is not attached to the nearest topic");
    // Folding the root hides the topic and its ink.
    await ink.click();
    await mapNode(page, "Pen map").click();
    await page.keyboard.press(" ");
    assert.equal(await strokes.count(), 0, "ink stayed visible with its topic folded away");
    await page.keyboard.press(" ");
    assert.equal(await strokes.count(), 1);
    // An Alt pen drag moves Examples freely, and the ink travels with it.
    const inkBefore = await strokes.boundingBox(), from = await centre(pill("Examples"));
    await stroke(page, [from, { x: from.x + 80, y: from.y + 60 }, { x: from.x + 170, y: from.y + 110 }], { altKey: true });
    const moved = await centre(pill("Examples")), inkAfter = await strokes.boundingBox();
    assert.ok(Math.abs(moved.x - from.x - 170) < 3 && Math.abs(moved.y - from.y - 110) < 3, "the pen did not drag Examples");
    assert.ok(Math.abs(inkAfter.x - inkBefore.x - 170) < 3 && Math.abs(inkAfter.y - inkBefore.y - 110) < 3, "the ink did not move with its topic");
    // Saved with the map: simplified, relative to Examples, with its pressure.
    await schema.getByRole("button", { name: /^Save map/ }).click();
    await schema.getByRole("button", { name: "Map saved" }).waitFor();
    await page.reload();
    // The study cockpit reopens on its session section; the map stays the last one opened.
    await openMindMaps(page);
    await strokes.waitFor();
    assert.equal(await strokes.getAttribute("data-ink-node"), examplesId);
    const saved = await page.evaluate(async () => {
      const { api } = await import("/src/lib/api.ts");
      return (await api(`/schema/map?id=${localStorage.getItem("kitty.schema.last-map.study")}`)).graph.strokes;
    });
    assert.equal(saved.length, 1);
    assert.equal(saved[0].node, examplesId);
    assert.ok(saved[0].points.length >= 2 && saved[0].points.length < 10, `the stroke was not simplified (${saved[0].points.length} points)`);
    assert.ok(saved[0].points[0][0] > 0 && saved[0].points[0][0] < 20, "points are not stored relative to the topic");
    assert.ok(saved[0].points.at(-1)[2] - saved[0].points[0][2] > 0.5, "pressure was not kept");
    await capturable(page);
    // Barrel-button lasso around Key ideas and Questions selects both (Examples now sits outside the loop).
    const k = await pill("Key ideas").boundingBox(), q = await pill("Questions").boundingBox();
    const left = Math.min(k.x, q.x) - 12, right = Math.max(k.x + k.width, q.x + q.width) + 12, top = Math.min(k.y, q.y) - 6, bottom = Math.max(k.y + k.height, q.y + q.height) + 6;
    const ex = await centre(pill("Examples"));
    assert.ok(ex.x > right || ex.y > bottom, "Examples is inside the lasso area");
    await stroke(page, [{ x: left, y: top }, { x: right, y: top }, { x: right, y: bottom }, { x: left, y: bottom }, { x: left, y: top + 2 }], { button: 2, buttons: 2 });
    assert.deepEqual((await picked()).sort(), ["Key ideas", "Questions"]);
    // The eraser end rubs out the ink on Examples but never the topic itself.
    const nodes = await page.locator("svg.stage [data-node]").count(), over = await strokes.boundingBox();
    await stroke(page, [{ x: over.x + over.width / 2, y: over.y - 4 }, { x: over.x + over.width / 2, y: over.y + over.height + 4 }], { button: 5, buttons: 32 });
    assert.equal(await strokes.count(), 0, "the eraser left the ink");
    assert.equal(await page.locator("svg.stage [data-node]").count(), nodes, "the eraser removed a topic");
    await page.keyboard.press("Control+z");
    assert.equal(await strokes.count(), 1, "undo did not bring the ink back");
    // Double-tapping a topic with the pen edits it.
    const tap = await centre(pill("Questions"));
    await stroke(page, [tap]);
    await stroke(page, [tap]);
    const editor = page.getByRole("textbox", { name: "Node label" });
    await editor.waitFor();
    assert.equal(await editor.inputValue(), "Questions");
    await page.keyboard.press("Escape");
    // While the pen hovers, a palm touching Key ideas neither selects nor drags it; once the pen leaves, a finger works.
    const palm = await centre(pill("Key ideas")), still = await nodeBoxes(page);
    await pointer(page, "pointermove", palm.x + 30, palm.y, { pointerId: 7, pointerType: "pen", buttons: 0, pressure: 0 });
    await stroke(page, [palm, { x: palm.x + 80, y: palm.y + 60 }], { pointerId: 9, pointerType: "touch" });
    assert.deepEqual(await picked(), ["Questions"], "the palm changed the selection");
    assert.deepEqual(await nodeBoxes(page), still, "the palm dragged a topic");
    await pointer(page, "pointerleave", palm.x + 30, palm.y, { pointerId: 7, pointerType: "pen", buttons: 0 });
    await page.waitForTimeout(800);
    await stroke(page, [palm], { pointerId: 9, pointerType: "touch" });
    assert.deepEqual(await picked(), ["Key ideas"], "a finger no longer selects after the pen left");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

/** Serves the two reminder sources from fixtures; `calls` records snoozes and clears. */
async function mockReminders(context, { calendar = [], job = [] } = {}) {
  const state = { calendar: [...calendar], job: [...job], calls: [] };
  await context.route("**/api/calendar/reminders", (route) => route.fulfill({ json: { items: state.calendar } }));
  await context.route("**/api/calendar/reminders/dismiss", (route) => {
    const body = route.request().postDataJSON();
    state.calls.push(["dismiss", body.event_id]);
    state.calendar = state.calendar.filter((item) => item.event_id !== body.event_id);
    return route.fulfill({ json: { ok: true } });
  });
  await context.route("**/api/reminders", (route) =>
    route.request().method() === "GET" ? route.fulfill({ json: { due: state.job, upcoming: [] } }) : route.continue());
  await context.route(/\/api\/reminders\/\d+\/(snooze|complete)$/, (route) => {
    const [, id, action] = /reminders\/(\d+)\/(\w+)$/.exec(route.request().url());
    state.calls.push([action, Number(id)]);
    state.job = state.job.filter((item) => item.id !== Number(id));
    return route.fulfill({ json: action === "snooze" ? { ok: true, previous: "x", due_at: "y" } : { ok: true } });
  });
  return state;
}
const LECTURE = { event_id: "ev-lecture", occurrence: "2026-11-01T09:00:00", minutes: 15, title: "Linear algebra lecture", due: "2026-11-01T08:45:00+00:00" };
const FOLLOWUP = { id: 7001, kind: "followup", due_at: "2026-11-01T00:00:00Z", note: null, job_id: null, label: "Northwind Labs (demo) - Quant intern" };

describe("notifications", () => {
  test("reminders from every source toast bottom-right; hover fans the stack out and pauses it; Snooze and Open act", async () => {
    let state;
    const { page, problems, context } = await open("settings", {
      dock: "hidden", setup: async (context) => { state = await mockReminders(context, { calendar: [LECTURE], job: [FOLLOWUP] }); },
    });
    const stack = page.getByRole("list", { name: "New notifications" });
    const toasts = stack.getByRole("listitem");
    await page.waitForFunction(() => document.querySelectorAll('[aria-label="New notifications"] > li').length === 2);
    assert.equal(await page.locator('aside[aria-label="Calendar reminders"]').count(), 0, "the old reminders box is still shown");
    const lecture = toasts.filter({ hasText: "Linear algebra lecture" });
    const followup = toasts.filter({ hasText: "Northwind Labs (demo)" });
    assert.match(await lecture.textContent(), /Calendar/);
    assert.match(await followup.textContent(), /Job search/);
    const box = await stack.boundingBox();
    const viewport = page.viewportSize();
    // Bottom-right, stacked just above the hidden dock's pill rather than over it.
    const pill = await page.getByRole("button", { name: "Show dock" }).boundingBox();
    assert.ok(viewport.width - (box.x + box.width) < 40 && box.y + box.height <= pill.y - 8 && pill.y - (box.y + box.height) < 24,
      `stack ${JSON.stringify(box)} is not bottom-right just above the pill ${JSON.stringify(pill)}`);
    // Stacked, the toasts overlap; hovering fans them out and freezes their countdown.
    const overlap = async () => {
      const [a, b] = [await lecture.boundingBox(), await followup.boundingBox()];
      return Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
    };
    assert.ok(await overlap() > 10, "stacked toasts do not overlap");
    await followup.hover();
    await page.waitForFunction(() => {
      const [a, b] = [...document.querySelectorAll('[aria-label="New notifications"] > li')].map((li) => li.getBoundingClientRect());
      return Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) <= 0;
    });
    const countdown = () => followup.locator("[data-countdown]").getAttribute("data-countdown");
    const held = await countdown();
    await page.waitForTimeout(700);
    assert.equal(await countdown(), held, "the countdown runs while hovered");
    await followup.getByRole("button", { name: /^Snooze/ }).click();
    await followup.waitFor({ state: "detached" });
    assert.deepEqual(state.calls, [["snooze", 7001]]);
    await lecture.getByRole("button", { name: "Open" }).click();
    await page.waitForFunction(() => location.hash === "#calendar");
    await lecture.waitFor({ state: "detached" });
    // The calendar shows no second reminders box of its own.
    await page.getByRole("region", { name: "Calendar workspace" }).waitFor();
    assert.equal(await page.locator('[aria-label="Due reminders"]').count(), 0);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("toasts stack above the floating dock card, not over it, and follow it when it moves", async () => {
    const { page, problems, context } = await open("settings", { setup: (context) => mockReminders(context, { calendar: [LECTURE], job: [FOLLOWUP] }) });
    const card = page.locator('[data-kitty-panel="dock"]');
    await card.waitFor();
    await page.waitForFunction(() => document.querySelectorAll('[aria-label="New notifications"] > li').length === 2);
    const stack = page.getByRole("list", { name: "New notifications" });
    const above = async (label) => {
      const [toasts, dock] = [await stack.boundingBox(), await card.boundingBox()];
      assert.ok(toasts.y + toasts.height <= dock.y - 8 && dock.y - (toasts.y + toasts.height) < 24,
        `${label}: toasts ${JSON.stringify(toasts)} are not just above the card ${JSON.stringify(dock)}`);
    };
    await above("initial");
    const tabs = await card.getByRole("group", { name: "Dock contents" }).boundingBox();
    await page.mouse.move(tabs.x + tabs.width + 12, tabs.y + tabs.height / 2);
    await page.mouse.down();
    await page.mouse.move(tabs.x + tabs.width + 12, tabs.y + tabs.height / 2 - 60, { steps: 6 });
    await page.mouse.up();
    await above("after moving the card up");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("the bell counts unread notifications and opens a center with All/Unread, sources and mark all read", async () => {
    const { page, problems, context } = await open("settings", {
      dock: "hidden", setup: (context) => mockReminders(context, { calendar: [LECTURE], job: [FOLLOWUP] }),
    });
    const bell = page.getByRole("button", { name: "Notifications, 2 unread" });
    await bell.waitFor();
    assert.equal((await bell.textContent()).trim(), "2");
    await bell.click();
    const center = page.getByRole("dialog", { name: "Notification center" });
    await center.waitFor();
    const items = center.getByRole("listitem");
    assert.equal(await items.count(), 2);
    assert.match(await items.filter({ hasText: "Linear algebra lecture" }).textContent(), /Calendar/);
    assert.match(await items.filter({ hasText: "Northwind Labs" }).textContent(), /Job search/);
    // Sources filter the list.
    await center.getByRole("button", { name: "Job search", exact: true }).click();
    assert.equal(await items.count(), 1);
    await center.getByRole("button", { name: "All sources" }).click();
    await center.getByRole("tab", { name: /Unread/ }).click();
    assert.equal(await items.count(), 2);
    await center.getByRole("button", { name: "Mark all read" }).click();
    await page.getByRole("button", { name: "Notifications, 0 unread" }).waitFor();
    await center.getByText("You're all caught up.").waitFor();
    await center.getByRole("tab", { name: "All" }).click();
    assert.equal(await items.count(), 2);
    // Read state survives a reload.
    await page.reload();
    await page.getByRole("button", { name: "Notifications, 0 unread" }).waitFor();
    await page.keyboard.press("Escape");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("quiet hours on a 24-hour dial hold notifications, then deliver one morning digest", async () => {
    const { page, problems, context } = await open("settings", {
      dock: "hidden",
      setup: async (context) => {
        await mockReminders(context, { calendar: [LECTURE], job: [FOLLOWUP] });
        // A window that started an hour ago and ends within 15 minutes; calendar events do not ring through.
        await context.addInitScript(() => {
          const now = new Date();
          const minute = now.getHours() * 60 + now.getMinutes();
          const from = ((Math.floor((minute - 60) / 15) * 15) + 1440) % 1440;
          const to = (Math.ceil((minute + 1) / 15) * 15) % 1440;
          if (!localStorage.getItem("kitty.notify.quiet"))
            localStorage.setItem("kitty.notify.quiet", JSON.stringify({ enabled: true, from, to, letCalendar: false, digest: true }));
        });
      },
    });
    const panel = page.locator(".br-panel").filter({ has: page.locator(".br-panel__header", { hasText: /^Quiet hours$/ }) });
    await panel.waitFor();
    const until = panel.getByRole("slider", { name: "Quiet until" });
    const from = panel.getByRole("slider", { name: "Quiet from" });
    assert.match(await until.getAttribute("aria-valuetext"), /^\d\d:\d\d$/);
    assert.match(await from.getAttribute("aria-valuetext"), /^\d\d:\d\d$/);
    // Held: nothing toasts, yet the center lists both as held and unread.
    await page.getByRole("button", { name: "Notifications, 2 unread" }).waitFor();
    assert.equal(await page.locator('[aria-label="New notifications"] > li').count(), 0, "a reminder toasted during quiet hours");
    await panel.getByText("Held now: 1 calendar · 1 job search").waitFor();
    // Pull the end of the window back past now: the held reminders arrive together as the digest.
    const before = Number(await until.getAttribute("aria-valuenow"));
    await until.focus();
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("ArrowLeft");
    assert.equal(Number(await until.getAttribute("aria-valuenow")), (before - 30 + 1440) % 1440);
    const digest = page.locator('[aria-label="New notifications"] > li');
    await digest.filter({ hasText: "Morning digest" }).waitFor();
    assert.equal(await digest.count(), 1, "held reminders toasted one by one");
    assert.match(await digest.textContent(), /1 calendar · 1 job search/);
    await digest.getByRole("button", { name: "Open" }).click();
    await page.getByRole("dialog", { name: "Notification center" }).waitFor();
    // The choices are remembered.
    await panel.getByRole("switch", { name: "Let calendar events through" }).click();
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem("kitty.notify.quiet")));
    assert.equal(saved.letCalendar, true);
    assert.equal(saved.to, (before - 30 + 1440) % 1440);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("job-search report", () => {
  test("a narrative card tells the week or month from recorded data; the tables stay one click away", async () => {
    const { page, problems, context } = await open("reports", { dock: "hidden" });
    const card = page.getByRole("article", { name: "Job-search summary" });
    await card.waitFor();
    const { summary, sections } = await page.evaluate(async () => (await fetch("/api/reports")).json());
    const text = async () => (await card.textContent()).replace(/\s+/g, " ");
    // Week: the weekly total against the target, the streak, an inline sparkline and a next step.
    assert.equal(await card.getByRole("radio", { name: "Week" }).getAttribute("aria-checked"), "true");
    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
    assert.ok((await text()).includes(`You sent ${plural(summary.week.applied, "application")} this week`), await text());
    if (summary.weekly_target) assert.ok((await text()).includes(`against a target of ${summary.weekly_target}`), await text());
    if (summary.streak_days) assert.ok((await text()).includes(`${plural(summary.streak_days, "day")} in a row`), await text());
    const spark = card.getByRole("img", { name: /^Applications per week/ });
    assert.equal(await spark.locator("i").count(), summary.weeks.length);
    assert.ok((await spark.getAttribute("aria-label")).endsWith(summary.weeks.map((week) => week.applied).join(", ")));
    assert.match(await card.getByText(/^Next:/).textContent(), /^Next: \S/);
    // Month: totals for the calendar month so far.
    await card.getByRole("radio", { name: "Month" }).click();
    const month = new Date(`${summary.month.start}T12:00:00`).toLocaleString("en", { month: "long" });
    assert.ok((await text()).includes(`${plural(summary.month.applied, "application")} in ${month} so far`), await text());
    // The tables are hidden until asked for.
    const tables = page.getByRole("region", { name: /^Response rates by/ });
    assert.equal(await tables.count(), 0);
    const toggle = page.getByRole("button", { name: "Show tables" });
    await toggle.click();
    assert.equal(await tables.count(), Object.keys(sections).length);
    assert.equal(await page.getByRole("button", { name: "Hide tables" }).getAttribute("aria-expanded"), "true");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("Google calendar conflicts", () => {
  test("both versions sit on the day grid, kitty's solid and Google's dashed; picking one strikes out what is lost", async () => {
    const event = (fields) => ({ title: "Linear algebra lecture", start: "2026-11-02T09:00:00+00:00", end: "2026-11-02T10:30:00+00:00", all_day: false,
      timezone: "UTC", location: "Hall B", description: "Bring problem set 6", reminders: [15], rrule: "", ...fields });
    const resolved = [];
    const { page, problems, context } = await open("calendar", {
      dock: "hidden",
      setup: async (context) => {
        await context.route("**/api/bootstrap", async (route) => {
          const response = await route.fetch();
          await route.fulfill({ response, json: { ...(await response.json()), demo: false } });
        });
        await context.route("**/api/calendar/google/conflicts", (route) => route.fulfill({ json: { items: [{
          id: 41, local_revision: 7, remote_json: { summary: "raw" },
          local: event({ revision: 7, deleted: false }),
          remote: event({ title: "Linear Algebra — Lecture 14", start: "2026-11-02T09:15:00+00:00", end: "2026-11-02T10:45:00+00:00", location: "Room 204" }),
        }] } }));
        await context.route("**/api/calendar/google/resolve", (route) => {
          resolved.push(route.request().postDataJSON());
          return route.fulfill({ json: { task: { id: "t-41", kind: "calendar-google", status: "completed", started_at: "", finished_at: "", result: { ok: true }, error: null } } });
        });
      },
    });
    await page.getByRole("button", { name: "Imports & Google" }).click();
    const conflict = page.getByRole("article", { name: "Conflict: Linear algebra lecture" });
    await conflict.waitFor();
    assert.equal(await page.locator("pre").filter({ hasText: '"summary": "raw"' }).count(), 0, "raw JSON is still shown");
    const mine = conflict.getByRole("button", { name: /^kitty's version/ });
    const google = conflict.getByRole("button", { name: /^Google's version/ });
    const style = (locator) => locator.evaluate((node) => getComputedStyle(node).borderTopStyle);
    assert.equal(await style(mine), "solid");
    assert.equal(await style(google), "dashed");
    // Google's block starts a quarter hour later, so it sits lower on the grid.
    assert.ok((await google.boundingBox()).y > (await mine.boundingBox()).y);
    const changes = conflict.getByRole("list", { name: "What changes" });
    assert.deepEqual(await changes.locator("li > span").allTextContents(), ["Title", "Time", "Where"]);
    const keep = conflict.getByRole("button", { name: "Pick a block" });
    assert.equal(await keep.isDisabled(), true);
    await google.click();
    assert.equal(await google.getAttribute("aria-pressed"), "true");
    // Choosing Google's version loses kitty's values.
    assert.equal(await changes.locator('[data-side="local"].lost').count(), 3);
    assert.equal(await changes.locator('[data-side="remote"].lost').count(), 0);
    assert.equal(await changes.locator('[data-side="local"]').first().evaluate((node) => getComputedStyle(node).textDecorationLine), "line-through");
    await mine.click();
    assert.equal(await changes.locator('[data-side="remote"].lost').count(), 3);
    await google.click();
    await conflict.getByRole("button", { name: "Use Google's" }).click();
    await page.waitForFunction(() => true);
    assert.deepEqual(resolved, [{ id: 41, revision: 7, choice: "remote", confirmed: true }]);
    assert.deepEqual(problems, []);
    await context.close();
  });
});
