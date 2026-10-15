import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

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
            if (request.url?.startsWith("/api")) {
              const host = request.headers.host || "";
              const origin = request.headers.origin;
              if (
                !/^(127\.0\.0\.1|localhost):\d+$/.test(host) ||
                (origin && origin !== `http://${host}`)
              ) {
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
    server: {
      host: "127.0.0.1",
      proxy: {
        "/api": {
          target: backendOrigin,
          changeOrigin: true,
          configure(proxy) {
            proxy.on("proxyReq", (request) => {
              // The middleware rejects foreign browser origins before this rewrite.
              request.setHeader("Origin", backendOrigin);
            });
          },
        },
      },
    },
  };
});
