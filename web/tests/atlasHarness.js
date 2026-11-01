// Browser fixture for tests/atlasNodes.browser.test.mjs. Mounts the real atlas
// (GraphNavigator) in several workspace tabs with synthetic module selections.
import { mount, unmount, flushSync } from "svelte";
import "@nunera/bracket/styles.css";
import "/src/styles.css";
import { setActiveModules } from "/src/lib/modules.svelte.ts";
import AtlasHarness from "./AtlasHarness.svelte";

const host = document.getElementById("tabs");
const errors = [];
window.addEventListener("error", (event) => errors.push(event.message));
window.addEventListener("unhandledrejection", (event) => errors.push(String(event.reason?.message ?? event.reason)));
let tabs = [];
const opened = [];

window.atlasHarness = {
  errors,
  opened,
  /** Replaces every tab with fresh 2D atlases using the given modules. */
  async mount({ enabled, count = 2 }) {
    for (const tab of tabs) unmount(tab.instance);
    host.replaceChildren();
    tabs = [];
    localStorage.setItem("kitty.home.look", "2d");
    setActiveModules(enabled);
    for (let index = 0; index < count; index += 1) {
      const target = document.createElement("div");
      target.className = `tab tab-${index}`;
      host.append(target);
      tabs.push({ target, instance: mount(AtlasHarness, { target, props: { id: `tab-${index}`, opened: (view) => opened.push([index, view]) } }) });
    }
    flushSync();
  },
  point: (tab, id) => tabs[tab].instance.nodePoint(id),
  /** Resolves once the listed nodes' positions are unchanged across several frames. */
  async settled(tab, ids) {
    const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
    let previous = "";
    let stable = 0;
    for (let index = 0; index < 900 && stable < 6; index += 1) {
      await frame();
      const current = JSON.stringify(ids.map((id) => tabs[tab].instance.nodePoint(id)));
      stable = current === previous ? stable + 1 : 0;
      previous = current;
    }
    if (stable < 6) throw new Error("atlas never settled");
  },
};
window.dispatchEvent(new Event("atlas-harness-ready"));
