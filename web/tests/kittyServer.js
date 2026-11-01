// A real kitty! server for browser tests that need the Python backend: `kitty-web --demo`
// (a disposable synthetic workspace in a temporary directory; never the owner's data) on a
// free port, plus a Vite dev server for the test harness that proxies /api (HTTP and the
// blocks WebSocket) to it with the exact Host and Origin kitty!'s request guard expects.
import { spawn } from "node:child_process";
import { createServer as createNetServer } from "node:net";
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

/** Start `kitty-web --demo`; resolves with { origin, stop, request } once it serves. */
export async function startKitty() {
  const port = await freePort();
  const child = spawn("uv", ["run", "--project", repo, "kitty-web", "--demo", "--port", String(port)], {
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
    /** One API call straight to the server (seeding and checking state). */
    async request(path, body) {
      const response = await fetch(`${origin}/api${path}`, body === undefined ? { headers: { Origin: origin } } : { method: "POST", headers, body: JSON.stringify(body) });
      const data = await response.json();
      if (!response.ok) throw new Error(`${path}: ${response.status} ${data.error}`);
      return data;
    },
    stop() {
      try { process.kill(-child.pid, "SIGTERM"); } catch { /* already gone */ }
    },
  };
}

/** A Vite dev server for web/ whose /api goes to `origin` (including WebSocket upgrades). */
export async function startHarness(origin) {
  const rewrite = (request) => request.setHeader("Origin", origin);
  const server = await createServer({
    root: web, configFile: false, logLevel: "error", plugins: [svelte()],
    server: {
      host: "127.0.0.1", port: 0,
      proxy: { "/api": { target: origin, changeOrigin: true, ws: true, configure(proxy) { proxy.on("proxyReq", rewrite); proxy.on("proxyReqWs", rewrite); } } },
    },
  });
  await server.listen();
  return server;
}
