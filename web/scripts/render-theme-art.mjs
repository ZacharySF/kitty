// Renders the themes' filtered art (turbulence washes, displacement, blurs, noise grain) from
// its SVG sources to static WebP, so no SVG filter runs while the app paints: a filter on the
// fixed art layer was re-run on every frame something above it moved (measured at 2–10 fps
// in Chromium and far worse in WebKitGTK). Re-run after editing a `*.source.svg`; the WebP
// files it writes beside each source are committed.
// Run from web/ with Node >=22 and a Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> node scripts/render-theme-art.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const themes = fileURLToPath(new URL("../src/lib/themes/", import.meta.url));

/**
 * [source, output widths in px, WebP quality]. Grounds get a 1x and a 2x file of their
 * viewBox (srcset picks one); a heavily blurred ground needs only 1x; noise tiles are drawn
 * at their CSS size. Every output is `<source name>-<width>.webp`.
 */
const ART = [
  ["speed/paper.source.svg", [1600, 3200], 0.84],
  ["c/bleed.source.svg", [1600], 0.86],
  ["bloodluxe/marble.source.svg", [1440, 2880], 0.84],
  ["pretti/air.source.svg", [1440], 0.86],
  ["red/brush.source.svg", [680], 0.9],
  ["grain.source.svg", [140], 0.9],
  ["speed/grain.source.svg", [160], 0.9],
  ["speed/fibres.source.svg", [200], 0.9],
  ["c/scratch.source.svg", [256], 0.9],
  ["pretti/spray.source.svg", [256], 0.9],
];

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined, args: ["--disable-gpu"] });
const page = await browser.newPage();
for (const [source, widths, quality] of ART) {
  const svg = readFileSync(themes + source, "utf8");
  for (const width of widths) {
    const data = await page.evaluate(
      async ([text, wide, q]) => {
        const image = new Image();
        image.src = URL.createObjectURL(new Blob([text], { type: "image/svg+xml" }));
        await image.decode();
        const height = Math.round((wide * image.naturalHeight) / image.naturalWidth);
        const canvas = Object.assign(document.createElement("canvas"), { width: wide, height });
        canvas.getContext("2d").drawImage(image, 0, 0, wide, height);
        return canvas.toDataURL("image/webp", q).split(",")[1];
      },
      [svg, width, quality],
    );
    const out = source.replace(/\.source\.svg$/, `-${width}.webp`);
    const bytes = Buffer.from(data, "base64");
    writeFileSync(themes + out, bytes);
    console.log(`${out}  ${(bytes.length / 1024).toFixed(0)} KiB`);
  }
}
await browser.close();
