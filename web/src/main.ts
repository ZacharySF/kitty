import { mount } from "svelte";
import App from "./App.svelte";
import QuickNoteWindow from "./QuickNoteWindow.svelte";
import { applySavedTheme } from "./lib/themes";
import { applySavedAppearance } from "./lib/appearance";
import "@nunera/bracket/styles.css";
import "@fontsource-variable/inter";
import "@fontsource-variable/fraunces/full.css";
import "@fontsource-variable/fredoka";
import "@fontsource-variable/jetbrains-mono";
// KaTeX's fonts are bundled here, so math (cards, Math, MathLive) never fetches a font.
import "katex/dist/katex.min.css";
import "./styles.css";
import "./lib/themes/engine.css";

applySavedAppearance();

// `#quick-note[/<id>]` is a standalone floating note, not a workspace view.
const quickNote = /^#quick-note(\/|$)/.test(window.location.hash);
if (quickNote) document.documentElement.dataset.surface = "quick-note";
// The saved theme's motif module (a small lazy chunk) is in before the first paint.
void applySavedTheme().finally(() => mount(quickNote ? QuickNoteWindow : App, { target: document.getElementById("app")! }));
