// Builds dist/chromium and dist/firefox (unpacked, loadable as they are) and a zip of each.
// Dependencies (Readability, webextension-polyfill) come from npm and are bundled by
// esbuild; their licences go into THIRD_PARTY_NOTICES.txt. Code is not minified, so a
// reviewer (Mozilla's signing) can read it.
import { build } from "esbuild";
import { cpSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { crc32, deflateRawSync } from "node:zlib";
import { manifest, TARGETS } from "./manifest.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const { version } = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const dist = join(root, "dist");
const NOTICES = [
  ["@mozilla/readability", "LICENSE.md", "Apache-2.0"],
  ["webextension-polyfill", "LICENSE", "MPL-2.0"],
];

/** A zip (deflate) of every file under `folder`, with fixed timestamps so builds repeat. */
function zip(folder) {
  const files = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir).sort()) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else files.push(path);
    }
  };
  walk(folder);
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const path of files) {
    const name = Buffer.from(relative(folder, path).split("\\").join("/"));
    const data = readFileSync(path);
    const packed = deflateRawSync(data, { level: 9 });
    const crc = crc32(data);
    // DOS time 00:00, date 2026-01-01.
    const time = 0, date = ((2026 - 1980) << 9) | (1 << 5) | 1;
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(8, 8);
    local.writeUInt16LE(time, 10);
    local.writeUInt16LE(date, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(packed.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0x0800, 8);
    central.writeUInt16LE(8, 10);
    central.writeUInt16LE(time, 12);
    central.writeUInt16LE(date, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(packed.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt32LE(offset, 42);
    locals.push(local, name, packed);
    centrals.push(central, name);
    offset += local.length + name.length + packed.length;
  }
  const directory = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, directory, end]);
}

async function bundle(target) {
  const out = join(dist, target);
  rmSync(out, { recursive: true, force: true });
  mkdirSync(out, { recursive: true });
  await build({
    entryPoints: { background: "src/background.ts", content: "src/content.ts", panel: "src/panel.ts" },
    absWorkingDir: root,
    outdir: out,
    bundle: true,
    format: "iife",
    target: target === "chromium" ? "chrome120" : "firefox140",
    define: { __TARGET__: JSON.stringify(target) },
    legalComments: "eof",
    charset: "utf8",
    logLevel: "warning",
    // Drops the other browser's branch (__TARGET__) while names and layout stay readable.
    minifySyntax: true,
  });
  cpSync(join(root, "src", "panel.html"), join(out, "panel.html"));
  cpSync(join(root, "src", "panel.css"), join(out, "panel.css"));
  cpSync(join(root, "icons"), join(out, "icons"), { recursive: true });
  cpSync(join(root, "LICENSE"), join(out, "LICENSE"));
  writeFileSync(join(out, "manifest.json"), `${JSON.stringify(manifest(target, version), null, 2)}\n`);
  const notices = NOTICES.map(([name, file, licence]) =>
    `${name} (${licence})\n${"=".repeat(60)}\n${readFileSync(join(root, "node_modules", name, file), "utf8").trim()}\n`);
  writeFileSync(join(out, "THIRD_PARTY_NOTICES.txt"), `kitty! for the browser bundles these packages unmodified.\n\n${notices.join("\n\n")}`);
  const archive = join(dist, `kitty-browser-${target}-${version}.zip`);
  writeFileSync(archive, zip(out));
  return archive;
}

for (const target of process.argv.slice(2).length ? process.argv.slice(2) : TARGETS) {
  console.log(`built ${relative(root, await bundle(target))}`);
}
