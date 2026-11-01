<script lang="ts" module>
  export type PopoverPlacement = "bottom-start" | "bottom-end" | "top-start" | "top-end";
  /** Spread onto the element that opens the popover; it becomes the anchor. */
  export type PopoverTriggerProps = Record<string | symbol, unknown>;
</script>

<script lang="ts">
  import type { Snippet } from "svelte";
  import { createAttachmentKey } from "svelte/attachments";

  /**
   * Q6/Q8: a small surface anchored to the control that opens it. It renders in the
   * top layer (the Popover API), so clipping or glass ancestors and open dialogs never
   * cover it. Escape closes and returns focus to the trigger; a click outside or
   * tabbing away closes. Anything rendered inside, including nested pickers, counts
   * as inside.
   */
  let {
    open = $bindable(false),
    label,
    role = "dialog",
    placement = "bottom-start",
    focus = "first",
    class: className = "",
    trigger,
    children,
    onopen,
    onclose,
  }: {
    open?: boolean;
    /** Accessible name of the popup. */
    label: string;
    /** `none` leaves the roles to the content (a picker's own listbox). */
    role?: "dialog" | "menu" | "listbox" | "none";
    placement?: PopoverPlacement;
    /** What takes focus on open: the first `[data-autofocus]` or focusable child, the panel, or nothing. */
    focus?: "first" | "panel" | "none";
    class?: string;
    trigger: Snippet<[PopoverTriggerProps]>;
    children: Snippet;
    onopen?: (panel: HTMLElement) => void;
    onclose?: () => void;
  } = $props();

  const id = $props.id();
  const attach = createAttachmentKey();
  const GAP = 6;
  const MARGIN = 8;
  let anchor: HTMLElement | undefined;
  let panel = $state<HTMLElement>();
  let top = $state(0);
  let left = $state(0);

  const triggerProps = $derived<PopoverTriggerProps>({
    "aria-haspopup": role === "none" ? "true" : role,
    "aria-expanded": open,
    "aria-controls": open ? id : undefined,
    onclick: () => (open ? close(false) : (open = true)),
    [attach]: (node: HTMLElement) => {
      anchor = node;
    },
  });

  /** Closes; `refocus` returns focus to the trigger (Escape, a chosen item). */
  export function close(refocus = true) {
    if (!open) return;
    open = false;
    onclose?.();
    if (refocus) anchor?.focus();
  }

  function place() {
    if (!anchor || !panel) return;
    const rect = anchor.getBoundingClientRect();
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    const above = rect.top - GAP - height;
    const below = rect.bottom + GAP;
    let y = placement.startsWith("top") ? above : below;
    if (y === below && below + height > innerHeight - MARGIN && above >= MARGIN) y = above;
    else if (y === above && above < MARGIN && below + height <= innerHeight - MARGIN) y = below;
    const x = placement.endsWith("end") ? rect.right - width : rect.left;
    top = Math.max(MARGIN, Math.min(y, innerHeight - MARGIN - height));
    left = Math.max(MARGIN, Math.min(x, innerWidth - MARGIN - width));
  }

  const FOCUSABLE = "[data-autofocus], input:not([type=hidden]):not(:disabled), textarea:not(:disabled), select:not(:disabled), button:not(:disabled), [href], [tabindex]:not([tabindex='-1'])";

  $effect(() => {
    const node = panel;
    if (!open || !node) return;
    node.showPopover?.();
    place();
    onopen?.(node);
    if (focus === "first") (node.querySelector<HTMLElement>("[data-autofocus]") ?? node.querySelector<HTMLElement>(FOCUSABLE) ?? node).focus({ preventScroll: true });
    else if (focus === "panel") node.focus({ preventScroll: true });
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!node.contains(target) && !anchor?.contains(target)) close(false);
    };
    const reposition = () => place();
    const observer = new ResizeObserver(reposition);
    observer.observe(node);
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      observer.disconnect();
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  });

  function keydown(event: KeyboardEvent) {
    if (event.key !== "Escape") return;
    // Stop here: an enclosing dialog or popover stays open.
    event.preventDefault();
    event.stopPropagation();
    close(true);
  }

  function focusout(event: FocusEvent) {
    const next = event.relatedTarget as Node | null;
    if (next && !panel?.contains(next) && !anchor?.contains(next)) close(false);
  }
</script>

{@render trigger(triggerProps)}
{#if open}
  <div
    bind:this={panel}
    {id}
    class={["kitty-popover", className]}
    popover="manual"
    role={role === "none" ? undefined : role}
    aria-label={role === "none" ? undefined : label}
    tabindex="-1"
    style:top={`${top}px`}
    style:left={`${left}px`}
    onkeydown={keydown}
    onfocusout={focusout}
  >
    {@render children()}
  </div>
{/if}

<style>
  /* Outranks the shared overlay rule's 12px padding; the overlay glass itself is shared. */
  :global(:root body) .kitty-popover {
    position: fixed;
    inset: auto;
    margin: 0;
    max-width: min(420px, calc(100vw - 16px));
    max-height: calc(100dvh - 16px);
    overflow: auto;
    padding: 5px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-menu);
    color: var(--ink);
    font: 400 var(--fs-control) / 1.35 var(--font-ui);
    animation: kitty-popover-in var(--motion-ms, 140ms) ease-out;
  }
  .kitty-popover:focus {
    outline: none;
  }
  @keyframes kitty-popover-in {
    from {
      opacity: 0;
      transform: translateY(-2px);
    }
  }
</style>
