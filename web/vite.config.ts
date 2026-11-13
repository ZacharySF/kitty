import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

/** Only this local dev page may reach the API: a loopback host and, when sent, its own origin. */
const localPage = (host = "", origin?: string) =>
  /^(127\.0\.0\.1|localhost):\d+$/.test(host) && (!origin || origin === `http://${host}`);
/** What the dev server forwards to kitty-web: the API and add-on module page scripts (docs/MODULES.md). */
const backendPath = (url = "") => url.startsWith("/api") || url.startsWith("/modules");

export default defineConfig(({ mode }) => {
  const backendOrigin =
    mode === "demo" ? "http://127.0.0.1:8768" : "http://127.0.0.1:8765";
  return {
    plugins: [
      svelte(),
      {
        name: "local-api-origin",
        configureServer(server) {
          server.middlewares.use((request, response, next) => {
            if (backendPath(request.url)) {
              if (!localPage(request.headers.host, request.headers.origin)) {
                response.writeHead(403, { "Content-Type": "application/json" });
                response.end(
                  JSON.stringify({
                    error: "Only this local website may access the API.",
                  }),
                );
                return;
              }
            }
            next();
          });
        },
      },
    ],
    // Small font subsets must remain same-origin under the server's font-src policy.
    build: { assetsInlineLimit: 0 },
    server: {
      host: "127.0.0.1",
      proxy: {
        "/api": {
          target: backendOrigin,
          changeOrigin: true,
          // Rika's stream (/api/rika/stream) is a WebSocket through the same proxy.
          ws: true,
          configure(proxy) {
            proxy.on("proxyReq", (request) => {
              // The middleware rejects foreign browser origins before this rewrite.
              request.setHeader("Origin", backendOrigin);
            });
            // Upgrades skip the middleware, so the same check runs here; browsers always send Origin.
            proxy.on("proxyReqWs", (request, incoming, socket) => {
              if (!incoming.headers.origin || !localPage(incoming.headers.host, incoming.headers.origin)) {
                socket.destroy();
                return;
              }
              request.setHeader("Origin", backendOrigin);
            });
          },
        },
        // Add-on module page scripts (`modules.external.views[].script`) are static files the backend serves.
        // A browser sends Origin with every module script import, and kitty-web refuses one not its own.
        "/modules": {
          target: backendOrigin,
          changeOrigin: true,
          configure(proxy) {
            proxy.on("proxyReq", (request) => {
              request.setHeader("Origin", backendOrigin);
            });
          },
        },
      },
    },
  };
});
