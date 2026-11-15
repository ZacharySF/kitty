import { defineConfig, type Plugin } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

/** Only this local dev page may reach the API: a loopback host and, when sent, its own origin. */
const localPage = (host = "", origin?: string) =>
  /^(127\.0\.0\.1|localhost):\d+$/.test(host) && (!origin || origin === `http://${host}`);

/**
 * Module packs (docs/rust-core-design.md, milestone 3): the modules whose web code is a
 * download. A chunk holding any module that matches a pack's patterns is that pack's (Rollup
 * groups modules by the imports that need them, so the rest of the chunk, such as a library's own
 * dependencies, loads only with it); the pack's files are those chunks, the chunks only they
 * reach, and their CSS and assets. The base may import them only dynamically. They are listed
 * in `dist/pack-files.json`, which scripts/build-packs.py packs (and strips from the base
 * install) and rika-server (server/src/packs.rs) reads to tell a build that still carries them.
 */
const PACKS: Record<string, RegExp[]> = {
  brain: [/\/src\/lib\/brain\//, /\/node_modules\/three\//, /\/node_modules\/3d-force-graph\//],
  // Every theme's module but the default's (themes/kitty stays in the base: it is the look
  // a fresh install opens in). Colours live in themes/catalog.ts, so a theme without its
  // pack still recolours the app; only its art, loader and motif stylesheet are missing.
  themes: [/\/src\/lib\/themes\/(aero|blossom|liquid-glass|scythe|speed|c|red|pretti|bloodluxe|halfblood)\//],
  animations: [/\/src\/lib\/animations\//],
};

function packFiles(): Plugin {
  return {
    name: "pack-files",
    apply: "build",
    generateBundle(_options, bundle) {
      const chunks = Object.values(bundle).filter((item) => item.type === "chunk");
      const byName = new Map(chunks.map((chunk) => [chunk.fileName, chunk]));
      const owner = new Map<string, string>();
      for (const chunk of chunks) {
        const ids = chunk.moduleIds.filter((id) => !id.startsWith("\0"));
        const id = Object.keys(PACKS).find((pack) => ids.some((module) => PACKS[pack].some((pattern) => pattern.test(module))));
        if (id) owner.set(chunk.fileName, id);
      }
      const reach = (start: string[], follow: (name: string) => boolean) => {
        const seen = new Set<string>();
        const queue = [...start];
        while (queue.length) {
          const name = queue.pop()!;
          if (seen.has(name)) continue;
          seen.add(name);
          for (const next of [...(byName.get(name)?.imports ?? []), ...(byName.get(name)?.dynamicImports ?? [])])
            if (follow(next)) queue.push(next);
        }
        return seen;
      };
      // The base: every entry and what it reaches without entering a pack.
      const base = reach(chunks.filter((chunk) => chunk.isEntry).map((chunk) => chunk.fileName), (name) => !owner.has(name));
      for (const name of base)
        for (const imported of byName.get(name)?.imports ?? [])
          if (owner.has(imported)) this.error(`${name} imports pack ${owner.get(imported)}'s ${imported} statically`);
      const files = (names: Iterable<string>) => {
        const out = new Set<string>();
        for (const name of names) {
          out.add(name);
          const meta = byName.get(name)?.viteMetadata;
          for (const extra of [...(meta?.importedCss ?? []), ...(meta?.importedAssets ?? [])]) out.add(extra);
        }
        return out;
      };
      const baseFiles = files(base);
      const listed: Record<string, string[]> = {};
      for (const id of Object.keys(PACKS)) {
        const own = [...owner].filter(([, pack]) => pack === id).map(([name]) => name);
        if (!own.length) this.error(`pack ${id}: no chunk is made only of its modules`);
        // Its chunks and the helpers only they reach; another pack's chunks stay that pack's.
        const reached = reach(own, (name) => !base.has(name) && (owner.get(name) ?? id) === id);
        listed[id] = [...files(reached)].filter((name) => !baseFiles.has(name)).sort();
      }
      this.emitFile({ type: "asset", fileName: "pack-files.json", source: `${JSON.stringify(listed, null, 2)}\n` });
    },
  };
}

export default defineConfig(({ mode }) => {
  const backendOrigin =
    mode === "demo" ? "http://127.0.0.1:8768" : "http://127.0.0.1:8765";
  return {
    plugins: [
      svelte(),
      packFiles(),
      {
        name: "local-api-origin",
        configureServer(server) {
          server.middlewares.use((request, response, next) => {
            if (request.url?.startsWith("/api")) {
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
      },
    },
  };
});
