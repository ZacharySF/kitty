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

export const DOCK_SELECTOR = '[data-kitty-panel="dock"], .dock-handle, .dock-pill';

export function tutorialSteps(enabled: ModuleId[], dockPresent: boolean): TutorialStep[] {
  const workspace = enabled.includes("workspace");
  return [
    { id: "workspace", title: "A workspace that follows your train of thought.",
      text: "Keep different areas open in tabs. Switch without losing your place, and give the panel you need more room.",
      tip: "Ctrl + T opens a tab · F expands a focused panel · Esc brings it back",
      view: "today", selector: ".workbench-top" },
    { id: "modules", title: "Only the tools you want.",
      text: "Modules add Study & planning, Job search, or Terminal tools. Your selection is saved here and takes effect after you restart kitty. Nothing restarts during this tour.",
      tip: "Turning a module off keeps its data. Settings is always available.",
      view: "settings", selector: '[data-tutorial="modules"]' },
    { id: "providers", title: "AI is optional, and you hold the budget.",
      text: "rika! works without any AI provider. If you want AI help, connect your own OpenRouter key here and set a daily budget. Paid requests are billed to you by the provider, and each one is shown for review before it runs.",
      tip: "No key, no cost. Google sign-in never turns on AI or other paid services.",
      view: "ai", selector: ".ai-panel" },
    { id: "atlas", title: "Your ideas have a place.",
      text: "The Atlas is your home map. Open a section from its node or the list beside it. Search to jump straight to what you need.",
      tip: "Drag to explore · Scroll to zoom · / to search · Ctrl + K to jump anywhere",
      view: "today", selector: ".atlas" },
    { id: "quick-notes", title: "Catch the thought. Keep moving.",
      text: workspace ? "Quick notes are a small place for the thought you don't want to lose. Create a note here; in the desktop app, open a floating note from the tray."
        : "Quick notes live in the Study & planning module. It is off in this session. Enable it here, then restart rika! when you are ready; this tour won't change your modules.",
      tip: "Quick notes stay on this device. No account or cloud upload is needed.",
      view: workspace ? "quick-notes" : "settings", selector: workspace ? '[data-kitty-panel="view"]:not([hidden])' : '[data-tutorial="modules"]', unavailable: !workspace },
    { id: "study", title: "A little focus goes a long way.",
      text: workspace ? "Study brings focus sessions, learning material and review into one place. Start with a focus session, or add your own notes and flashcards. LearnHarness is an optional extension, configured separately."
        : "Focus sessions, notes, flashcards and the optional LearnHarness extension belong to Study & planning. Turn the module on in Settings and restart to explore them.",
      tip: "A Google connection does not configure LearnHarness, AI services or Calendar sync.",
      view: workspace ? "study" : "settings", selector: workspace ? '[data-kitty-panel="view"]:not([hidden])' : '[data-tutorial="modules"]', unavailable: !workspace },
    ...(enabled.includes("terminal") && dockPresent ? [{ id: "terminal", title: "Your terminal, close at hand.",
      text: "The terminal dock keeps your shell at the bottom of the window, beside the work. Use its controls to change its height, float it as a card or tuck it away. This tour never starts a shell or an agent for you.",
      tip: "Ctrl + Shift + F expands a focused terminal panel · Esc returns",
      view: "today" as View, selector: DOCK_SELECTOR }] : []),
  ];
}
