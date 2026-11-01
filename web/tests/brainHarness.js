// Browser fixture for tests/brain.browser.test.mjs. Mounts the Brain stage (or a
// graph that must never show it) against whatever `/api/*` the test routes.
import { mount, unmount, flushSync } from "svelte";
import "@nunera/bracket/styles.css";
import "/src/styles.css";
import "/src/liquidGlass.css";
import { setActiveModules } from "/src/lib/modules.svelte.ts";
import { setTheme } from "/src/lib/themes.ts";
import BrainHarness from "./BrainHarness.svelte";

const host = document.getElementById("host");
let instance = null;

window.brainHarness = {
  /** Replaces the fixture with a fresh `kind` ("stage" | "atlas" | "section" | "schema"). */
  mount(kind, { look, home } = {}) {
    if (instance) unmount(instance);
    host.replaceChildren();
    if (look) localStorage.setItem("kitty.graph.look", look);
    if (home) localStorage.setItem("kitty.home.look", home);
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
window.dispatchEvent(new Event("brain-harness-ready"));
