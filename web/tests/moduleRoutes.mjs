// For browser tests of the module picker and the home templates against a real kitty-web: each
// browser context gets its own modules, packs and desktop shell, so a test can choose, download
// and restart without changing the shared server (whose names and descriptions are the ones shown).

const MB = 1e6;
/** Download sizes as docs/rust-core-design.md gives them. */
export const PACK_SIZES = { discovery: 168.5 * MB, math: 29.6 * MB, learnharness: 270 * MB, herdr: 29 * MB, voice: 344.9 * MB, brain: 1.6 * MB };

/**
 * `configured`: the saved selection (also what runs, until a save changes it). `ready`: packs
 * already here. `offline`: no pack can be downloaded. `desktop`: the desktop app answers, so
 * `restart` is accepted. The returned state records what the page asked for; `hold` keeps a
 * download at 40% until it is set false.
 */
export async function routeModules(context, kitty, { configured = ["workspace"], ready = [], offline = false, desktop = false } = {}) {
  const modules = await kitty.request("/modules");
  const packs = await kitty.request("/packs");
  const state = {
    configured: [...configured], enabled: [...configured], posts: [], installs: [], removed: [], commands: [], hold: false,
    packs: Object.fromEntries(packs.packs.map((pack) => [pack.id, { state: ready.includes(pack.id) ? "installed" : offline ? "unavailable" : "available", ticks: 0 }])),
  };
  const same = (a, b) => a.length === b.length && a.every((id) => b.includes(id));
  const listing = () => ({
    version: packs.version,
    index: offline ? { error: "rika! could not reach GitHub to check for downloads" } : {},
    packs: packs.packs.map((pack) => {
      const own = state.packs[pack.id];
      const size = PACK_SIZES[pack.id];
      if (own.state === "downloading") {
        own.ticks += 1;
        if (!state.hold && own.ticks > 2) own.state = "installed";
      }
      return {
        ...pack, state: own.state, size: offline && own.state !== "installed" ? null : size, installed_size: size * 3,
        installed_version: own.state === "installed" ? packs.version : null, error: null,
        progress: own.state === "downloading" ? { received: Math.round(size * 0.4), total: size } : null,
      };
    }),
  });
  await context.route("**/api/modules", (route) => {
    if (route.request().method() === "POST") {
      const { enabled } = route.request().postDataJSON();
      state.posts.push(enabled);
      state.configured = enabled;
    }
    return route.fulfill({ json: { ...modules, enabled: state.enabled, configured: state.configured, restart_required: !same(state.configured, state.enabled), missing: [] } });
  });
  await context.route("**/api/packs", (route) => route.fulfill({ json: listing() }));
  await context.route("**/api/packs/install", (route) => {
    const { id } = route.request().postDataJSON();
    state.installs.push(id);
    Object.assign(state.packs[id], { state: "downloading", ticks: 0 });
    return route.fulfill({ json: listing() });
  });
  await context.route("**/api/packs/uninstall", (route) => {
    const { id } = route.request().postDataJSON();
    state.removed.push(id);
    state.packs[id].state = "available";
    return route.fulfill({ json: listing() });
  });
  await context.route("**/api/desktop", (route) =>
    route.fulfill({ json: desktop ? { available: true, keep_running: false, keep_running_available: true } : { available: false } }));
  await context.route("**/api/desktop/command", (route) => {
    state.commands.push(route.request().postDataJSON().command);
    return desktop ? route.fulfill({ json: { ok: true } })
      : route.fulfill({ status: 409, json: { error: "Desktop controls are only available in the rika! desktop app" } });
  });
  return state;
}
