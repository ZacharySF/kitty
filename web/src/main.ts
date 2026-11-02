import { mount } from "svelte";
import App from "./App.svelte";
import QuickNoteWindow from "./QuickNoteWindow.svelte";
import BuildStatus from "./lib/BuildStatus.svelte";
import { loadBuild, toggleSafe } from "./lib/build.svelte";
import { isSafeShortcut } from "./lib/buildCore";
import { keepLive } from "./lib/live";
import "@nunera/bracket/styles.css";
import "@fontsource-variable/inter";
import "@fontsource-variable/inter-tight";
import "@fontsource-variable/jetbrains-mono";
import "./styles.css";

// Safe mode (Q9) answers before anything else, even inside the terminal or an editor.
window.addEventListener("keydown", (event) => {
  if (!isSafeShortcut(event)) return;
  event.preventDefault();
  event.stopPropagation();
  if (!event.repeat) toggleSafe();
}, true);

async function start() {
  // The build carries the theme and interface preferences: load it before the first paint.
  await loadBuild();
  // Every window stays on the live channel for build pushes.
  keepLive();
  // `#quick-note[/<id>]` is a standalone floating note, not a workspace view.
  const quickNote = /^#quick-note(\/|$)/.test(window.location.hash);
  if (quickNote) document.documentElement.dataset.surface = "quick-note";
  mount(quickNote ? QuickNoteWindow : App, { target: document.getElementById("app")! });
  // Outside #app, so the build's custom CSS (scoped to #app) can never hide it.
  const status = document.createElement("div");
  status.id = "kitty-build-status";
  document.body.append(status);
  mount(BuildStatus, { target: status });
}
void start();
