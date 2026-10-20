import { mount } from "svelte";
import App from "./App.svelte";
import { applySavedTheme } from "./lib/themes";
import "@nunera/bracket/styles.css";
import "@fontsource-variable/inter";
import "@fontsource-variable/inter-tight";
import "@fontsource-variable/jetbrains-mono";
import "./styles.css";

applySavedTheme();

mount(App, { target: document.getElementById("app")! });
