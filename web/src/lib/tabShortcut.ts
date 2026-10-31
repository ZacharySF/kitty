type ShortcutKey = Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey" | "defaultPrevented">;

/** Ctrl+W (Cmd+W on macOS) closes the active workspace tab unless a modal owns the keyboard. */
export function isCloseTabShortcut(event: ShortcutKey, modalOpen: boolean): boolean {
  return !event.defaultPrevented && !modalOpen && (event.ctrlKey || event.metaKey) &&
    !event.altKey && !event.shiftKey && event.key.toLowerCase() === "w";
}
