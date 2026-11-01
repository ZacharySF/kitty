// The block editor's one keyboard model: the same keys act on the caret while typing
// in a block ("text" mode) and on whole blocks after Esc selects them ("blocks" mode).
// Keys typed during IME composition always belong to the input method.
export type KeyLike = { key: string; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean; altKey: boolean; isComposing: boolean };
export type TextContext = {
  mode: "text";
  /** Caret (collapsed) or selection start at offset 0 / selection end at the end. */
  atStart: boolean;
  atEnd: boolean;
  collapsed: boolean;
  /** The caret is on the block's first / last visual line. */
  firstLine: boolean;
  lastLine: boolean;
  type: string;
};
export type KeyContext = TextContext | { mode: "blocks" };
export type Command =
  | "split" | "newline" | "mergeBackward" | "mergeForward" | "indent" | "outdent" | "selectBlock"
  | "focusPrevious" | "focusNext" | "focusPreviousEnd" | "focusNextStart" | "extendUp" | "extendDown"
  | "moveUp" | "moveDown" | "bold" | "italic" | "code" | "link"
  | "selectNext" | "selectPrevious" | "edit" | "deleteBlocks" | "clearSelection" | "selectAll";

const MARKS: Record<string, Command> = { b: "bold", i: "italic", e: "code", k: "link" };

export function keyCommand(event: KeyLike, context: KeyContext): Command | null {
  if (event.isComposing || event.key === "Process") return null;
  const mod = event.ctrlKey || event.metaKey;
  const plain = !mod && !event.altKey;
  if (mod && event.shiftKey && !event.altKey && event.key === "ArrowUp") return "moveUp";
  if (mod && event.shiftKey && !event.altKey && event.key === "ArrowDown") return "moveDown";
  if (event.key === "Tab" && plain) return event.shiftKey ? "outdent" : "indent";
  if (context.mode === "blocks") {
    if (plain && event.key === "ArrowDown") return event.shiftKey ? "extendDown" : "selectNext";
    if (plain && event.key === "ArrowUp") return event.shiftKey ? "extendUp" : "selectPrevious";
    if (plain && !event.shiftKey && event.key === "Enter") return "edit";
    if (plain && (event.key === "Backspace" || event.key === "Delete")) return "deleteBlocks";
    if (event.key === "Escape") return "clearSelection";
    if (mod && !event.shiftKey && event.key.toLowerCase() === "a") return "selectAll";
    return null;
  }
  if (event.key === "Enter") {
    if (event.altKey) return null;
    if (context.type === "code") return mod ? "split" : event.shiftKey ? null : "newline";
    if (event.shiftKey) return "newline";
    return mod ? null : "split";
  }
  if (event.key === "Escape") return "selectBlock";
  if (mod && !event.shiftKey && !event.altKey && Object.hasOwn(MARKS, event.key.toLowerCase())) return MARKS[event.key.toLowerCase()];
  if (!plain) return null;
  const caret = context.collapsed;
  if (event.key === "Backspace" && caret && context.atStart && !event.shiftKey) return "mergeBackward";
  if (event.key === "Delete" && caret && context.atEnd && !event.shiftKey) return "mergeForward";
  if (event.key === "ArrowUp" && context.firstLine) return event.shiftKey ? (context.atStart ? "extendUp" : null) : "focusPrevious";
  if (event.key === "ArrowDown" && context.lastLine) return event.shiftKey ? (context.atEnd ? "extendDown" : null) : "focusNext";
  if (event.key === "ArrowLeft" && caret && context.atStart && !event.shiftKey) return "focusPreviousEnd";
  if (event.key === "ArrowRight" && caret && context.atEnd && !event.shiftKey) return "focusNextStart";
  return null;
}
