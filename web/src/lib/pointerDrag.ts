/** Pointer drags onto drop zones marked with `data-drop="<zone>"`. */

export type DragHandlers = {
  /** The pointer moved past the threshold; the drag is live. */
  start?: (x: number, y: number) => void;
  /** Client coordinates plus the `data-drop` zone under the pointer. */
  move?: (x: number, y: number, zone: string | null) => void;
  drop: (zone: string | null, x: number, y: number) => void;
  /** Released without dragging. */
  click?: () => void;
  /** Escape during a live drag. */
  cancel?: () => void;
};

function zoneAt(x: number, y: number): string | null {
  return document.elementFromPoint(x, y)?.closest("[data-drop]")?.getAttribute("data-drop") ?? null;
}

/** Follows one pointer press. Drag ghosts must be `pointer-events: none` so zones stay hit-testable. */
export function beginDrag(event: PointerEvent, handlers: DragHandlers): void {
  if (event.button !== 0) return;
  const sx = event.clientX;
  const sy = event.clientY;
  let active = false;
  const move = (ev: PointerEvent) => {
    if (!active) {
      if (Math.hypot(ev.clientX - sx, ev.clientY - sy) < 5) return;
      active = true;
      handlers.start?.(ev.clientX, ev.clientY);
    }
    ev.preventDefault();
    handlers.move?.(ev.clientX, ev.clientY, zoneAt(ev.clientX, ev.clientY));
  };
  const up = (ev: PointerEvent) => {
    cleanup();
    if (active) handlers.drop(zoneAt(ev.clientX, ev.clientY), ev.clientX, ev.clientY);
    else handlers.click?.();
  };
  const key = (ev: KeyboardEvent) => {
    if (ev.key !== "Escape") return;
    ev.preventDefault();
    ev.stopPropagation();
    cleanup();
    if (active) handlers.cancel?.();
  };
  const cleanup = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    window.removeEventListener("keydown", key, true);
  };
  window.addEventListener("pointermove", move, { passive: false });
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
  window.addEventListener("keydown", key, true);
}
