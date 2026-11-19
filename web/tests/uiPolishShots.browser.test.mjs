// The rika-ui-polish screenshot pass (.claude/skills/rika-ui-polish/scripts/shots.mjs) against
// two static pages: one that follows the rules and one that breaks each hard check. Agents rely
// on its exit code to decide whether a UI change is done, so a check that stops firing is a bug.
// Run from web/ with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/uiPolishShots.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(new URL("../../.claude/skills/rika-ui-polish/scripts/shots.mjs", import.meta.url));

const head = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Fixture</title>
<meta name="viewport" content="width=device-width">
<style>
  :root { --motion-ms: 200ms; }
  @media (prefers-reduced-motion: reduce) { :root { --motion-ms: 0ms; } }
  body { margin: 0; font: 16px sans-serif; background: #000; color: #fff; }
  button { min-width: 80px; min-height: 28px; }
</style>`;

const pages = {
  "/good": `${head}</head><body><main><h1>Today</h1><button type="button">Save changes</button></main></body></html>`,
  "/bad": `${head}<style>
  a { outline: none; color: #fff; }
  .wide { width: 900px; height: 10px; }
  </style></head><body><main><h1>Today</h1>
  <a href="#x">Open</a><div class="wide"></div>
  <script>
    document.querySelector("h1").animate([{ opacity: 1 }, { opacity: 0.5 }], { duration: 1000, iterations: Infinity });
  </script></main></body></html>`,
};

function run(url) {
  return new Promise((resolve) => {
    execFile(process.execPath, [script, url, "today"], { timeout: 120_000 }, (error, stdout, stderr) => {
      const out = /out: (.+)/.exec(stdout)?.[1];
      const report = out ? JSON.parse(readFileSync(`${out}/report.json`, "utf8")) : null;
      resolve({ code: error ? error.code : 0, stdout, stderr, report });
    });
  });
}

describe("rika-ui-polish shots.mjs", () => {
  let server;
  let base;

  before(async () => {
    server = createServer((req, res) => {
      const body = pages[req.url];
      res.writeHead(body ? 200 : 404, { "content-type": "text/html" });
      res.end(body ?? "");
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    base = `http://127.0.0.1:${server.address().port}`;
  });

  after(() => server?.close());

  test("a page that follows the rules passes with exit 0 and writes every screenshot", async () => {
    const { code, stdout, stderr, report } = await run(`${base}/good`);
    assert.equal(code, 0, `${stdout}\n${stderr}`);
    assert.match(stdout, /PASS: no hard failures/);
    assert.deepEqual(report.failures, []);
    const shots = report.routes[0].shots.map((s) => `${s.size}/${s.motion}`).sort();
    assert.deepEqual(shots, ["desktop/no-preference", "desktop/reduce", "phone/no-preference", "phone/reduce"]);
    assert.ok(report.routes[0].focus.some((f) => f.ring && f.file), "focus crop with a visible ring");
  });

  test("a page that breaks the rules exits 2 and names each failure", async () => {
    const { code, stdout, stderr, report } = await run(`${base}/bad`);
    assert.equal(code, 2, `${stdout}\n${stderr}`);
    const failures = report.failures.join("\n");
    assert.match(failures, /phone-390x844-.*scrolls sideways by \d+px/);
    assert.match(failures, /animations still running under reduced motion/);
    assert.match(failures, /no visible focus ring on "Open" \(a\)/);
  });
});
