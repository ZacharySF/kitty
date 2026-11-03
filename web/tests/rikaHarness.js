// Browser fixture for tests/rika.browser.test.mjs. Mounts the real Rika bar, toasts and
// history drawer; the test serves /api/rika/* and the stream with a scripted stand-in.
import { mount } from "svelte";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { applySavedTheme } from "/src/lib/themes/index.ts";
import Rika from "/src/lib/rika/Rika.svelte";
import { rika } from "/src/lib/rika/session.svelte.ts";

applySavedTheme();
const navigations = [];
mount(Rika, { target: document.getElementById("rika"), props: { onnavigate: (view) => navigations.push(view) } });
window.rikaHarness = { navigations, connected: () => rika.connected };
