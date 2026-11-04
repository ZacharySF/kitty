// Browser fixture for tests/controls.browser.test.mjs. Mounts the shared controls
// (Select, OverflowMenu, Popover, StatusDot, QuickAdd, EditDrawer) with the app's
// stylesheets and faces; `window.controlsLog` records what each control reported.
import { mount } from "svelte";
import "@nunera/bracket/styles.css";
import "@fontsource-variable/inter";
import "@fontsource-variable/fraunces/full.css";
import "@fontsource-variable/jetbrains-mono";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { installBuild } from "./buildStub.js";
import ControlsHarness from "./ControlsHarness.svelte";

window.controlsLog = [];
// The build's theme and its motif module are in before mounting, as in main.ts.
await installBuild();
mount(ControlsHarness, { target: document.getElementById("view") });
window.controlsReady = true;
