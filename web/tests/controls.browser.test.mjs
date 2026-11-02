// rika!'s shared controls (design Q1–Q8) in a real browser on synthetic data
// (tests/controls-harness.html): one control height and radius per control type,
// the Inter sentence-case label voice, one surface per region with hairline sections,
// the status dot, the themed Select, the ⋯ menu, anchored popovers, quick-add and the
// edit drawer. Styles are asserted as computed values, never as source text.
// Run from web/ with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/controls.browser.test.mjs
import { after, before, describe, test } from "node:test";
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

async function harness(theme = "kitty") {
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 } });
  await context.addInitScript((id) => localStorage.setItem("kitty.theme", id), theme);
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("tests/controls-harness.html", base).href, { timeout: 60000 });
  await page.waitForFunction(() => window.controlsReady, null, { timeout: 60000 });
  await page.evaluate(() => document.fonts.ready);
  return { page, problems, context };
}
const log = (page) => page.evaluate(() => window.controlsLog);
const box = (locator) => locator.evaluate((node) => node.getBoundingClientRect().toJSON());
const style = (locator, ...keys) =>
  locator.evaluate((node, keys) => {
    const computed = getComputedStyle(node);
    return Object.fromEntries(keys.map((key) => [key, computed[key]]));
  }, keys);
const family = (value) => value.split(",")[0].replace(/["']/g, "").trim();

for (const theme of ["kitty", "liquid-glass"]) {
  describe(`shared control styles (${theme})`, () => {
    test("controls are 28px tall, primary page actions 32px, with one radius per control type", async () => {
      const { page, problems, context } = await harness(theme);
      const metrics = await page.evaluate(() => {
        const row = document.querySelector('[aria-label="Controls"]');
        return [...row.children].map((node) => {
          const s = getComputedStyle(node);
          return { label: node.getAttribute("aria-label") || node.getAttribute("placeholder") || node.textContent.trim(), tag: node.tagName,
            className: node.className, size: node.dataset.size ?? "", height: node.getBoundingClientRect().height, radius: parseFloat(s.borderTopLeftRadius) };
        });
      });
      for (const control of metrics) {
        const primary = control.size === "lg" || /page-action/.test(control.className);
        assert.equal(control.height, primary ? 32 : 28, `${control.label} is ${control.height}px`);
        if (control.tag === "INPUT") assert.equal(control.radius, 8, `${control.label} field radius`);
        else assert.ok(control.radius >= control.height / 2, `${control.label} is not a pill (radius ${control.radius})`);
      }
      // Select triggers and segmented controls share the same line.
      for (const name of ["Deck", "Notebook", "Time zone"]) {
        const trigger = page.getByRole("combobox", { name });
        assert.equal((await box(trigger)).height, 28, `${name} trigger`);
        assert.equal(parseFloat((await style(trigger, "borderTopLeftRadius")).borderTopLeftRadius), 8, `${name} field radius`);
      }
      const segments = page.getByRole("group", { name: "Study mode" });
      assert.equal((await box(segments)).height, 28);
      for (const button of await segments.getByRole("button").all()) assert.ok((await box(button)).height <= 28);
      assert.equal((await box(page.getByRole("button", { name: "More actions" }))).height, 28);
      assert.equal(await page.locator("select").count(), 0, "a native select is rendered");
      assert.deepEqual(problems, []);
      await context.close();
    });

    test("labels, eyebrows, section titles, field labels and fold-outs are Inter 600 sentence case, never italic", async () => {
      const { page, problems, context } = await harness(theme);
      for (const selector of [".eyebrow", ".br-section-title", ".br-field__label", "summary"]) {
        const s = await style(page.locator(selector).first(), "fontFamily", "fontWeight", "fontStyle", "textTransform", "fontSize", "letterSpacing");
        assert.equal(family(s.fontFamily), "Inter Variable", `${selector} face`);
        assert.equal(s.fontWeight, "600", `${selector} weight`);
        assert.equal(s.fontStyle, "normal", `${selector} is italic`);
        assert.equal(s.textTransform, "none", `${selector} case`);
        assert.equal(s.fontSize, "12px", `${selector} size`);
      }
      // Fraunces is the page title's face only; every other heading is Inter.
      assert.equal(family((await style(page.locator("h1"), "fontFamily")).fontFamily), "Fraunces Variable");
      assert.equal(family((await style(page.locator("h2"), "fontFamily")).fontFamily), "Inter Variable");
      const loaded = await page.evaluate(() => [...document.fonts].map((face) => face.family.replace(/["']/g, "")));
      for (const gone of ["Newsreader Variable", "Space Grotesk Variable"]) assert.ok(!loaded.includes(gone), `${gone} is still bundled`);
      assert.deepEqual(problems, []);
      await context.close();
    });

    test("one surface per region: hairline sections inside, nested panels unboxed, fold-outs unboxed", async () => {
      const { page, problems, context } = await harness(theme);
      const region = await style(page.locator(".surface"), "borderTopWidth", "borderTopLeftRadius");
      assert.equal(region.borderTopWidth, "1px");
      assert.ok(parseFloat(region.borderTopLeftRadius) >= 12);
      const second = await style(page.locator(".surface-section").nth(1), "borderTopWidth", "borderTopStyle");
      assert.deepEqual(second, { borderTopWidth: "1px", borderTopStyle: "solid" }, "sections are not split by a hairline");
      const first = await style(page.locator(".surface-section").first(), "borderTopWidth");
      assert.equal(first.borderTopWidth, "0px");
      const nested = await style(page.locator(".panel.nested"), "borderTopWidth", "borderLeftWidth", "backdropFilter", "backgroundColor");
      assert.deepEqual([nested.borderTopWidth, nested.borderLeftWidth], ["0px", "0px"], "a panel inside a surface is boxed");
      assert.equal(nested.backdropFilter, "none");
      assert.equal(nested.backgroundColor, "rgba(0, 0, 0, 0)");
      const summary = await style(page.locator("summary"), "borderTopWidth", "backgroundColor");
      assert.equal(summary.borderTopWidth, "0px", "the fold-out is a boxed well");
      assert.equal(summary.backgroundColor, "rgba(0, 0, 0, 0)");
      assert.deepEqual(problems, []);
      await context.close();
    });

    test("status dots: 6px, neutral when saved, warning when unsaved, danger when failed, with a tooltip", async () => {
      const { page, problems, context } = await harness(theme);
      const tokens = await page.evaluate(() => {
        const probe = document.createElement("i");
        document.body.append(probe);
        const read = (css) => { probe.style.color = css; return getComputedStyle(probe).color; };
        const result = { saved: read("var(--subtle)"), unsaved: read("var(--warning)"), failed: read("var(--error)") };
        probe.remove();
        return result;
      });
      for (const [state, name, tip] of [["saved", "Saved", "Saved"], ["unsaved", "Unsaved changes", "Unsaved changes"], ["failed", "Couldn't save: offline", "Couldn't save: offline"]]) {
        const dot = page.getByRole("img", { name, exact: true });
        const rect = await box(dot);
        assert.deepEqual([rect.width, rect.height], [6, 6], `${state} dot size`);
        assert.equal((await style(dot, "backgroundColor")).backgroundColor, tokens[state], `${state} colour`);
        assert.equal(await dot.getAttribute("title"), tip);
      }
      assert.deepEqual(problems, []);
      await context.close();
    });
  });
}

describe("Select", () => {
  test("four or fewer choices are segments: one click switches, arrows move focus", async () => {
    const { page, problems, context } = await harness();
    const group = page.getByRole("group", { name: "Study mode" });
    await group.getByRole("button", { name: "Practice" }).click();
    assert.equal(await group.getByRole("button", { name: "Practice" }).getAttribute("aria-pressed"), "true");
    assert.equal(await group.getByRole("button", { name: "Scheduled" }).getAttribute("aria-pressed"), "false");
    // Choosing the current segment again keeps it chosen.
    await group.getByRole("button", { name: "Practice" }).click();
    assert.equal(await group.getByRole("button", { name: "Practice" }).getAttribute("aria-pressed"), "true");
    assert.deepEqual(await log(page), [["mode", "practice"]]);
    assert.match(await page.getByTestId("values").textContent(), /^practice\|/);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("longer lists open the themed listbox: check on the current item, keyboard and type-ahead, Esc returns focus", async () => {
    const { page, problems, context } = await harness();
    const trigger = page.getByRole("combobox", { name: "Deck" });
    assert.match(await trigger.textContent(), /Biology/);
    await trigger.click();
    const list = page.getByRole("listbox", { name: "Deck" });
    await list.waitFor();
    const options = list.getByRole("option");
    assert.equal(await options.count(), 7);
    assert.equal(await list.getByRole("option", { name: /Biology/ }).getAttribute("aria-selected"), "true");
    assert.equal(await list.getByRole("option", { name: /Unassigned/ }).getAttribute("aria-disabled"), "true");
    // The open list is the app's overlay glass, not the OS menu: one themed box with a radius.
    const glass = page.locator("[popover]").filter({ has: list });
    const panel = await style(glass, "borderTopLeftRadius", "backdropFilter", "fontFamily");
    assert.equal(family(panel.fontFamily), "Inter Variable");
    assert.ok(parseFloat(panel.borderTopLeftRadius) >= 10);
    assert.notEqual(panel.backdropFilter, "none");
    const inner = await style(list, "borderTopWidth", "backgroundColor");
    assert.deepEqual(inner, { borderTopWidth: "0px", backgroundColor: "rgba(0, 0, 0, 0)" }, "the list draws a second box inside the popover");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await list.waitFor({ state: "detached" });
    assert.match(await trigger.textContent(), /Organic chemistry/);
    assert.equal(await trigger.evaluate((node) => node === document.activeElement), true, "focus did not return to the trigger");
    // Type-ahead from the closed trigger.
    await page.keyboard.press("ArrowDown");
    await list.waitFor();
    await page.keyboard.type("ka");
    await page.keyboard.press("Enter");
    assert.match(await trigger.textContent(), /Kanji/);
    // Escape closes without choosing; a click outside closes too.
    await trigger.click();
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Escape");
    await list.waitFor({ state: "detached" });
    assert.match(await trigger.textContent(), /Kanji/);
    assert.equal(await trigger.evaluate((node) => node === document.activeElement), true);
    await trigger.click();
    await list.waitFor();
    await page.mouse.click(1200, 820);
    await list.waitFor({ state: "detached" });
    assert.deepEqual(await log(page), [["deck", "chem"], ["deck", "kanji"]]);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("the searchable picker filters as you type, picks with Enter and offers to create from the query", async () => {
    const { page, problems, context } = await harness();
    const trigger = page.getByRole("combobox", { name: "Notebook" });
    assert.match(await trigger.textContent(), /Choose notebook/);
    await trigger.click();
    const search = page.getByRole("searchbox", { name: "Search Notebook" });
    await search.waitFor();
    assert.equal(await search.evaluate((node) => node === document.activeElement), true, "search is not focused");
    await page.keyboard.type("ha");
    const list = page.getByRole("listbox", { name: "Notebook" });
    assert.deepEqual(await list.getByRole("option").allTextContents().then((all) => all.map((text) => text.trim())), ["Hashing4", "Alphabet", "+New notebook “ha”"]);
    await page.keyboard.press("Enter");
    assert.match(await trigger.textContent(), /Hashing/);
    await trigger.click();
    await page.keyboard.type("zz");
    await page.keyboard.press("Enter");
    assert.deepEqual((await log(page)).at(-1), ["create", "zz"]);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("time zones are a searchable picker with offsets", async () => {
    const { page, problems, context } = await harness();
    const trigger = page.getByRole("combobox", { name: "Time zone" });
    await trigger.click();
    await page.keyboard.type("tokyo");
    const option = page.getByRole("listbox", { name: "Time zone" }).getByRole("option", { name: /Asia\/Tokyo/ });
    assert.match(await option.textContent(), /GMT\+9/);
    await option.click();
    assert.match(await page.getByTestId("values").textContent(), /\|Asia\/Tokyo$/);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("overflow menu and popover", () => {
  test("⋯ is a keyboard menu that escapes a clipping ancestor and returns focus", async () => {
    const { page, problems, context } = await harness();
    const trigger = page.getByRole("button", { name: "More actions" });
    assert.equal(await trigger.getAttribute("aria-haspopup"), "menu");
    await trigger.click();
    const menu = page.getByRole("menu", { name: "More actions" });
    await menu.waitFor();
    const [anchor, panel] = [await box(trigger), await box(menu)];
    assert.ok(panel.top >= anchor.bottom, "menu is not below its trigger");
    assert.ok(panel.height > 100, "menu is clipped by its container");
    assert.equal(await menu.getByRole("menuitem", { name: "Import…" }).evaluate((node) => node === document.activeElement), true);
    for (const item of await menu.getByRole("menuitem").all()) assert.equal((await box(item)).height, 28);
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowDown"); // skips the disabled item
    assert.equal(await menu.getByRole("menuitem", { name: "Delete" }).evaluate((node) => node === document.activeElement), true);
    await page.keyboard.press("Home");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await menu.waitFor({ state: "detached" });
    assert.deepEqual(await log(page), [["menu", "export"]]);
    assert.equal(await trigger.evaluate((node) => node === document.activeElement), true);
    await trigger.press("Enter");
    await menu.waitFor();
    await page.keyboard.press("Escape");
    await menu.waitFor({ state: "detached" });
    assert.equal(await trigger.evaluate((node) => node === document.activeElement), true);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("a popover anchors to its control, focuses its first field and stays open while a nested picker is used", async () => {
    const { page, problems, context } = await harness();
    const trigger = page.getByRole("button", { name: "Reminder" });
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "Reminder" });
    await dialog.waitFor();
    const [anchor, panel] = [await box(trigger), await box(dialog)];
    assert.ok(Math.abs(panel.left - anchor.left) < 2 && panel.top >= anchor.bottom, "popover is not anchored below-start");
    assert.equal(await dialog.getByPlaceholder("tomorrow 9am").evaluate((node) => node === document.activeElement), true);
    await dialog.getByRole("combobox", { name: "Repeat" }).click();
    await page.getByRole("listbox", { name: "Repeat" }).getByRole("option", { name: "Weekly" }).click();
    assert.equal(await dialog.isVisible(), true, "choosing inside a nested picker closed the popover");
    assert.match(await dialog.getByRole("combobox", { name: "Repeat" }).textContent(), /Weekly/);
    await page.mouse.click(1200, 820);
    await dialog.waitFor({ state: "detached" });
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("adding and editing", () => {
  test("quick-add: a 32px + action opens one phrase field and up to two chips; Enter saves, Esc cancels", async () => {
    const { page, problems, context } = await harness();
    const trigger = page.getByRole("button", { name: "Add exam" });
    assert.equal((await box(trigger)).height, 32);
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "Add exam" });
    await dialog.waitFor();
    const field = dialog.getByRole("textbox", { name: "Add exam" });
    assert.equal(await field.evaluate((node) => node === document.activeElement), true);
    assert.equal(await dialog.getByRole("textbox").count(), 1, "more than one text field");
    assert.ok(await dialog.locator(".quick-chips > *").count() <= 2, "more than two essentials");
    await page.keyboard.press("Enter");
    assert.equal(await dialog.isVisible(), true, "an empty phrase saved");
    await page.keyboard.type("Orgo quiz Fri 9am");
    await page.keyboard.press("Enter");
    await dialog.waitFor({ state: "detached" });
    assert.deepEqual(await log(page), [["save", "Orgo quiz Fri 9am"]]);
    await trigger.click();
    assert.equal(await dialog.getByRole("textbox", { name: "Add exam" }).inputValue(), "", "the phrase was not cleared after saving");
    await page.keyboard.type("draft");
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "detached" });
    assert.equal(await trigger.evaluate((node) => node === document.activeElement), true);
    assert.equal((await log(page)).length, 1, "Escape saved");
    await trigger.click();
    await dialog.getByRole("button", { name: "More" }).click();
    assert.deepEqual((await log(page)).at(-1), ["more", "draft"]);
    await page.getByRole("dialog", { name: "Orgo quiz" }).waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("edit drawer: a non-modal side surface with hairline header and footer; Esc closes and returns focus", async () => {
    const { page, problems, context } = await harness();
    const opener = page.getByRole("button", { name: "Open exam" });
    await opener.click();
    const drawer = page.getByRole("dialog", { name: "Orgo quiz" });
    await drawer.waitFor();
    assert.equal(await drawer.getAttribute("aria-modal"), "false");
    assert.equal(await drawer.getByRole("textbox").first().evaluate((node) => node === document.activeElement), true);
    const rect = await box(drawer);
    assert.ok(rect.right > 1260 && rect.width <= 420, "drawer is not on the right edge");
    assert.equal((await style(drawer.locator("header"), "borderBottomWidth")).borderBottomWidth, "1px");
    assert.equal((await style(drawer.locator("footer"), "borderTopWidth")).borderTopWidth, "1px");
    assert.equal(family((await style(drawer.getByRole("heading", { name: "Orgo quiz" }), "fontFamily")).fontFamily), "Inter Variable");
    await page.keyboard.press("Escape");
    await drawer.waitFor({ state: "detached" });
    assert.equal(await opener.evaluate((node) => node === document.activeElement), true);
    assert.deepEqual(problems, []);
    await context.close();
  });
});
