/** Q4 panel fullscreen is CSS-only: never request or exit native fullscreen. */
export const PANEL_ATTRIBUTE = "data-kitty-panel";
/**
 * Terminal views must put data-kitty-terminal on their root, including around
 * their hidden input/textarea. Bare letter shortcuts then belong to the shell.
 * Ctrl+Shift+F toggles panel fullscreen everywhere, typing included; Escape
 * exits it while active. Views offer the same toggle as a visible button.
 */
export const TERMINAL_ATTRIBUTE = "data-kitty-terminal";
export const FULLSCREEN_ATTRIBUTE = "data-panel-fullscreen";

type FocusTarget = {
  tagName: string;
  type?: string;
  isContentEditable?: boolean;
  parentElement: FocusTarget | null;
  hasAttribute(name: string): boolean;
};
function isElement(target: unknown): target is FocusTarget {
  return typeof target === "object" && target !== null && "tagName" in target && "hasAttribute" in target;
}
export type FocusContext = "terminal" | "text" | "other";
const NON_TEXT_INPUTS: Record<string, true> = { checkbox: true, radio: true, button: true, submit: true };
/** Shared focus guard for letter shortcuts. Terminal wins over its textarea. */
export function focusContext(target: unknown): FocusContext {
  if (!isElement(target)) return "other";
  for (let node: FocusTarget | null = target; node; node = node.parentElement)
    if (node.hasAttribute(TERMINAL_ATTRIBUTE)) return "terminal";
  const tag = target.tagName.toLowerCase();
  if (tag === "textarea" || tag === "select" || target.isContentEditable) return "text";
  if (tag === "input" && !NON_TEXT_INPUTS[(target.type || "text").toLowerCase()]) return "text";
  return "other";
}

type PanelEnvironment = {
  root: Pick<HTMLElement, "setAttribute" | "removeAttribute">;
  fallbackPanel(): HTMLElement | null;
  blocked(): boolean;
  scrollPosition(): [number, number];
  scrollTo(x: number, y: number): void;
  activeElement(): unknown;
  changed?(panel: HTMLElement | null): void;
};
export interface PanelFullscreen {
  active(): HTMLElement | null;
  exit(): void;
  /** Toggle the panel holding `from` (a button inside it), or exit the active one. */
  toggle(from: unknown): void;
  keydownCapture(event: KeyboardEvent): void;
}
export function createPanelFullscreen(env: PanelEnvironment): PanelFullscreen {
  let current: HTMLElement | null = null;
  let restoreScroll: [number, number] = [0, 0];
  function exit() {
    if (!current) return;
    current.removeAttribute(FULLSCREEN_ATTRIBUTE);
    env.root.removeAttribute(FULLSCREEN_ATTRIBUTE);
    current = null;
    env.scrollTo(...restoreScroll);
    env.changed?.(null);
  }
  function panelFor(target: unknown): HTMLElement | null {
    for (let node = isElement(target) ? target : null; node; node = node.parentElement)
      if (node.hasAttribute(PANEL_ATTRIBUTE)) return node as HTMLElement;
    return env.fallbackPanel();
  }
  function toggle(from: unknown) {
    if (current) { exit(); return; }
    const panel = panelFor(from);
    if (!panel?.isConnected) return;
    restoreScroll = env.scrollPosition();
    current = panel;
    panel.setAttribute(FULLSCREEN_ATTRIBUTE, "");
    env.root.setAttribute(FULLSCREEN_ATTRIBUTE, "");
    if (!panel.contains(env.activeElement() as Node | null)) panel.focus({ preventScroll: true });
    env.changed?.(panel);
  }
  function keydownCapture(event: KeyboardEvent) {
    if (event.defaultPrevented || event.repeat || event.isComposing || event.altKey || event.metaKey || env.blocked()) return;
    const context = focusContext(event.target);
    const f = event.key.toLowerCase() === "f";
    const chord = event.ctrlKey && event.shiftKey;
    // Bare F never steals typing; the chord works everywhere, editors included.
    const switching = f && (chord || (context === "other" && !event.ctrlKey && !event.shiftKey));
    // While typing, the app's Escape leaves the field first; the next Escape exits.
    const escape = event.key === "Escape" && !event.ctrlKey && !event.shiftKey && current !== null && context !== "text";
    if (!switching && !escape) return;
    if (!(current ?? panelFor(event.target))?.isConnected) return;
    // Own these keys before any view can reinterpret F as a page action.
    event.preventDefault();
    event.stopPropagation();
    toggle(event.target);
  }
  return { active: () => current, exit, toggle, keydownCapture };
}

/** Fired on the window whenever a panel enters or leaves fullscreen. */
export const PANEL_FULLSCREEN_EVENT = "kitty-panel-fullscreen";
let installed: PanelFullscreen | null = null;
/** The visible Full screen buttons call this with themselves; a no-op before the app installs. */
export function togglePanelFullscreen(from: Element) {
  installed?.toggle(from);
}

/** Install once at the app root; return cleanup for Svelte's mount lifecycle. */
export function installPanelFullscreen(win: Window, doc: Document) {
  const panels = createPanelFullscreen({
    root: doc.documentElement,
    fallbackPanel: () => doc.querySelector<HTMLElement>(`[${PANEL_ATTRIBUTE}="view"]:not([hidden])`),
    blocked: () => Boolean(doc.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')),
    scrollPosition: () => [win.scrollX, win.scrollY],
    scrollTo: (x, y) => win.scrollTo(x, y),
    activeElement: () => doc.activeElement,
    changed: () => win.dispatchEvent(new Event(PANEL_FULLSCREEN_EVENT)),
  });
  installed = panels;
  // Canvas surfaces are not tab stops. Clicking them focuses their panel so F
  // targets the graph rather than the enclosing workspace view.
  const focusPanel = (event: PointerEvent) => {
    if (focusContext(event.target) !== "other" || !(event.target instanceof Element)) return;
    // A popover (top layer) keeps its own focus; clicking inside one must not close it.
    if (event.target.closest("[popover]")) return;
    if (event.target.closest("button,a,input,summary,[tabindex]") === event.target) return;
    event.target.closest<HTMLElement>(`[${PANEL_ATTRIBUTE}]`)?.focus({ preventScroll: true });
  };
  win.addEventListener("pointerdown", focusPanel);
  win.addEventListener("keydown", panels.keydownCapture, true);
  return {
    panels,
    dispose() {
      win.removeEventListener("keydown", panels.keydownCapture, true);
      win.removeEventListener("pointerdown", focusPanel);
      panels.exit();
      if (installed === panels) installed = null;
    },
  };
}
