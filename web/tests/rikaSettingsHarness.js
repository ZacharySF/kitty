// Browser fixture for tests/rikaSettings.browser.test.mjs. Mounts the real Settings view
// (with its Rika pages) and the real Rika UI; the test serves /api/* with a scripted stand-in.
import { mount } from "svelte";
import "@nunera/bracket/styles.css";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { applySavedTheme, setTheme } from "/src/lib/themes/index.ts";
import { rika } from "/src/lib/rika/session.svelte.ts";
import RikaSettingsHarness from "./RikaSettingsHarness.svelte";

await applySavedTheme();
const navigations = [];
mount(RikaSettingsHarness, { target: document.getElementById("app"), props: { opened: (view) => navigations.push(view) } });
window.rikaSettingsHarness = { navigations, setTheme, connected: () => rika.connected };
