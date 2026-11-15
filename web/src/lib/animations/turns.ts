/** Which way a page turns (index.ts). Pure, so Node tests can import it. */
import { navigationGroups } from "../workspace.ts";

export type Turn = "forward" | "back" | "next" | "prev" | "zoom" | "home";
/** Which way a page turns: `from` and `to` are view ids, `pressed` whether a widget, row or link was just pressed. */
export function turnOf(from: string, to: string, pressed: boolean): Turn {
  if (to === "today" || to === "") return "home";
  if (pressed) return "zoom";
  const group = (view: string) => navigationGroups.findIndex((item) => (item.views as string[]).includes(view));
  const a = group(from), b = group(to);
  if (a !== b && a >= 0 && b >= 0) return b > a ? "forward" : "back";
  const views = (navigationGroups[b]?.views ?? []) as string[];
  return views.indexOf(to) >= views.indexOf(from) ? "next" : "prev";
}
/** The view a page address names (`#jobs/12` → jobs). */
export const viewOf = (hash: string) => hash.replace(/^#/, "").split(/[/?]/)[0];
