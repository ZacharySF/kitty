// A real rika! server for browser tests that need the Python backend: `kitty-web --demo`
// (a disposable synthetic workspace in a temporary directory; never the owner's data), or
// `kitty-web --root <dir>` on a temporary workspace the test created, on a free port, plus a
// Vite dev server for the test harness that proxies /api (HTTP and the live WebSocket) to it
// with the exact Host and Origin rika!'s request guard expects.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

const web = fileURLToPath(new URL("..", import.meta.url));
const repo = fileURLToPath(new URL("../..", import.meta.url));

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = createNetServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

/**
 * Start kitty-web; resolves with { origin, root, stop, request } once it serves. Without
 * `root` it is `--demo`; with `root` (a temporary workspace the caller made) it serves that
 * workspace, so a test can edit its files, such as config/build.json, like an outside editor.
 */
export async function startKitty({ root } = {}) {
  const port = await freePort();
  const where = root ? ["--root", root] : ["--demo"];
  const child = spawn("uv", ["run", "--project", repo, "kitty-web", ...where, "--port", String(port)], {
    cwd: repo, detached: true, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, KITTY_KEYRING: "none", KITTY_SYNC: "off" },
  });
  let output = "";
  const origin = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`kitty-web did not start:\n${output}`)), 120_000);
    const read = (chunk) => {
      output += chunk;
      const match = /website: (http:\/\/127\.0\.0\.1:\d+)/.exec(output);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    };
    child.stdout.on("data", read);
    child.stderr.on("data", read);
    child.once("exit", (code) => { clearTimeout(timer); reject(new Error(`kitty-web exited (${code}):\n${output}`)); });
  });
  const headers = { Origin: origin, "X-Kitty-Request": "1", "Content-Type": "application/json" };
  return {
    origin,
    root,
    /** One API call straight to the server (seeding and checking state). */
    async request(path, body) {
      const response = await fetch(`${origin}/api${path}`, body === undefined ? { headers: { Origin: origin } } : { method: "POST", headers, body: JSON.stringify(body) });
      const data = await response.json();
      if (!response.ok) throw new Error(`${path}: ${response.status} ${data.error}`);
      return data;
    },
    /** Stop the server; resolves once it exited (its workspace files are closed). */
    stop() {
      const exited = child.exitCode !== null || child.signalCode !== null
        ? Promise.resolve() : new Promise((resolve) => child.once("exit", resolve));
      try { process.kill(-child.pid, "SIGTERM"); } catch { /* already gone */ }
      return exited;
    },
  };
}

/**
 * A Vite dev server for web/ whose /api goes to `origin` (including WebSocket upgrades).
 * Its dependency cache is its own: test files run in parallel, and a server with this
 * configuration re-optimizing the shared web/node_modules/.vite would invalidate the
 * modules other test files' pages are loading.
 */
export async function startHarness(origin) {
  const rewrite = (request) => request.setHeader("Origin", origin);
  const cacheDir = mkdtempSync(join(tmpdir(), "kitty-vite-harness-"));
  const server = await createServer({
    root: web, configFile: false, logLevel: "error", plugins: [svelte()], cacheDir,
    server: {
      host: "127.0.0.1", port: 0,
      proxy: { "/api": { target: origin, changeOrigin: true, ws: true, configure(proxy) { proxy.on("proxyReq", rewrite); proxy.on("proxyReqWs", rewrite); } } },
    },
  });
  await server.listen();
  const close = server.close.bind(server);
  server.close = async () => {
    await close();
    rmSync(cacheDir, { recursive: true, force: true });
  };
  return server;
}
