/** Q4 panel fullscreen is CSS-only: never request or exit native fullscreen. */
export const PANEL_ATTRIBUTE = "data-kitty-panel";
/**
 * Terminal views must put data-kitty-terminal on their root, including around
 * their hidden input/textarea. Bare letter shortcuts then belong to the shell.
 * Ctrl+Shift+F toggles panel fullscreen; Escape exits it while active.
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
};
export interface PanelFullscreen {
  active(): HTMLElement | null;
  exit(): void;
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
  }
  function panelFor(target: unknown): HTMLElement | null {
    for (let node = isElement(target) ? target : null; node; node = node.parentElement)
      if (node.hasAttribute(PANEL_ATTRIBUTE)) return node as HTMLElement;
    return env.fallbackPanel();
  }
  function keydownCapture(event: KeyboardEvent) {
    if (event.defaultPrevented || event.repeat || event.isComposing || event.altKey || event.metaKey || env.blocked()) return;
    const context = focusContext(event.target);
    if (context === "text") return;
    const f = event.key.toLowerCase() === "f";
    const chord = event.ctrlKey && event.shiftKey;
    const toggle = f && (chord || (context === "other" && !event.ctrlKey && !event.shiftKey));
    const escape = event.key === "Escape" && !event.ctrlKey && !event.shiftKey && current !== null;
    if (!toggle && !escape) return;
    const panel = current ?? panelFor(event.target);
    if (!panel?.isConnected) return;
    // Own these keys before any view can reinterpret F as a page action.
    event.preventDefault();
    event.stopPropagation();
    if (current) { exit(); return; }
    restoreScroll = env.scrollPosition();
    current = panel;
    panel.setAttribute(FULLSCREEN_ATTRIBUTE, "");
    env.root.setAttribute(FULLSCREEN_ATTRIBUTE, "");
    if (!panel.contains(env.activeElement() as Node | null)) panel.focus({ preventScroll: true });
  }
  return { active: () => current, exit, keydownCapture };
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
  });
  // Canvas surfaces are not tab stops. Clicking them focuses their panel so F
  // targets the graph rather than the enclosing workspace view.
  const focusPanel = (event: PointerEvent) => {
    if (focusContext(event.target) !== "other" || !(event.target instanceof Element)) return;
    // Controls (and anything inside one, such as a button's label) and popovers keep their own focus.
    if (event.target.closest("button,a,input,select,textarea,summary,label,[popover]")) return;
    if (event.target.closest("[tabindex]") === event.target) return;
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
    },
  };
}
