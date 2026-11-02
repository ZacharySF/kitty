// For browser tests against a real kitty-web: each browser context gets its own layer of
// interface preferences over the server's build (docs/platform-design.md Q8). `seed` starts
// the layer; every preference the page saves lands in it (the returned object) instead of
// the shared server, so one test's dock, theme or look never leaks into another, and a
// reload in the same context keeps what the page saved.

/** `patch` merged into `target` in place, as the server merges preferences into the build. */
export function mergePreferences(target, patch) {
  for (const [key, value] of Object.entries(patch ?? {})) {
    if (value && typeof value === "object" && !Array.isArray(value) && target[key] && typeof target[key] === "object" && !Array.isArray(target[key]))
      mergePreferences(target[key], value);
    else target[key] = value;
  }
  return target;
}

/** The dock as hidden, or floating, in a build's `ui.dock`. */
export const DOCK = {
  hidden: { mode: "hidden", shown: "terminal", float: false, step: "m", size: null, offset: { right: 16, bottom: 16 } },
  floating: { mode: "terminal", shown: "terminal", float: true, step: "m", size: null, offset: { right: 16, bottom: 16 } },
};

export async function routeBuild(context, seed = {}) {
  const prefs = structuredClone(seed);
  await context.route("**/api/build/preferences", async (route) => {
    mergePreferences(prefs, route.request().postDataJSON());
    await route.fulfill({ json: { ok: true } });
  });
  await context.route("**/api/build", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const body = await response.json();
    body.build = mergePreferences(body.build, structuredClone(prefs));
    await route.fulfill({ response, json: body });
  });
  return prefs;
}
