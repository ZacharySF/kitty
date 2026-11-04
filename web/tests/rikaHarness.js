// Browser fixture for tests/rika.browser.test.mjs. Mounts the real Rika bar, toasts and
// history drawer; the test serves /api/rika/* and the stream with a scripted stand-in.
// The look comes from a stand-in build, as in every harness on the platform stack.
import { mount } from "svelte";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { loadBuild } from "/src/lib/build.svelte.ts";
import { createBuildStub } from "./buildStub.js";
import Rika from "/src/lib/rika/Rika.svelte";
import { rika } from "/src/lib/rika/session.svelte.ts";

const build = createBuildStub();
const served = window.fetch.bind(window);
window.fetch = (url, init = {}) => build.fetch(url, init) ?? served(url, init);
await loadBuild();
const navigations = [];
mount(Rika, { target: document.getElementById("rika"), props: { onnavigate: (view) => navigations.push(view) } });
window.rikaHarness = { navigations, connected: () => rika.connected };
