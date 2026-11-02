// Browser fixture for tests/themes.browser.test.mjs. Loads the app's global styles and
// theme engine, then mounts a specimen surface built from the shared controls.
import { mount } from "svelte";
import "@nunera/bracket/styles.css";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { setTheme } from "/src/lib/build.svelte.ts";
import { setAppearance, DEFAULT_APPEARANCE } from "/src/lib/appearance.ts";
import { installBuild } from "./buildStub.js";
import ThemeHarness from "./ThemeHarness.svelte";

await installBuild();
mount(ThemeHarness, { target: document.getElementById("app") });
window.themeHarness = { setTheme, setAppearance, DEFAULT_APPEARANCE };
