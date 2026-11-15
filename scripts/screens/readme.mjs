// README screenshots: 1400x875 JPEGs of the demo, Black theme, then the Kitty theme.
// Usage: node readme-shots.mjs <demo-link> <out dir>
import { chromium } from "../../web/node_modules/playwright-core/index.mjs";
import { mkdirSync } from "node:fs";
const out = process.argv[3];
mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 875 }, deviceScaleFactor: 1 });
const errors = []; page.on("pageerror", (e) => errors.push(e.message));
const shot = (name, clip) => page.screenshot({ path: `${out}/${name}.jpg`, type: "jpeg", quality: 84, ...(clip ? { clip } : {}) });
const go = async (hash, wait = 2600) => { await page.evaluate((h) => { location.hash = h; }, hash); await page.waitForTimeout(wait); };
const attempt = async (label, run) => { try { await run(); } catch (e) { console.log("skipped", label, String(e.message).split("\n")[0].slice(0, 200)); } };
await page.goto(process.argv[2], { waitUntil: "load" });
await page.waitForTimeout(4500);
const skip = page.getByRole("button", { name: /Skip/ });
if (await skip.count()) { await skip.first().click(); await page.waitForTimeout(2500); }
await go("today", 3200);
await attempt("offer", async () => { const b = page.getByRole("region", { name: "Home template" }).getByRole("button", { name: "Not now" }); if (await b.count()) await b.click(); });
await page.waitForTimeout(600);
await shot("home");
await attempt("jobs", async () => { await go("queue", 2800); for (const i of [0, 2]) await page.locator(".row-check").nth(i).check(); await page.mouse.move(700, 500); await page.waitForTimeout(500); await shot("jobs"); for (const i of [0, 2]) await page.locator(".row-check").nth(i).uncheck(); });
await attempt("rika", async () => { await go("rika", 3000); await shot("rika"); });
await attempt("modules", async () => {
  await go("settings/modules", 2600);
  const box = await page.locator("main").boundingBox();
  await shot("modules-full");
});
await attempt("kitty", async () => {
  const themes = page.getByRole("switch", { name: "Themes" });
  if ((await themes.getAttribute("aria-checked")) === "false") { await themes.click(); await page.waitForTimeout(900); }
  await go("settings/appearance", 2200);
  await shot("themes");
  await page.locator('input[name="appearance-theme"][value="aero"]').check({ force: true, timeout: 8000 });
  await page.waitForTimeout(3000);
  for (let i = 0; i < 3; i += 1) { await go("today", 2600); await shot(`kitty-${i + 1}`); await page.reload({ waitUntil: "load" }); await page.waitForTimeout(4200); }
});
console.log("errors:", errors.slice(0, 3));
await browser.close();
