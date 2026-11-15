// Forms without boxes, colour-coded widgets, and the new Kitty theme.
import { chromium } from "../../web/node_modules/playwright-core/index.mjs";
import { mkdirSync, writeFileSync } from "node:fs";
const out = process.argv[3] ?? "/tmp/design-labs/kitty-theme";
mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5 });
const errors = []; page.on("pageerror", (e) => errors.push(e.message));
const shots = []; let n = 0;
const shot = async (title, section) => { n += 1; const file = `${String(n).padStart(2, "0")}-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.png`; await page.screenshot({ path: `${out}/${file}` }); shots.push({ file, title, section }); };
const go = async (hash, wait = 2600) => { await page.evaluate((h) => { location.hash = h; }, hash); await page.waitForTimeout(wait); };
const attempt = async (label, run) => { try { await run(); } catch (e) { console.log("skipped", label, String(e.message).split("\n")[0].slice(0, 200)); } };
await page.goto(process.argv[2], { waitUntil: "load" });
await page.waitForTimeout(4500);
const skip = page.getByRole("button", { name: /Skip/ });
if (await skip.count()) { await skip.first().click(); await page.waitForTimeout(2500); }

// Colour-coded widgets (default theme).
await go("today", 3200);
await attempt("offer", async () => { const b = page.getByRole("region", { name: "Home template" }).getByRole("button", { name: "Not now" }); if (await b.count()) await b.click(); });
await attempt("tones", async () => {
  await page.getByRole("button", { name: "Edit", exact: true }).click({ timeout: 8000 }); await page.waitForTimeout(600);
  const colours = page.getByRole("group", { name: "Colours" });
  const slots = page.locator(".board .slot");
  for (const [tone, index] of [["Colour 2", 0], ["Colour 4", 2], ["Colour 1", 1], ["Colour 5", 3]]) {
    await colours.getByRole("button", { name: tone, exact: true }).click();
    await slots.nth(index).click({ position: { x: 40, y: 60 } });
    await colours.getByRole("button", { name: tone, exact: true }).click();
  }
  await page.waitForTimeout(400);
  await shot("Edit: drop a colour on any widget", "Colour-coded widgets");
  await page.getByRole("complementary", { name: "Widget library" }).getByRole("button", { name: "Done" }).click();
  await page.waitForTimeout(2500);
  await shot("Home, colour-coded", "Colour-coded widgets");
});

// The job map: every point carries its name.
await attempt("map", async () => {
  await go("jobs", 2600);
  await page.getByRole("navigation", { name: "Jobs view" }).getByRole("button", { name: "Map", exact: true }).click();
  await page.waitForTimeout(5200);
  await shot("Find jobs, map: names on every point", "Job search");
  await page.getByRole("navigation", { name: "Jobs view" }).getByRole("button", { name: "List", exact: true }).click();
  await page.waitForTimeout(900);
});

// Forms.
for (const [hash, title] of [["resumes", "Resumes: one row to add"], ["questions", "Questions"], ["finder", "Find companies"]]) { await go(hash, 2600); await shot(title, "Forms without boxes"); }
await attempt("ticked", async () => { await go("queue", 2600); for (const i of [0, 2]) await page.locator(".row-check").nth(i).check(); await page.mouse.move(700, 880); await page.waitForTimeout(400); await shot("Ticked rows: the bar appears at the bottom", "Job search"); });

// The new Kitty theme: three loads, three different scenes.
await attempt("theme", async () => {
  await go("settings/modules", 2200);
  const themes = page.getByRole("switch", { name: "Themes" });
  if ((await themes.getAttribute("aria-checked")) === "false") { await themes.click(); await page.waitForTimeout(900); }
  await go("settings/appearance", 2000);
  await page.locator('input[name="appearance-theme"][value="aero"]').check({ force: true, timeout: 8000 });
  await page.waitForTimeout(3000);
  await shot("Kitty: Appearance", "The new Kitty theme");
  await go("today", 3000); await shot("Kitty: home", "The new Kitty theme");
  await page.reload({ waitUntil: "load" }); await page.waitForTimeout(4500); await shot("Kitty: home again, a different roll", "The new Kitty theme");
  await go("notes", 3000); await shot("Kitty: notes", "The new Kitty theme");
  await go("calendar", 2800); await shot("Kitty: calendar", "The new Kitty theme");
  await go("jobs", 2800); await shot("Kitty: job search", "The new Kitty theme");
  await go("rika", 2800); await shot("Kitty: Rika", "The new Kitty theme");
  await go("documents", 2800); await shot("Kitty: an empty page", "The new Kitty theme");
  await page.reload({ waitUntil: "load" }); await page.waitForTimeout(1200); await shot("Kitty: loading", "The new Kitty theme");
  await page.waitForTimeout(3500);
  await page.keyboard.press("Control+Space"); await page.waitForTimeout(900); await shot("Kitty: search", "The new Kitty theme");
});
writeFileSync(`${out}/shots.json`, JSON.stringify(shots));
console.log("shots:", shots.length, "errors:", JSON.stringify([...new Set(errors)].slice(0, 6)));
await browser.close();
