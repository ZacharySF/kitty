// Browser fixture for tests/themes.browser.test.mjs. Loads the app's global styles and
// theme engine, then mounts a specimen surface built from the shared controls.
import { mount } from "svelte";
import "@nunera/bracket/styles.css";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { applySavedTheme, setTheme } from "/src/lib/themes/index.ts";
import { applySavedAppearance, setAppearance, DEFAULT_APPEARANCE } from "/src/lib/appearance.ts";
import ThemeHarness from "./ThemeHarness.svelte";

applySavedAppearance();
await applySavedTheme();
mount(ThemeHarness, { target: document.getElementById("app") });
window.themeHarness = { setTheme, setAppearance, DEFAULT_APPEARANCE };
