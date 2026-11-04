// Browser fixture for tests/rikaSettings.browser.test.mjs. Mounts the real Settings view
// (with its Rika pages) and the real Rika UI; the test serves /api/* with a scripted stand-in.
// The look comes from a stand-in build, as in every harness on the platform stack.
import { mount } from "svelte";
import "@nunera/bracket/styles.css";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { loadBuild, setTheme } from "/src/lib/build.svelte.ts";
import { createBuildStub } from "./buildStub.js";
import { rika } from "/src/lib/rika/session.svelte.ts";
import RikaSettingsHarness from "./RikaSettingsHarness.svelte";

const build = createBuildStub();
const served = window.fetch.bind(window);
window.fetch = (url, init = {}) => build.fetch(url, init) ?? served(url, init);
await loadBuild();
const navigations = [];
mount(RikaSettingsHarness, { target: document.getElementById("app"), props: { opened: (view) => navigations.push(view) } });
window.rikaSettingsHarness = { navigations, setTheme, connected: () => rika.connected };
