// The DOM side of one block's contenteditable: read its text and inline marks back
// from what the browser edited, and map the caret to and from text offsets (UTF-16,
// the same offsets the marks use). A <br data-tail> only makes a trailing newline
// visible and is not text.
import { normalizeMarks, type Mark, type MarkType } from "./blockText";

const TAG_MARKS: Record<string, MarkType> = { STRONG: "b", B: "b", EM: "i", I: "i", CODE: "c", A: "a" };

/** The block's text and marks as the browser now shows them. */
export function readEditable(element: HTMLElement): { text: string; marks: Mark[] } {
  let text = "";
  const marks: Mark[] = [];
  const walk = (node: Node, active: Mark[]) => {
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        const value = (child as Text).data;
        for (const mark of active) marks.push({ ...mark, s: text.length, e: text.length + value.length });
        text += value;
      } else if (child instanceof HTMLBRElement) {
        if (!child.hasAttribute("data-tail")) text += "\n";
      } else if (child instanceof HTMLElement) {
        const type = TAG_MARKS[child.tagName];
        const href = child instanceof HTMLAnchorElement ? child.getAttribute("href") ?? undefined : undefined;
        const bold = !type && /^(bold|[6-9]00)$/.test(child.style.fontWeight) ? [{ s: 0, e: 0, t: "b" as const }] : [];
        const italic = !type && child.style.fontStyle === "italic" ? [{ s: 0, e: 0, t: "i" as const }] : [];
        const block = child !== element && /^(DIV|P)$/.test(child.tagName) && text && !text.endsWith("\n");
        if (block) text += "\n";
        walk(child, [...active, ...(type ? [{ s: 0, e: 0, t: type, ...(href ? { href } : {}) }] : []), ...bold, ...italic]);
      }
    }
  };
  walk(element, []);
  return { text, marks: normalizeMarks(marks, text.length) };
}

function offsetOf(element: HTMLElement, node: Node, offset: number): number {
  let total = 0;
  let found = -1;
  const walk = (current: Node): boolean => {
    if (current === node && current.nodeType !== Node.TEXT_NODE) {
      // An offset inside an element counts its first `offset` children.
      for (let index = 0; index < offset && index < current.childNodes.length; index += 1) total += lengthOf(current.childNodes[index]);
      found = total;
      return true;
    }
    if (current.nodeType === Node.TEXT_NODE) {
      if (current === node) {
        found = total + offset;
        return true;
      }
      total += (current as Text).data.length;
      return false;
    }
    if (current instanceof HTMLBRElement) {
      if (!current.hasAttribute("data-tail")) total += 1;
      return false;
    }
    for (const child of current.childNodes) if (walk(child)) return true;
    return false;
  };
  walk(element);
  return found < 0 ? total : found;
}

function lengthOf(node: Node): number {
  if (node.nodeType === Node.TEXT_NODE) return (node as Text).data.length;
  if (node instanceof HTMLBRElement) return node.hasAttribute("data-tail") ? 0 : 1;
  let total = 0;
  for (const child of node.childNodes) total += lengthOf(child);
  return total;
}

/** The selection inside `element` as text offsets, or null when it is elsewhere. */
export function caretOffsets(element: HTMLElement): { start: number; end: number } | null {
  const selection = element.ownerDocument.getSelection();
  if (!selection || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  if (!element.contains(range.startContainer) || !element.contains(range.endContainer)) return null;
  return { start: offsetOf(element, range.startContainer, range.startOffset), end: offsetOf(element, range.endContainer, range.endOffset) };
}

function position(element: HTMLElement, offset: number): { node: Node; offset: number } {
  let remaining = offset;
  let last: { node: Node; offset: number } = { node: element, offset: 0 };
  const walk = (node: Node): { node: Node; offset: number } | null => {
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        const length = (child as Text).data.length;
        if (remaining <= length) return { node: child, offset: remaining };
        remaining -= length;
        last = { node: child, offset: length };
      } else if (child instanceof HTMLBRElement) {
        if (child.hasAttribute("data-tail")) continue;
        const parent = child.parentNode!;
        const index = [...parent.childNodes].indexOf(child);
        if (remaining === 0) return { node: parent, offset: index };
        remaining -= 1;
        last = { node: parent, offset: index + 1 };
      } else {
        const found = walk(child);
        if (found) return found;
      }
    }
    return null;
  };
  return walk(element) ?? last;
}

/** Put the caret (or a selection) at text offsets inside `element`. */
export function setCaret(element: HTMLElement, start: number, end = start): void {
  const selection = element.ownerDocument.getSelection();
  if (!selection) return;
  const range = element.ownerDocument.createRange();
  const from = position(element, start);
  const to = end === start ? from : position(element, end);
  range.setStart(from.node, from.offset);
  range.setEnd(to.node, to.offset);
  selection.removeAllRanges();
  selection.addRange(range);
}

/** Whether the caret sits on the first / last rendered line of `element`. */
export function caretLines(element: HTMLElement): { first: boolean; last: boolean } {
  const selection = element.ownerDocument.getSelection();
  if (!selection?.rangeCount || !element.textContent) return { first: true, last: true };
  const range = selection.getRangeAt(0).cloneRange();
  range.collapse(true);
  let rect = range.getClientRects()[0];
  if (!rect) {
    // A collapsed range at a line start can have no rectangle; measure one character.
    const probe = element.ownerDocument.createRange();
    const from = position(element, Math.min(caretOffsets(element)?.start ?? 0, (element.textContent ?? "").length));
    probe.setStart(from.node, from.offset);
    rect = probe.getBoundingClientRect();
  }
  const box = element.getBoundingClientRect();
  const line = parseFloat(getComputedStyle(element).lineHeight) || 24;
  return { first: rect.top - box.top < line * 0.75, last: box.bottom - rect.bottom < line * 0.75 };
}
