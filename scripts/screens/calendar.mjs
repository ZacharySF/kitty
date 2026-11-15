// Calendar screenshots: every view, default theme (and Kitty theme with --kitty).
import { chromium } from "../../web/node_modules/playwright-core/index.mjs";
import { mkdirSync, writeFileSync } from "node:fs";
const out = process.argv[3] ?? "/tmp/design-labs/calendar-before";
mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5 });
const errors = []; page.on("pageerror", (e) => errors.push(e.message));
const shots = []; let n = 0;
const shot = async (title, section) => { n += 1; const file = `${String(n).padStart(2, "0")}-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.png`; await page.screenshot({ path: `${out}/${file}` }); shots.push({ file, title, section }); };
const go = async (hash, wait = 2600) => { await page.evaluate((h) => { location.hash = h; }, hash); await page.waitForTimeout(wait); };
await page.goto(process.argv[2], { waitUntil: "load" });
await page.waitForTimeout(4500);
const skip = page.getByRole("button", { name: /Skip/ });
if (await skip.count()) { await skip.first().click(); await page.waitForTimeout(2500); }
const views = async (section) => {
  await go("calendar", 3000);
  await shot("Calendar as it opens", section);
  for (const name of ["Day", "Week", "Month", "Agenda", "Year", "List"]) {
    const b = page.getByRole("radio", { name, exact: true }).or(page.getByRole("button", { name, exact: true })).or(page.getByRole("tab", { name, exact: true }));
    if (await b.count()) { await b.first().click({ force: true }).catch(() => {}); await page.waitForTimeout(1400); await shot(`Calendar: ${name}`, section); }
  }
};
await views("Black");
if (process.argv.includes("--kitty")) {
  await go("settings/modules", 2200);
  const themes = page.getByRole("switch", { name: "Themes" });
  if ((await themes.getAttribute("aria-checked")) === "false") { await themes.click(); await page.waitForTimeout(900); }
  await go("settings/appearance", 2200);
  await page.locator('input[name="appearance-theme"][value="aero"]').check({ force: true, timeout: 8000 });
  await page.waitForTimeout(2500);
  await views("Kitty");
}
writeFileSync(`${out}/shots.json`, JSON.stringify(shots, null, 1));
console.log("shots:", shots.length, "errors:", errors.slice(0, 3));
await browser.close();
