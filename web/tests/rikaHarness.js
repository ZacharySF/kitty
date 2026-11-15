// Browser fixture for tests/rika.browser.test.mjs. Mounts the real Rika drawer and toasts
// and, with `?panel`, the home's Rika panel in the page; `?modules=a,b` sets the active
// modules (voice input needs `voice`). The test serves /api/rika/* and the stream with a
// scripted stand-in. The look comes from a stand-in build, as in every harness on the
// platform stack.
import { mount } from "svelte";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { loadBuild } from "/src/lib/build.svelte.ts";
import { setActiveModules } from "/src/lib/modules.svelte.ts";
import { createBuildStub } from "./buildStub.js";
import Rika from "/src/lib/rika/Rika.svelte";
import RikaPanel from "/src/lib/rika/RikaPanel.svelte";
import { rika } from "/src/lib/rika/session.svelte.ts";

const build = createBuildStub();
const served = window.fetch.bind(window);
window.fetch = (url, init = {}) => build.fetch(url, init) ?? served(url, init);
await loadBuild();
const query = new URLSearchParams(location.search);
setActiveModules((query.get("modules") ?? "").split(",").filter(Boolean));
const navigations = [];
mount(Rika, { target: document.getElementById("rika"), props: { onnavigate: (view) => navigations.push(view) } });
if (query.has("panel")) {
  const view = document.getElementById("view");
  view.replaceChildren();
  view.classList.add("with-panel");
  mount(RikaPanel, { target: view, props: { id: "harness-rika", active: true } });
}
window.rikaHarness = { navigations, connected: () => rika.connected };
