/**
 * Page edit mode: a block's stable path from its view panel, kept free of the DOM's globals
 * so Node tests can import it. A path is a chain of child selectors, one step per element:
 * the tag, then `[aria-label="…"]` when the element has one, else its first class that is
 * neither a Svelte hash nor a state, then `:nth-of-type(n)` only where a sibling would
 * otherwise read the same.
 */

/** The part of an element a path reads. */
export type PathNode = {
  readonly tagName: string;
  readonly parentElement: PathNode | null;
  readonly children: ArrayLike<PathNode>;
  readonly classList: ArrayLike<string>;
  getAttribute(name: string): string | null;
};

/** The longest path a build entry holds (kitty-build.schema.json, `ui.pages.moves`). */
export const MAX_PATH = 600;

/** Classes that say how an element is right now, not what it is. */
const STATE_CLASS = /^(active|busy|checked|closed|collapsed|current|disabled|dragged|dragging|editing|empty|expanded|focused|hidden|hover|loading|muted|off|on|open|over|pressed|selected|sized|solo|toned|tone-over|visible|(is|has)-.*)$/;

/** A class that names what the element is, not how it is right now or which build drew it. */
export function isStableClass(name: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_-]*$/.test(name) && !name.startsWith("svelte-") && !STATE_CLASS.test(name);
}

/** A label short and plain enough to sit in a selector and in the build file. */
function usableLabel(label: string | null): label is string {
  return label !== null && /^[ -~]{1,60}$/.test(label) && !/["\\|]/.test(label);
}

/** One element's step, before any `:nth-of-type`. */
function stem(node: PathNode): string {
  const tag = node.tagName.toLowerCase();
  const label = node.getAttribute("aria-label");
  if (usableLabel(label)) return `${tag}[aria-label="${label}"]`;
  const named = Array.from(node.classList).find(isStableClass);
  return named ? `${tag}.${named}` : tag;
}

/** One element's step among its siblings. */
export function stepOf(node: PathNode): string {
  const own = stem(node);
  const siblings = Array.from(node.parentElement?.children ?? []);
  if (!siblings.some((sibling) => sibling !== node && stem(sibling) === own)) return own;
  const sameTag = siblings.filter((sibling) => sibling.tagName === node.tagName);
  return `${own}:nth-of-type(${sameTag.indexOf(node) + 1})`;
}

/** The path from `root` down to `node`; null when `node` is not inside `root` or the path is too long to keep. */
export function pathOf(root: PathNode, node: PathNode): string | null {
  const steps: string[] = [];
  for (let at: PathNode | null = node; at !== root; at = at.parentElement) {
    if (!at) return null;
    steps.unshift(stepOf(at));
  }
  const path = steps.join(" > ");
  return path && path.length <= MAX_PATH ? path : null;
}

/** The element a path names inside `root`, or null (the page changed, or the path is not a selector). */
export function resolvePath<T>(root: { querySelector(selector: string): T | null }, path: string): T | null {
  try { return root.querySelector(`:scope > ${path}`); } catch { return null; }
}
