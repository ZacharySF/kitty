import type { ModuleId } from "./api";
import type { View } from "./workspace";

export interface AccountState {
  configured: boolean;
  identity: { sub: string; email: string; name: string } | null;
  onboarding: { welcome_completed: boolean; tutorial_completed: boolean };
}
export interface TutorialStep {
  id: string;
  title: string;
  text: string;
  tip: string;
  view: View;
  selector: string;
  unavailable?: boolean;
}

export const RIKA_SELECTOR = '[data-tutorial="rika"]';

export function tutorialSteps(enabled: ModuleId[]): TutorialStep[] {
  const workspace = enabled.includes("workspace");
  const brain = enabled.includes("brain");
  return [
    { id: "workspace", title: "Tabs and panels",
      text: "Keep several areas open in tabs and give the panel you need more room.",
      tip: "Ctrl + T opens a tab. F expands the focused panel, Esc restores it.",
      view: "today", selector: ".workbench-top" },
    { id: "modules", title: "Modules",
      text: brain ? "Turn on only the tools you want. Changes apply after a restart."
        : "Turn on only the tools you want. The Brain, a map of your workspace, downloads from here. Changes apply after a restart.",
      tip: "Turning a module off keeps its data.",
      view: "settings", selector: '[data-tutorial="modules"]' },
    { id: "providers", title: "AI is optional",
      text: "rika! works without AI. To use it, add your own OpenRouter key and a daily budget. Each paid request waits for your review.",
      tip: "No key, no cost.",
      view: "ai", selector: ".ai-panel" },
    { id: "home", title: "Home",
      text: "Open any section from here.",
      tip: "Ctrl + K jumps anywhere.",
      view: "today", selector: '[data-tutorial="home"], [data-kitty-panel="view"]:not([hidden])' },
    { id: "quick-notes", title: "Quick notes",
      text: workspace ? "Somewhere to put a thought before it's gone. In the desktop app, open one from the tray."
        : "Quick notes are part of Study & planning, which is off. Turn it on here and restart.",
      tip: "Notes stay on this device.",
      view: workspace ? "quick-notes" : "settings", selector: workspace ? '[data-kitty-panel="view"]:not([hidden])' : '[data-tutorial="modules"]', unavailable: !workspace },
    { id: "study", title: "Study",
      text: workspace ? "Focus sessions, learning material and flashcards in one place."
        : "Focus sessions and flashcards are part of Study & planning. Turn it on here and restart.",
      tip: "LearnHarness is an optional extension, set up separately.",
      view: workspace ? "study" : "settings", selector: workspace ? '[data-kitty-panel="view"]:not([hidden])' : '[data-tutorial="modules"]', unavailable: !workspace },
    { id: "rika", title: "Rika",
      text: enabled.includes("terminal")
        ? "Type a request in the Rika panel and press Enter. Rika adds tasks and events, opens pages, opens terminals as Tern windows and starts worker agents. Changes can be undone; anything outward waits for your approval."
        : "Type a request in the Rika panel and press Enter. Rika adds tasks and events and opens pages. Changes can be undone; anything outward waits for your approval.",
      tip: "Ctrl + J opens Rika from any page.",
      view: "today", selector: RIKA_SELECTOR },
  ];
}
