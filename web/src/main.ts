import { mount } from "svelte";
import App from "./App.svelte";
import { applyTheme, findTheme, type Theme } from "@nunera/bracket/theme";
import "@nunera/bracket/styles.css";
import "./styles.css";

const kittyTheme: Theme = {
  ...findTheme("Deep Void"),
  name: "kitty! · Personal Terminal",
  void: "#09080d",
  panel: "#14121c",
  inset: "#0e0c14",
  raised: "#211d30",
  line: "#42394f",
  lineHot: "#b5a2d3",
  ink: "#e7dfef",
  mute: "#b2a6c0",
  dim: "#a295b2",
  structure: "#beafea",
  signal: "#d9b28c",
  go: "#a9c5aa",
  reference: "#acbdd7",
  caution: "#dec18b",
  alternate: "#d29dab",
  danger: "#efa4ae",
};
applyTheme(kittyTheme, document.documentElement, "comfortable");

mount(App, { target: document.getElementById("app")! });
