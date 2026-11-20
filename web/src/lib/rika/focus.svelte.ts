import { untrack } from "svelte";
import { useWorkspaceContext } from "../workspaceContext.svelte";
import { focusContext, type RikaContext, type RikaFocus } from "./rika";

export type { RikaFocus } from "./rika";

/**
 * What the owner is looking at, for Rika (sent as `context` with each message, see `currentContext`).
 * App sets `view` on every navigation and clears `item`; the open view reports its item with
 * `useFocus`. `skip` is the side panel's ×: the next message goes without the item, then
 * sharing resumes (and any newly opened item is shared again).
 */
export const rikaFocus = $state<{ view: string; item: RikaFocus | null; skip: boolean }>({ view: "", item: null, skip: false });
let owner: symbol | null = null;

/** A view's open item (null clears it). */
export function setFocus(item: RikaFocus | null): void {
  claim(null, item);
}
function claim(by: symbol | null, item: RikaFocus | null) {
  owner = item ? by : null;
  rikaFocus.item = item;
  rikaFocus.skip = false;
}

/** App, on every navigation: a new view starts with nothing open. */
export function setFocusView(view: string): void {
  if (rikaFocus.view === view) return;
  rikaFocus.view = view;
  claim(null, null);
}

/**
 * In a view's script: reports `item()` while it changes, clears it when the view goes. Inside a
 * hidden tab (App keeps every tab mounted) it waits, and reports once its tab is the active one.
 */
export function useFocus(item: () => RikaFocus | null): void {
  const me = Symbol("rika-focus");
  // The tab (WorkspacePane's context); outside a tab, always active.
  let pane: { readonly active: boolean } | null = null;
  try { pane = useWorkspaceContext(); } catch { pane = null; }
  $effect(() => {
    const value = item();
    if (pane && !pane.active) {
      if (untrack(() => owner) === me) untrack(() => claim(null, null));
      return;
    }
    untrack(() => {
      // A view with nothing open clears only its own item, never a nested view's.
      if (owner === me ? same(rikaFocus.item, value) : !value) return;
      claim(me, value);
    });
  });
  $effect(() => () => {
    if (owner === me) claim(null, null);
  });
}
function same(a: RikaFocus | null, b: RikaFocus | null) {
  return a === b || (!!a && !!b && a.kind === b.kind && a.id === b.id && a.title === b.title && a.detail === b.detail);
}

/** The `context` of the next message, or null for none. Only built-in views are named. */
export function currentContext(known: (view: string) => boolean): RikaContext | null {
  if (!known(rikaFocus.view)) return null;
  return focusContext(rikaFocus.view, rikaFocus.skip ? null : rikaFocus.item);
}
/** After a message goes: the × only ever skips one. */
export function focusSent(): void {
  rikaFocus.skip = false;
}

let opener: ((prompt?: string) => void | Promise<void>) | null = null;
/** App registers how Rika opens beside the page (the side panel, or home's own panel). */
export function onAskRika(open: (prompt?: string) => void | Promise<void>): () => void {
  opener = open;
  return () => { if (opener === open) opener = null; };
}
/**
 * Any view's "Ask Rika": opens the side panel (home and the Rika page show her already) and
 * focuses her field; with a `prompt`, sends it at once, with what is open as its context.
 */
export function askRika(prompt?: string): void {
  void opener?.(prompt?.trim() || undefined);
}
