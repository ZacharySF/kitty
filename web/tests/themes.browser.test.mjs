// The theme engine (design Q11) in a real browser: every bundled theme on one surface of
// shared controls (tests/themeHarness.js; synthetic content only). Per theme it checks AA
// contrast of text and of selected and pressed controls over the theme's brightest backdrop
// pixel, that the theme's surface style really applies, and the focus ornament; then that
// Reduce motion (setting and OS) stops theme motion and that switching themes swaps the
// motif layer without a reload.
// Run from web/ with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/themes.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";
import { THEMES } from "../src/lib/themes/catalog.ts";

const root = fileURLToPath(new URL("..", import.meta.url));
const VIEWPORT = { width: 1100, height: 760 };
/** Themes that ship a motif module (lib/themes/<id>/). */
const MODULES = ["kitty", "liquid-glass", "scythe"];
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

/** Opens the harness on `theme` (saved before the first load, as a returning user). */
async function harness(theme, { reducedMotion = "no-preference", appearance } = {}) {
  const context = await browser.newContext({ viewport: VIEWPORT, reducedMotion });
  await context.addInitScript(
    ([id, look]) => {
      localStorage.setItem("kitty.theme", id);
      if (look) localStorage.setItem("kitty.appearance", JSON.stringify(look));
    },
    [theme, appearance],
  );
  const page = await context.newPage();
  page.setDefaultTimeout(10_000);
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("tests/theme-harness.html", base).href, { timeout: 60_000 });
  await page.waitForFunction(() => !!window.themeHarness, null, { timeout: 60_000 });
  await settled(page, theme);
  return { page, problems, context };
}

/** Waits until `id` is applied and its motif module (if any) has loaded. */
async function settled(page, id) {
  await page.waitForFunction(
    ([theme, hasModule]) =>
      document.documentElement.dataset.theme === theme &&
      (hasModule ? document.documentElement.dataset.themeMotif === theme : !document.documentElement.dataset.themeMotif),
    [id, MODULES.includes(id)],
  );
  // Fonts and the lazily loaded motif stylesheet settle before measuring.
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(150);
}

async function switchTheme(page, id) {
  await page.evaluate((theme) => window.themeHarness.setTheme(theme), id);
  await settled(page, id);
}

/**
 * The brightest and darkest pixels of the backdrop alone (art included): the specimen is
 * hidden, the page screenshotted, and the image scanned in the page.
 */
async function backdropExtremes(page) {
  // Opacity, not visibility: Bracket's buttons pin an inline `visibility: visible` on their labels.
  await page.evaluate(() => (document.getElementById("specimen").style.opacity = "0"));
  const shot = (await page.screenshot()).toString("base64");
  await page.evaluate(() => (document.getElementById("specimen").style.opacity = ""));
  return page.evaluate(async (data) => {
    const image = new Image();
    image.src = `data:image/png;base64,${data}`;
    await image.decode();
    const canvas = Object.assign(document.createElement("canvas"), { width: image.width, height: image.height });
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0);
    const { data: pixels } = context.getImageData(0, 0, image.width, image.height);
    const lin = (value) => ((value /= 255) <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    let bright = { l: -1 }, dark = { l: 2 };
    for (let index = 0; index < pixels.length; index += 4) {
      const [r, g, b] = [pixels[index], pixels[index + 1], pixels[index + 2]];
      const l = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
      if (l > bright.l) bright = { l, rgb: [r, g, b] };
      if (l < dark.l) dark = { l, rgb: [r, g, b] };
    }
    return [bright.rgb, dark.rgb];
  }, shot);
}

/**
 * Worst-case contrast of each `[data-sample]` (and Bracket's pressed toggle) over each
 * backdrop extreme: every background layer between the element and <body> is composited
 * (colours, and for gradients the stop that is worst for the text).
 */
function sampleContrasts(page, backdrops) {
  return page.evaluate((grounds) => {
    const probe = document.createElement("span");
    document.body.append(probe);
    /** Any computed colour → [r, g, b, a] in 0–255 / 0–1, via the canvas's own parser. */
    const canvas = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
    const rgba = (css) => {
      canvas.clearRect(0, 0, 1, 1);
      canvas.fillStyle = "#000";
      canvas.fillStyle = css;
      canvas.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = canvas.getImageData(0, 0, 1, 1).data;
      return [r, g, b, a / 255];
    };
    const over = (top, bottom) => top.slice(0, 3).map((value, index) => value * top[3] + bottom[index] * (1 - top[3]));
    const lin = (value) => ((value /= 255) <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    const lum = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    const ratio = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
    const COLOUR = /(?:rgba?|color|hsla?|oklch|oklab|lab|lch)\([^()]*(?:\([^()]*\)[^()]*)*\)|#[0-9a-f]{3,8}\b|transparent/gi;
    const samples = [...document.querySelectorAll("[data-sample]"), document.querySelector('.toggle-group .br-btn[aria-pressed="true"]')];
    probe.remove();
    const results = [];
    for (const element of samples) {
      const label = element.dataset.sample ?? "pressed segment";
      const chain = [];
      for (let node = element; node && node !== document.body; node = node.parentElement) chain.unshift(getComputedStyle(node));
      const text = rgba(getComputedStyle(element).color);
      let worst = Infinity;
      for (const ground of grounds) {
        let bg = ground;
        for (const style of chain) {
          bg = over(rgba(style.backgroundColor), bg);
          // Gradient layers, painted bottom-up (CSS lists the top layer first).
          for (const layer of style.backgroundImage.split(/,(?![^()]*\))/).reverse()) {
            if (!layer.includes("gradient")) continue;
            const stops = layer.match(COLOUR) ?? [];
            if (!stops.length) continue;
            const candidates = stops.map((stop) => over(rgba(stop), bg));
            bg = candidates.reduce((pick, candidate) => (ratio(over(text, candidate), candidate) < ratio(over(text, pick), pick) ? candidate : pick));
          }
        }
        worst = Math.min(worst, ratio(over(text, bg), bg));
      }
      results.push({ label, ratio: Math.round(worst * 100) / 100 });
    }
    return results;
  }, backdrops);
}

describe("every theme keeps text and selections readable and owns its surfaces", () => {
  for (const theme of THEMES) {
    test(`${theme.label}: AA text, selected and pressed controls over its brightest backdrop`, async () => {
      const { page, problems, context } = await harness(theme.id);
      const grounds = await backdropExtremes(page);
      const results = await sampleContrasts(page, grounds);
      assert.ok(results.length >= 10, `only ${results.length} samples`);
      const failing = results.filter((result) => result.ratio < 4.5);
      assert.deepEqual(failing, [], `${theme.label}: below 4.5:1 over backdrop ${JSON.stringify(grounds)}`);
      assert.deepEqual(problems, []);
      await context.close();
    });

    test(`${theme.label}: its ${theme.surface.style} surface, ${theme.focus} focus and ${theme.selection} selection apply`, async () => {
      const { page, problems, context } = await harness(theme.id);
      const html = await page.evaluate(() => ({ ...document.documentElement.dataset }));
      assert.equal(html.themeSurface, theme.surface.style);
      assert.equal(html.themeFocus, theme.focus);
      assert.equal(html.themeSelection, theme.selection);
      const surface = await page.locator("[data-surface-sample]").evaluate((panel) => {
        const style = getComputedStyle(panel);
        return {
          blur: style.backdropFilter,
          background: style.backgroundColor,
          image: style.backgroundImage,
          border: style.borderTopColor,
          radius: parseFloat(style.borderTopLeftRadius),
          shadow: style.boxShadow,
        };
      });
      const alpha = (colour) => (colour.startsWith("rgba") ? parseFloat(colour.split(",")[3]) : colour === "transparent" ? 0 : 1);
      assert.equal(surface.radius, theme.surface.radius, `${theme.label} panel radius`);
      if (theme.surface.style === "glass") {
        assert.match(surface.blur, /blur\(/, "glass is frosted");
        assert.ok(alpha(surface.background) < 1 || /gradient/.test(surface.image), "glass is see-through");
      } else {
        assert.equal(surface.blur, "none", `${theme.surface.style} surfaces are not frosted`);
      }
      if (theme.surface.style === "paper") {
        assert.match(surface.image, /url\(/, "paper has grain");
        assert.equal(alpha(surface.border), 0, "paper has no border");
        assert.notEqual(surface.shadow, "none", "paper lifts on a soft shadow");
      }
      if (theme.surface.style === "flat") assert.equal(alpha(surface.background), 1, "flat surfaces are opaque");
      if (theme.surface.style === "frame") {
        assert.ok(surface.radius <= 3, "frames have square corners");
        assert.match(surface.shadow, /inset/, "frames draw a second hairline inside");
      }
      if (theme.surface.style === "glass") assert.match(surface.shadow, /inset/, "glass has a lit inner edge");

      // Keyboard focus: at least a 2px ring, plus the theme's ornament.
      await page.locator("[data-focus-target]").focus();
      await page.keyboard.press("Shift+Tab");
      await page.keyboard.press("Tab");
      const focus = await page.evaluate(() => {
        const active = document.activeElement;
        const style = getComputedStyle(active);
        const overlay = document.querySelector(".theme-focus");
        const box = active.getBoundingClientRect();
        const ring = overlay?.getBoundingClientRect();
        return {
          outline: parseFloat(style.outlineWidth) * (style.outlineStyle === "none" ? 0 : 1),
          overlay: overlay && !overlay.hidden ? overlay.dataset.focusStyle : null,
          around: ring ? ring.left <= box.left && ring.right >= box.right && ring.top <= box.top && ring.bottom >= box.bottom : false,
          before: overlay ? getComputedStyle(overlay, "::before").content : "none",
        };
      });
      assert.ok(focus.outline >= 2, `${theme.label}: focus ring ${focus.outline}px`);
      if (["petal", "bracket", "glow"].includes(theme.focus)) {
        assert.equal(focus.overlay, theme.focus, `${theme.label}: the ${theme.focus} focus ornament shows`);
        assert.ok(focus.around, "the ornament surrounds the focused control");
        assert.notEqual(focus.before, "none");
      } else assert.equal(focus.overlay, null, "the plain ring needs no ornament");
      assert.deepEqual(problems, []);
      await context.close();
    });
  }
});

describe("theme motion yields to Reduce motion", () => {
  /** Motion probes: the motion token, a fresh bloom on the tick, the art's ambient animation. */
  async function motion(page) {
    await page.locator("[data-tick]").uncheck();
    await page.locator("[data-tick]").check();
    return page.evaluate(() => {
      const root = getComputedStyle(document.documentElement);
      const animated = [...document.querySelectorAll(".theme-art, .theme-art *, [data-tick]")]
        .flatMap((node) => [getComputedStyle(node), getComputedStyle(node, "::before"), getComputedStyle(node, "::after")])
        .concat([getComputedStyle(document.body, "::before")])
        .filter((style) => style.animationName !== "none" && parseFloat(style.animationDuration) > 0).length;
      const button = getComputedStyle(document.querySelector("[data-focus-target]"));
      return { ms: root.getPropertyValue("--motion-ms").trim(), animated, transition: button.transitionDuration };
    });
  }

  for (const id of ["kitty", "liquid-glass", "scythe"]) {
    test(`${id}: ambient and transition motion stop under the setting and the OS preference`, async () => {
      const theme = THEMES.find((entry) => entry.id === id);
      const expected = { calm: "320ms", fluid: "240ms", snappy: "120ms" }[theme.motion];
      const live = await harness(id);
      const moving = await motion(live.page);
      assert.equal(moving.ms, expected);
      assert.ok(moving.animated > 0, `${id} has no theme motion to stop`);
      await live.context.close();

      for (const [name, options] of [
        ["Settings → Reduce motion", { appearance: { motion: "reduced" } }],
        ["prefers-reduced-motion", { reducedMotion: "reduce" }],
      ]) {
        const { page, problems, context } = await harness(id, options);
        const still = await motion(page);
        assert.equal(still.ms, "0ms", `${name}: motion token`);
        assert.equal(still.animated, 0, `${name}: animations still running`);
        // Bracket's own reduced-motion rule leaves 1ms, which is no visible motion.
        assert.ok(still.transition.split(",").every((value) => parseFloat(value) <= 0.001), `${name}: transitions ${still.transition}`);
        assert.deepEqual(problems, []);
        await context.close();
      }
    });
  }
});

describe("switching themes swaps the motif layer without a reload", () => {
  test("Kitty → Liquid Glass → Harness → Kitty", async () => {
    const { page, problems, context } = await harness("kitty");
    await page.evaluate(() => (window.__sameDocument = true));
    const motif = () =>
      page.evaluate(() => {
        const divider = getComputedStyle(document.querySelector("hr.rule"), "::before");
        const tick = getComputedStyle(document.querySelector("[data-tick]"));
        return {
          art: document.querySelector(".theme-art")?.dataset.themeArt ?? null,
          motif: document.documentElement.dataset.themeMotif ?? null,
          divider: divider.content === "none" ? "none" : `${divider.backgroundImage} ${divider.maskImage ?? divider.webkitMaskImage}`,
          tick: `${tick.backgroundImage} ${tick.maskImage ?? ""}`,
          empty: document.querySelector(".kitty-empty [data-empty-art]")?.dataset.emptyArt ?? null,
          loader: document.querySelector(".kitty-loader [data-loader-art]")?.dataset.loaderArt ?? null,
        };
      });

    const kitty = await motif();
    assert.equal(kitty.art, "kitty", "blossom backdrop");
    assert.equal(kitty.motif, "kitty");
    assert.match(kitty.divider, /url\(/, "petal divider");
    assert.match(kitty.tick, /url\(/, "blossom tick");
    assert.equal(kitty.empty, "kitty");
    assert.equal(kitty.loader, "kitty");

    await switchTheme(page, "liquid-glass");
    const glass = await motif();
    assert.equal(glass.art, "liquid-glass");
    assert.doesNotMatch(glass.divider, /url\(/, "no petals on glass");
    assert.doesNotMatch(glass.tick, /url\(/, "no blossom tick on glass");
    assert.equal(glass.empty, "liquid-glass");

    await switchTheme(page, "harness");
    const plain = await motif();
    assert.deepEqual([plain.art, plain.motif, plain.empty, plain.loader], [null, null, "default", "default"], "Harness has no motif module");
    assert.doesNotMatch(plain.divider, /url\(/);

    await switchTheme(page, "kitty");
    const back = await motif();
    assert.equal(back.art, "kitty");
    assert.match(back.divider, /url\(/);
    assert.equal(back.empty, "kitty");

    assert.equal(await page.evaluate(() => window.__sameDocument), true, "the page did not reload");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Appearance → Theme motif off hides Kitty's marks but keeps its glass", async () => {
    const { page, problems, context } = await harness("kitty", { appearance: { motif: false } });
    const state = await page.evaluate(() => ({
      divider: getComputedStyle(document.querySelector("hr.rule"), "::before").content,
      tick: getComputedStyle(document.querySelector("[data-tick]")).backgroundImage,
      blur: getComputedStyle(document.querySelector("[data-surface-sample]")).backdropFilter,
    }));
    assert.equal(state.divider, "none");
    assert.doesNotMatch(state.tick, /url\(/);
    assert.match(state.blur, /blur\(/);
    assert.deepEqual(problems, []);
    await context.close();
  });
});
