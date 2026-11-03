// Browser fixture for tests/brain.browser.test.mjs. Mounts the Brain stage (or a
// graph that must never show it) against whatever `/api/*` the test routes.
import { mount, unmount, flushSync } from "svelte";
import "@nunera/bracket/styles.css";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { setActiveModules } from "/src/lib/modules.svelte.ts";
import { setPreferences, setTheme } from "/src/lib/build.svelte.ts";
import { installBuild } from "./buildStub.js";
import BrainHarness from "./BrainHarness.svelte";

const host = document.getElementById("host");
let instance = null;

const brainHarness = {
  /** Replaces the fixture with a fresh `kind` ("stage" | "atlas" | "section" | "schema"). */
  mount(kind, { look, home } = {}) {
    if (instance) unmount(instance);
    host.replaceChildren();
    if (look || home) void setPreferences({ ui: { ...(look ? { graph: { look } } : {}), ...(home ? { home: { look: home } } : {}) } });
    setActiveModules(["workspace"]);
    instance = mount(BrainHarness, { target: host, props: { kind } });
    flushSync();
  },
  unmount() {
    if (instance) unmount(instance);
    instance = null;
  },
  screenPoint: (id) => instance?.screenPoint(id) ?? null,
  worldPoint: (id) => instance?.worldPoint(id) ?? null,
  pointColour: (id) => instance?.pointColour(id) ?? null,
  setTheme: (id) => setTheme(id),
};
// The build loads before the harness is offered (no top-level await: a reload's load event never waits on it).
void installBuild().then(() => {
  window.brainHarness = brainHarness;
  window.dispatchEvent(new Event("brain-harness-ready"));
});
