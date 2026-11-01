import { mount } from "svelte";
import App from "./App.svelte";
import QuickNoteWindow from "./QuickNoteWindow.svelte";
import { applySavedTheme } from "./lib/themes";
import "@nunera/bracket/styles.css";
import "@fontsource-variable/inter";
import "@fontsource-variable/fraunces/full.css";
import "@fontsource-variable/space-grotesk";
import "@fontsource-variable/newsreader/opsz-italic.css";
import "@fontsource-variable/fredoka";
import "@fontsource-variable/jetbrains-mono";
import "./styles.css";

applySavedTheme();

// `#quick-note[/<id>]` is a standalone floating note, not a workspace view.
const quickNote = /^#quick-note(\/|$)/.test(window.location.hash);
if (quickNote) document.documentElement.dataset.surface = "quick-note";
mount(quickNote ? QuickNoteWindow : App, { target: document.getElementById("app")! });
