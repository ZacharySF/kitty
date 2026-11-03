// A stand-in for rika!'s build routes (src/kitty/web_build.py) for browser harnesses that
// answer the API themselves: GET /api/build, POST /api/build/preferences (merged like the
// server) and GET /api/build/history. The build survives a reload of the page (sessionStorage
// plays the build file), so harness tests can check that preferences are remembered.
import { DEFAULT_BUILD, deepMerge } from "/src/lib/buildCore.ts";

const KEY = "harness.build";

/** `initial` is merged into the default build the first time this tab loads. */
export function createBuildStub(initial = {}) {
  const read = () => JSON.parse(sessionStorage.getItem(KEY) || "null");
  if (!read()) sessionStorage.setItem(KEY, JSON.stringify(deepMerge(DEFAULT_BUILD, initial)));
  const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
  const stub = {
    /** Every preferences POST body, in order. */
    preferences: [],
    get build() { return read(); },
    /** Change the stand-in file as another window or editor would (then reload the build). */
    write(patch) { sessionStorage.setItem(KEY, JSON.stringify(deepMerge(read(), patch))); },
    /** Answer a build request, or null for other paths. */
    fetch(url, init = {}) {
      const path = new URL(String(url), location.href).pathname;
      const method = init.method ?? "GET";
      if (path === "/api/build" && method === "GET") {
        const build = read();
        return json({
          revision: "harness", active_revision: "harness", build, text: JSON.stringify(build, null, 2), problems: [],
          invalid: false, restart_required: false, safe: false, history: [], local_preferences_moved: true,
        });
      }
      if (path === "/api/build/history") return json({ history: [] });
      if (path === "/api/build/preferences" && method === "POST") {
        const patch = JSON.parse(init.body);
        stub.preferences.push(patch);
        sessionStorage.setItem(KEY, JSON.stringify(deepMerge(read(), patch)));
        return json({ ok: true });
      }
      return null;
    },
  };
  window.harnessBuild = stub;
  return stub;
}

/**
 * For harnesses that answer the API themselves: the build routes come from a stub whose
 * first build is the defaults plus whatever old per-browser preferences a test seeded in
 * localStorage (moved once, as `POST /api/build/local` does), and the build is loaded so
 * the look and its motif are in before the harness mounts. Call it after the harness's own
 * `window.fetch`, which it chains to.
 */
export async function installBuild() {
  const { collectLegacy } = await import("/src/lib/buildCore.ts");
  const stub = createBuildStub(collectLegacy(localStorage).patch);
  const next = window.fetch.bind(window);
  window.fetch = (input, init) => stub.fetch(input, init) ?? next(input, init);
  const build = await import("/src/lib/build.svelte.ts");
  await build.loadBuild();
  await build.lookApplied();
  return stub;
}
