/**
 * Page edit mode: a view's saved moves put on its blocks as inline styles, and taken off
 * the blocks whose move is gone. `translate` composes with a block's own transform; a size
 * comes with what makes flex and grid children obey it.
 */
import type { Move } from "./moves";
import { resolvePath } from "./paths";

/** On every block this applier styled, so a removed move can be undone. */
const MARK = "data-page-move";
const SIZED = ["box-sizing", "flex", "max-width", "min-width", "max-height", "min-height"] as const;

function put(style: CSSStyleDeclaration, name: string, value: string | null) {
  if (value === null) style.removeProperty(name);
  else if (style.getPropertyValue(name) !== value) style.setProperty(name, value);
}

export function applyMoves(root: HTMLElement, moves: readonly Move[]): void {
  const wanted = new Map<HTMLElement, Move>();
  for (const move of moves) {
    const block = resolvePath<HTMLElement>(root, move.path);
    if (block) wanted.set(block, move);
  }
  for (const block of root.querySelectorAll<HTMLElement>(`[${MARK}]`)) {
    if (wanted.has(block)) continue;
    for (const name of ["translate", "width", "height", ...SIZED]) block.style.removeProperty(name);
    block.removeAttribute(MARK);
  }
  for (const [block, { dx, dy, w, h }] of wanted) {
    const sized = w !== null || h !== null;
    put(block.style, "translate", dx || dy ? `${dx}px ${dy}px` : null);
    put(block.style, "width", w === null ? null : `${w}px`);
    put(block.style, "height", h === null ? null : `${h}px`);
    put(block.style, "box-sizing", sized ? "border-box" : null);
    put(block.style, "flex", sized ? "0 0 auto" : null);
    put(block.style, "max-width", w === null ? null : "none");
    put(block.style, "min-width", w === null ? null : "0");
    put(block.style, "max-height", h === null ? null : "none");
    put(block.style, "min-height", h === null ? null : "0");
    if (!block.hasAttribute(MARK)) block.setAttribute(MARK, "");
  }
}
