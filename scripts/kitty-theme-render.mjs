// Renders the Kitty theme's stills (scripts/kitty-theme-scenes.py) to the JPEGs the theme ships.
// Chromium draws them, so the SVG filters (blur, grain) are baked in and cost nothing at run time.
//   node scripts/kitty-theme-render.mjs <svg dir> <out dir>
import { readdirSync, readFileSync, mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { createRequire } from "node:module";

const require = createRequire(resolve("web/package.json"));
const { chromium } = require("playwright-core");
const [from, to] = process.argv.slice(2);
mkdirSync(to, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1200 }, deviceScaleFactor: 1 });
for (const name of readdirSync(from).filter((file) => file.endsWith(".svg"))) {
  await page.setContent(`<body style="margin:0;background:#000">${readFileSync(join(from, name), "utf8")}</body>`);
  await page.waitForTimeout(300);
  const file = join(to, name.replace(/\.svg$/, ".jpg"));
  await page.screenshot({ path: file, type: "jpeg", quality: 78 });
  console.log(file);
}
await browser.close();
