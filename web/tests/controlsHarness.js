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
import { applySavedTheme } from "/src/lib/themes/index.ts";
import ControlsHarness from "./ControlsHarness.svelte";

window.controlsLog = [];
// The saved theme's motif module is in before mounting, as in main.ts.
await applySavedTheme();
mount(ControlsHarness, { target: document.getElementById("view") });
window.controlsReady = true;
