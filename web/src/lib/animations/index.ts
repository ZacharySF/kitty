/**
 * The Animations module's payload (a pack: web/vite.config.ts). The base reaches it only
 * through ../motion.ts, which loads it when the module is switched on and calls `start`;
 * a workspace without it loads nothing.
 *
 * What moves, and why (Material's container transform and shared axis, Apple's springs,
 * the FLIP technique, Chrome's motion guide):
 *  - Pages turn. The page you leave slides or shrinks away while the next arrives, along the
 *    top bar's own order (right for a later section, left for an earlier one), grows out of
 *    the widget or row you pressed, or steps back when you go home.
 *  - The marker of where you are slides from the old item to the new one instead of jumping.
 *  - A press leaves a ring where the pointer was, and what you pressed gives and springs back.
 *  - Home's widgets lean a little toward the pointer and carry its light.
 *  - Lists arrive one row after another; dialogs, menus and toasts spring in.
 *  - A mind map's zoom buttons glide (`tween`).
 * Only `transform` and `opacity` are animated, nothing blocks input, and everything stops
 * under Reduce motion (the setting or the system's).
 */
import "./animations.css";
import { turnOf, viewOf, type Turn } from "./turns.ts";

const SPRING = "cubic-bezier(0.22, 1.25, 0.36, 1)";
const GLIDE = "cubic-bezier(0.16, 1, 0.3, 1)";
const LEAVE = "cubic-bezier(0.4, 0, 1, 1)";
const root = document.documentElement;
const still = () => root.dataset.motion === "reduced" || matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Ease-out cubic: fast first, settling at the end. */
const ease = (t: number) => 1 - (1 - t) ** 3;

/** Calls `apply` with values from 0 to 1 over `ms`, eased; returns a function that stops it where it is. */
export function tween(ms: number, apply: (progress: number) => void): () => void {
  const started = performance.now();
  let frame = 0;
  const step = (now: number) => {
    const progress = Math.min(1, (now - started) / ms);
    apply(ease(progress));
    if (progress < 1) frame = requestAnimationFrame(step);
  };
  frame = requestAnimationFrame(step);
  return () => cancelAnimationFrame(frame);
}

const RAILS = 'nav, [role="tablist"], [role="radiogroup"], [role="toolbar"], [role="group"], ul';
const ACTIVE = '[aria-current="page"], [aria-pressed="true"], [aria-selected="true"], [role="radio"][aria-checked="true"]';
/** The strip an active marker belongs to, as something that is the same before and after a page turn. */
function railKey(item: Element): string {
  const rail = item.closest(RAILS);
  if (!rail || item.matches('input, [role="switch"], [role="option"], [role="gridcell"], td, th')) return "";
  const names = [...rail.classList].filter((name) => !name.startsWith("svelte-") && !name.startsWith("anim-")).join(".");
  return `${rail.tagName}.${names}[${rail.getAttribute("aria-label") ?? ""}]`;
}

let stop: (() => void) | null = null;

/** Turn the module's script on (idempotent). */
export function start(): void {
  if (stop) return;
  const undo: (() => void)[] = [];
  const listen = <K extends keyof DocumentEventMap>(type: K, handler: (event: DocumentEventMap[K]) => void, options?: AddEventListenerOptions) => {
    document.addEventListener(type, handler, options);
    undo.push(() => document.removeEventListener(type, handler, options));
  };

  // ── What was true just before a change: where the pointer pressed, where the markers were.
  let press = { x: innerWidth / 2, y: innerHeight / 2, at: 0, inBar: true };
  let marks = new Map<string, DOMRect>();
  let scrolled = window.scrollY;
  const remember = () => {
    marks = new Map();
    for (const item of document.querySelectorAll(ACTIVE)) {
      const key = railKey(item), rect = item.getBoundingClientRect();
      if (key && rect.width && !marks.has(key)) marks.set(key, rect);
    }
    scrolled = window.scrollY;
  };

  // ── A ring where the pointer pressed.
  const PRESSABLE = 'button, a, [role="button"], [role="tab"], [role="radio"], [role="switch"], [role="menuitem"], [role="option"], input[type="checkbox"], summary, .slot, [data-job-id]';
  const ring = (x: number, y: number) => {
    const dot = document.createElement("i");
    dot.className = "anim-ring";
    dot.style.left = `${x}px`;
    dot.style.top = `${y}px`;
    document.body.append(dot);
    dot.animate([{ transform: "translate(-50%, -50%) scale(0.15)", opacity: 0.55 }, { transform: "translate(-50%, -50%) scale(1)", opacity: 0 }], { duration: 520, easing: GLIDE }).finished.finally(() => dot.remove());
  };
  listen("pointerdown", (event) => {
    const target = event.target as Element | null;
    press = { x: event.clientX, y: event.clientY, at: performance.now(), inBar: !!target?.closest?.(".workbench-top, nav, [role='tablist']") };
    remember();
    if (!still() && target?.closest?.(PRESSABLE)) ring(event.clientX, event.clientY);
  }, { capture: true, passive: true });
  listen("keydown", remember, { capture: true, passive: true });

  // ── Home's widgets lean toward the pointer and carry its light.
  let lit: HTMLElement | null = null, frame = 0, last: PointerEvent | null = null;
  const unlight = () => {
    if (!lit) return;
    for (const name of ["--mx", "--my", "--rx", "--ry"]) lit.style.removeProperty(name);
    lit = null;
  };
  listen("pointermove", (event) => {
    last = event;
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (!last || still()) return unlight();
      const card = (last.target as Element | null)?.closest?.<HTMLElement>(".board:not(.editing) .slot") ?? null;
      if (card !== lit) unlight();
      if (!card) return;
      lit = card;
      const rect = card.getBoundingClientRect();
      const px = (last.clientX - rect.left) / rect.width, py = (last.clientY - rect.top) / rect.height;
      card.style.setProperty("--mx", `${(px * 100).toFixed(1)}%`);
      card.style.setProperty("--my", `${(py * 100).toFixed(1)}%`);
      // At most two degrees, and less on tall widgets, so text never smears.
      const reach = Math.min(2, 520 / Math.max(rect.width, rect.height));
      card.style.setProperty("--rx", `${((0.5 - py) * 2 * reach).toFixed(2)}deg`);
      card.style.setProperty("--ry", `${((px - 0.5) * 2 * reach).toFixed(2)}deg`);
    });
  }, { passive: true });
  undo.push(unlight);

  // ── The marker slides from where it was to where it is.
  const slide = (from: DOMRect, to: HTMLElement) => {
    const rail = to.closest<HTMLElement>(RAILS), rect = to.getBoundingClientRect(), style = getComputedStyle(to);
    if (!rail || !rect.width || /rgba?\([^)]*,\s*0\)|transparent/.test(style.backgroundColor)) return;
    if (Math.abs(from.left - rect.left) < 2 && Math.abs(from.top - rect.top) < 2) return;
    if (getComputedStyle(rail).position === "static") rail.classList.add("anim-rail-static");
    rail.classList.add("anim-rail");
    const base = rail.getBoundingClientRect();
    const pill = document.createElement("i");
    pill.className = "anim-pill";
    Object.assign(pill.style, {
      left: `${rect.left - base.left - rail.clientLeft + rail.scrollLeft}px`, top: `${rect.top - base.top - rail.clientTop + rail.scrollTop}px`,
      width: `${rect.width}px`, height: `${rect.height}px`, background: style.backgroundColor, borderRadius: style.borderRadius, boxShadow: style.boxShadow,
    });
    rail.append(pill);
    to.classList.add("anim-bare");
    pill.animate([
      { transform: `translate(${from.left - rect.left}px, ${from.top - rect.top}px) scale(${from.width / rect.width}, ${from.height / rect.height})` },
      { transform: "none" },
    ], { duration: 460, easing: SPRING }).finished.finally(() => {
      pill.remove();
      to.classList.remove("anim-bare");
      rail.classList.remove("anim-rail", "anim-rail-static");
    });
  };
  const follow = (item: Element) => {
    const key = railKey(item), from = key ? marks.get(key) : undefined;
    if (from && item instanceof HTMLElement && !item.closest(".anim-ghost")) {
      marks.delete(key);
      slide(from, item);
    }
  };

  // ── Pages turn: the old one leaves as a ghost while the new one arrives.
  let view = viewOf(location.hash), clear = 0;
  // The page that is open when the module starts arrives too.
  root.dataset.nav = "open";
  clear = window.setTimeout(() => delete root.dataset.nav, 1400);
  const turn = (panel: HTMLElement, removed: Element[]) => {
    const next = viewOf(location.hash);
    if (next === view) return;
    const kind = turnOf(view, next, performance.now() - press.at < 900 && !press.inBar);
    view = next;
    const rect = panel.getBoundingClientRect();
    root.dataset.nav = kind;
    panel.style.setProperty("--nav-origin", `${press.x - rect.left}px ${press.y - rect.top}px`);
    window.clearTimeout(clear);
    clear = window.setTimeout(() => delete root.dataset.nav, 1400);
    // The new page's markers slide from where the old page's were.
    for (const item of panel.querySelectorAll(ACTIVE)) follow(item);
    if (!removed.length) return;
    const ghost = document.createElement("div");
    ghost.className = "anim-ghost";
    ghost.inert = true;
    ghost.setAttribute("aria-hidden", "true");
    // A strip both pages share (the job steps, a section's tabs) stays where it is: the old
    // one is left out of the ghost and the new one does not arrive, so only its marker moves.
    const plain = (item: Element) => [...item.classList].filter((name) => !name.startsWith("anim-")).join(" ");
    const shared = (a: Element, b: Element) => a.tagName === b.tagName && !!plain(a) && plain(a) === plain(b) && a.matches("nav, header");
    const arrived = [...panel.children].filter((child) => !child.classList.contains("anim-ghost"));
    for (const node of removed) {
      const twin = arrived.find((child) => shared(node, child));
      if (twin) {
        twin.classList.add("anim-keep");
        window.setTimeout(() => twin.classList.remove("anim-keep"), 1400);
        continue;
      }
      // A frame would reload and media would play again: leave them out of the ghost.
      node.querySelectorAll("iframe, video, audio").forEach((item) => item.remove());
      if (!node.matches("iframe, video, audio")) ghost.append(node);
    }
    panel.classList.add("anim-host");
    // The ghost lies exactly where the old page lay: where the first arriving piece that is
    // not a kept strip now starts (layout offsets, so the arrival's own transform does not count).
    const first = arrived.find((child): child is HTMLElement => child instanceof HTMLElement && !child.classList.contains("anim-keep"));
    const pad = getComputedStyle(panel);
    ghost.style.top = `${(first ? first.offsetTop : parseFloat(pad.paddingTop)) + window.scrollY - scrolled}px`;
    ghost.style.left = `${first ? first.offsetLeft : parseFloat(pad.paddingLeft)}px`;
    ghost.style.width = `${first ? first.offsetWidth : panel.clientWidth - parseFloat(pad.paddingLeft) - parseFloat(pad.paddingRight)}px`;
    panel.append(ghost);
    const origin = `${press.x - rect.left}px ${press.y - rect.top + (scrolled - window.scrollY)}px`;
    const leave: Record<Turn, Keyframe[]> = {
      forward: [{ transform: "none", opacity: 1 }, { transform: "translateX(-9%) scale(0.97)", opacity: 0 }],
      back: [{ transform: "none", opacity: 1 }, { transform: "translateX(9%) scale(0.97)", opacity: 0 }],
      next: [{ transform: "none", opacity: 1 }, { transform: "translateX(-64px)", opacity: 0 }],
      prev: [{ transform: "none", opacity: 1 }, { transform: "translateX(64px)", opacity: 0 }],
      zoom: [{ transform: "none", opacity: 1, transformOrigin: origin }, { transform: "scale(1.18)", opacity: 0, transformOrigin: origin }],
      home: [{ transform: "none", opacity: 1 }, { transform: "scale(0.9)", opacity: 0 }],
    };
    ghost.animate(leave[kind], { duration: kind === "zoom" || kind === "home" ? 300 : 260, easing: LEAVE, fill: "forwards" }).finished.finally(() => {
      ghost.remove();
      if (!panel.querySelector(":scope > .anim-ghost")) panel.classList.remove("anim-host");
    });
  };

  const observer = new MutationObserver((records) => {
    if (still()) return;
    let panel: HTMLElement | null = null, arrived = false;
    const removed: Element[] = [];
    for (const record of records) {
      const target = record.target as HTMLElement;
      if (record.type === "attributes") {
        // An item that has just become the active one of its strip.
        if (target.matches?.(ACTIVE)) follow(target);
        continue;
      }
      if (!target.matches?.('[data-kitty-panel="view"]') || target.hidden) continue;
      panel = target;
      for (const node of record.removedNodes) if (node instanceof Element && !node.classList.contains("anim-ghost")) removed.push(node);
      for (const node of record.addedNodes) if (node instanceof Element && !node.classList.contains("anim-ghost")) arrived = true;
    }
    if (panel && arrived) turn(panel, removed);
  });
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-current", "aria-pressed", "aria-selected", "aria-checked"] });
  undo.push(() => observer.disconnect(), () => window.clearTimeout(clear), () => cancelAnimationFrame(frame));

  stop = () => {
    for (const run of undo) run();
    document.querySelectorAll(".anim-ghost, .anim-pill, .anim-ring").forEach((item) => item.remove());
    delete root.dataset.nav;
    stop = null;
  };
}

/** Turn the module's script off again. */
export function end(): void {
  stop?.();
}
