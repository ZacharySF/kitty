<script lang="ts">
  import type { Snippet } from "svelte";
  import Icon from "../Icon.svelte";
  import { activeModules } from "../modules.svelte";
  import { externalViewId, type AnyView } from "../workspace";
  import { careerCounts, refreshCareerCounts } from "./counts.svelte";
  import { careerFocus } from "./focus.svelte";
  import { RAIL_GROUPS, RAIL_MORE, countLabel, currentPlace, placeCount, stripDot, type RailPlace } from "./rail";

  /**
   * Career's left rail (Q12 C): every Career page with its live count, the page beside it.
   * `compact` (the Resume Workshop, Q14 A) folds it to a 56px strip of icons with count dots;
   * hovering it, tabbing into it or pressing its toggle opens the full rail over the page.
   * A pane too narrow for both folds it the same way.
   */
  let { view, revision = 0, compact = false, onview, children }: {
    view: AnyView;
    revision?: number;
    compact?: boolean;
    onview: (next: AnyView) => void;
    children: Snippet;
  } = $props();

  let width = $state(0);
  /** Open by the toggle, by a mouse resting on it, or by keyboard focus inside it. */
  let pinned = $state(false);
  let hovered = $state(false);
  let keyboard = $state(false);
  let nav: HTMLElement | undefined = $state();
  const folded = $derived(compact || (width > 0 && width < 880));
  const open = $derived(folded && (pinned || hovered || keyboard));
  const current = $derived(currentPlace(view));
  const counts = $derived(careerCounts.value);
  /** Add-on pages that join Career (an add-on's `group: "jobs"`). */
  const addons = $derived(activeModules.views.filter((item) => item.group === "jobs"));

  // Each Career page drawn, and each change a page reports, reads the counts once more.
  $effect(() => {
    void view;
    void revision;
    void refreshCareerCounts();
  });
  // The rail opens for keyboard focus and a resting mouse; Escape folds it again.
  $effect(() => {
    if (!nav) return;
    const target = nav;
    const enter = (event: PointerEvent) => { if (event.pointerType === "mouse") hovered = true; };
    const leave = () => { hovered = false; };
    const listeners: [string, EventListener][] = [
      ["focusin", focusin as EventListener], ["focusout", focusout as EventListener], ["keydown", keydown as EventListener],
      ["pointerenter", enter as EventListener], ["pointerleave", leave],
    ];
    for (const [name, listener] of listeners) target.addEventListener(name, listener);
    return () => { for (const [name, listener] of listeners) target.removeEventListener(name, listener); };
  });

  function go(event: MouseEvent, next: AnyView, stage?: string) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    pinned = hovered = keyboard = false;
    if (stage) careerFocus.stage = stage;
    onview(next);
  }
  function focusin(event: FocusEvent) {
    if ((event.target as Element | null)?.matches?.(":focus-visible")) keyboard = true;
  }
  function focusout(event: FocusEvent) {
    if (!nav?.contains(event.relatedTarget as Node | null)) keyboard = false;
  }
  function keydown(event: KeyboardEvent) {
    if (event.key !== "Escape" || !open) return;
    event.preventDefault();
    event.stopPropagation();
    pinned = hovered = keyboard = false;
  }
  function outside(event: PointerEvent) {
    if (pinned && !nav?.contains(event.target as Node)) pinned = false;
  }
  /** Pins the rail open (hover and focus open it only while they last), or folds it again. */
  function toggle() {
    if (pinned) pinned = hovered = keyboard = false;
    else pinned = true;
  }
</script>

<svelte:window onpointerdown={outside} />

{#snippet place(item: RailPlace)}
  {@const count = placeCount(item, counts)}
  {@const dot = stripDot(item, counts)}
  <a class="place" href={`#${item.view}`} data-place={item.key} aria-current={current === item.key ? "page" : undefined}
    title={folded && !open ? item.label : undefined} onclick={(event) => go(event, item.view, item.stage)}>
    <span class="glyph"><Icon name={item.icon} size={17} />{#if dot}<span class="dot" data-kind={dot} aria-hidden="true">{dot === "number" ? countLabel(count!.value, true) : ""}</span>{/if}</span>
    <span class="label">{item.label}</span>
    {#if count && count.value > 0}<span class="count" class:urgent={count.urgent}>{countLabel(count.value)}</span>{/if}
  </a>
{/snippet}

<div class="career-shell" class:folded bind:clientWidth={width}>
  <div class="rail-slot">
    <nav class="career-rail" class:strip={folded && !open} aria-label="Career" data-open={open} bind:this={nav}>
      {#if folded}
        <button type="button" class="toggle" aria-expanded={open} aria-label={pinned ? "Close the Career menu" : "Open the Career menu"}
          title={pinned ? "Close the Career menu" : "Open the Career menu"} onclick={toggle}>
          <Icon name={pinned ? "collapse" : "expand"} size={15} />
        </button>
      {/if}
      {#each RAIL_GROUPS as group (group.label)}
        <div class="group">
          {#if group.label}<span class="group-label">{group.label}</span>{/if}
          {#each group.places as item (item.key)}{@render place(item)}{/each}
        </div>
      {/each}
      <div class="more" role="group" aria-label="More Career pages">
        {#each RAIL_MORE as item (item.key)}
          {@const count = placeCount(item, counts)}
          <a href={`#${item.view}`} data-place={item.key} aria-current={current === item.key ? "page" : undefined}
            onclick={(event) => go(event, item.view)}>{item.label}{#if count && count.value > 0}<span class="count" class:urgent={count.urgent}>{countLabel(count.value)}</span>{/if}</a>
        {/each}
        {#each addons as item (externalViewId(item))}
          {@const id = externalViewId(item)}
          <a href={`#${id}`} aria-current={view === id ? "page" : undefined} onclick={(event) => go(event, id)}>{item.label}</a>
        {/each}
      </div>
    </nav>
  </div>
  <div class="career-page">{@render children()}</div>
</div>

<style>
  .career-shell {
    display: grid;
    grid-template-columns: 196px minmax(0, 1fr);
    gap: 22px;
    align-items: start;
  }
  .career-shell.folded {
    grid-template-columns: 56px minmax(0, 1fr);
    gap: 14px;
  }
  .career-page {
    min-width: 0;
  }
  .rail-slot {
    position: sticky;
    top: calc(var(--workbench-top, 0px) + 12px);
    z-index: 6;
    margin-top: 14px;
  }
  .career-rail {
    display: grid;
    gap: 12px;
    box-sizing: border-box;
    width: 196px;
    max-height: calc(100dvh - var(--workbench-top, 0px) - 24px);
    overflow-y: auto;
    padding: 2px 0 8px;
    scrollbar-width: thin;
  }
  .group {
    display: grid;
    gap: 1px;
  }
  .group-label {
    padding: 0 10px 4px;
    color: var(--subtle);
    font: 600 var(--fs-label, 11px) / 1.3 var(--font-ui);
  }
  .place {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 34px;
    padding: 0 10px;
    border-radius: var(--r-item, 8px);
    color: var(--muted);
    font: 400 14px/1 var(--font-ui);
    text-decoration: none;
    white-space: nowrap;
  }
  .place:hover {
    background: color-mix(in srgb, var(--ink) 5%, transparent);
    color: var(--ink);
  }
  .place[aria-current="page"] {
    background: var(--wash);
    color: var(--ink);
    font-weight: 500;
    box-shadow: inset 2px 0 var(--accent);
  }
  .career-rail a:focus-visible,
  .toggle:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
  .glyph {
    position: relative;
    display: grid;
    flex: none;
    place-items: center;
  }
  .label {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .count {
    color: var(--subtle);
    font: 500 11.5px/1 var(--font-ui);
    font-variant-numeric: tabular-nums;
  }
  .count.urgent {
    min-width: 20px;
    box-sizing: border-box;
    padding: 4px 6px;
    border-radius: 999px;
    background: var(--accent);
    color: var(--on-accent, var(--canvas));
    text-align: center;
  }
  .dot {
    display: none;
  }
  .more {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    padding: 10px 10px 0;
    border-top: 1px solid var(--hairline, var(--border));
  }
  .more a {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    min-height: 22px;
    color: var(--subtle);
    font-size: 12px;
    text-decoration: none;
  }
  .more a:hover,
  .more a[aria-current="page"] {
    color: var(--ink);
  }
  .more a[aria-current="page"] {
    text-decoration: underline;
    text-decoration-color: var(--accent);
    text-underline-offset: 3px;
  }
  .more .count.urgent {
    min-width: 16px;
    padding: 2px 5px;
    font-size: 10.5px;
  }
  .toggle {
    display: grid;
    place-items: center;
    width: 38px;
    height: 30px;
    min-height: 0;
    margin: 0 0 0 auto;
    padding: 0;
    border: 0;
    border-radius: var(--r-item, 8px);
    background: transparent;
    color: var(--subtle);
    cursor: pointer;
  }
  .toggle:hover {
    color: var(--ink);
    background: color-mix(in srgb, var(--ink) 5%, transparent);
  }

  /* Q14 A: folded to a strip of icons; the full rail opens over the page, never pushing it. */
  .folded .career-rail {
    width: 56px;
    gap: 6px;
    padding: 8px 9px;
    overflow-x: hidden;
    border: 1px solid var(--hairline, var(--border));
    border-radius: var(--r-panel);
    background: var(--surface);
  }
  .strip .toggle {
    width: 36px;
    margin: 0;
  }
  .strip .group {
    justify-items: center;
  }
  .strip .group + .group {
    padding-top: 6px;
    border-top: 1px solid var(--hairline, var(--border));
  }
  .strip .group-label,
  .strip .count,
  .strip .more {
    display: none;
  }
  .strip .place {
    justify-content: center;
    width: 36px;
    height: 36px;
    padding: 0;
  }
  .strip .label {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
  .strip .dot {
    position: absolute;
    top: -6px;
    right: -8px;
    display: block;
    box-shadow: 0 0 0 2px var(--surface);
  }
  .strip .dot[data-kind="dot"] {
    top: -2px;
    right: -3px;
    width: 6px;
    height: 6px;
    border-radius: 999px;
    background: var(--subtle);
  }
  .strip .dot[data-kind="number"] {
    min-width: 15px;
    height: 15px;
    box-sizing: border-box;
    padding: 0 4px;
    border-radius: 999px;
    background: var(--accent);
    color: var(--on-accent, var(--canvas));
    font: 650 9.5px/15px var(--font-ui);
    text-align: center;
  }
  .folded .career-rail[data-open="true"] {
    width: 232px;
    padding: 8px;
    border-color: var(--border);
    background: var(--solid-panel, var(--surface-raised));
    box-shadow: var(--overlay-shadow, 0 18px 44px color-mix(in srgb, #000 30%, transparent));
  }
  :root[data-anim="on"]:not([data-motion="reduced"]) .folded .career-rail {
    transition: width 160ms cubic-bezier(0.16, 1, 0.3, 1), box-shadow 160ms ease;
  }
</style>
