<script lang="ts">
  import { onMount } from "svelte";
  import { THEME_EVENT } from "./index";
  import { themeMotif } from "./motif.svelte";

  /**
   * The theme's layers outside any one component (contract.ts): its background art behind
   * every surface, its window frame above everything, and the focus ornament that petal,
   * bracket and glow focus styles draw around the keyboard-focused control (the 2px ring
   * itself is plain CSS on the control). Mounted once at the app root.
   */
  const ORNAMENTED = ["petal", "bracket", "glow"];
  let focusStyle = $state(document.documentElement.dataset.themeFocus ?? "ring");
  let target = $state<HTMLElement | null>(null);
  let box = $state({ left: 0, top: 0, width: 0, height: 0, radius: "0px" });
  const Art = $derived(themeMotif.module?.Art);
  const Frame = $derived(themeMotif.module?.Frame);
  const showFocus = $derived(!!target && ORNAMENTED.includes(focusStyle));

  function place() {
    if (!target?.isConnected) {
      target = null;
      return;
    }
    const rect = target.getBoundingClientRect();
    box = { left: rect.left, top: rect.top, width: rect.width, height: rect.height, radius: getComputedStyle(target).borderTopLeftRadius };
  }

  onMount(() => {
    const syncTheme = () => queueMicrotask(() => (focusStyle = document.documentElement.dataset.themeFocus ?? "ring"));
    // Focus events also fire synchronously while Svelte updates the DOM (a focused node is
    // removed, a template focuses a field); state changes wait until that update is done.
    const focusIn = (event: FocusEvent) => {
      const element = event.target;
      // Top-layer dialogs and popovers paint above any page layer; their ring stays plain.
      const shown = element instanceof HTMLElement && element.matches(":focus-visible") && !element.closest("dialog, [popover]");
      queueMicrotask(() => {
        target = shown ? element : null;
        if (shown) place();
      });
    };
    const focusOut = () => queueMicrotask(() => {
      if (!document.activeElement || document.activeElement === document.body) target = null;
    });
    let frame = 0;
    const follow = () => {
      if (!target || frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        place();
      });
    };
    window.addEventListener(THEME_EVENT, syncTheme);
    document.addEventListener("focusin", focusIn);
    document.addEventListener("focusout", focusOut);
    window.addEventListener("scroll", follow, { capture: true, passive: true });
    window.addEventListener("resize", follow);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener(THEME_EVENT, syncTheme);
      document.removeEventListener("focusin", focusIn);
      document.removeEventListener("focusout", focusOut);
      window.removeEventListener("scroll", follow, { capture: true });
      window.removeEventListener("resize", follow);
    };
  });
</script>

{#if Art}
  <div class="theme-art" data-theme-art={themeMotif.id} aria-hidden="true"><Art /></div>
{/if}
{#if Frame}
  <div class="theme-frame" data-theme-frame={themeMotif.id} aria-hidden="true"><Frame /></div>
{/if}
<div
  class="theme-focus"
  data-focus-style={focusStyle}
  hidden={!showFocus}
  aria-hidden="true"
  style:left={`${box.left}px`}
  style:top={`${box.top}px`}
  style:width={`${box.width}px`}
  style:height={`${box.height}px`}
  style:--focus-radius={box.radius}
></div>

<style>
  /* Behind every surface, above the root's backdrop (like PetalDrift). The module's art
     positions itself inside this window-sized box, which is its own compositing layer:
     scrolling, typing and the page's own motion above it never repaint the art. */
  .theme-art {
    position: fixed;
    inset: 0;
    z-index: -1;
    overflow: hidden;
    pointer-events: none;
    contain: strict;
    will-change: transform;
  }
  /* Above everything, edges only, never in the pointer's way; it ends above the Rika bar. */
  .theme-frame {
    position: fixed;
    inset: 0 0 var(--rika-space, 0px);
    z-index: 900;
    pointer-events: none;
  }
  /* The ornament's shape comes from engine.css and the theme's module, per data-focus-style. */
  .theme-focus {
    position: fixed;
    z-index: 1000;
    pointer-events: none;
  }
  .theme-focus[hidden] {
    display: none;
  }
</style>
