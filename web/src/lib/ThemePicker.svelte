<script lang="ts">
  import { Button, Popover } from "@nunera/bracket";
  import { onMount, tick } from "svelte";
  import { currentTheme, setTheme, THEME_EVENT } from "./themes";
  import { getTheme, THEMES, KITTY_BLOSSOM } from "./themeCatalog";

  let open = $state(false);
  let selected = $state(currentTheme());
  let root = $state<HTMLDivElement>();
  let triggerButton = $state<HTMLButtonElement | HTMLAnchorElement | null>(null);
  const active = $derived(getTheme(selected));

  onMount(() => {
    const sync = () => { selected = currentTheme(); };
    window.addEventListener(THEME_EVENT, sync);
    return () => window.removeEventListener(THEME_EVENT, sync);
  });

  $effect(() => {
    if (open) {
      void tick().then(() => {
        if (open) root?.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]')?.focus();
      });
    }
  });

  function navigate(event: KeyboardEvent) {
    const direction = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1
      : ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 0;
    if (!direction && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const index = THEMES.findIndex(theme => theme.id === selected);
    const next = event.key === "Home" ? 0 : event.key === "End" ? THEMES.length - 1
      : (index + direction + THEMES.length) % THEMES.length;
    setTheme(THEMES[next].id);
    root?.querySelector<HTMLElement>(`[data-theme-choice="${THEMES[next].id}"]`)?.focus();
  }
</script>

<svelte:window onkeydown={event => {
  if (event.key === "Escape" && open) {
    event.preventDefault();
    event.stopPropagation();
    open = false;
    triggerButton?.focus();
  }
}} />

<div
  class="theme-picker"
  bind:this={root}
  onfocusout={event => {
    if (event.relatedTarget instanceof Node && !root?.contains(event.relatedTarget)) open = false;
  }}
>
  <Popover bind:open placement="bottom-end" arrow={false}>
    {#snippet trigger(props)}
      <Button {...props} bind:ref={triggerButton} size="sm" variant="ghost" aria-label={`Theme: ${active.label}`}>
        <span class="active-swatch" style:background={active.roles.structure} aria-hidden="true"></span>
        <span class="trigger-label">Theme</span>
      </Button>
    {/snippet}
    <div class="picker-heading">Theme</div>
    <div class="theme-options" role="radiogroup" tabindex="-1" aria-label="Theme" onkeydown={navigate}>
      {#each THEMES as theme (theme.id)}
        <Button
          role="radio"
          aria-checked={selected === theme.id}
          tabindex={selected === theme.id ? 0 : -1}
          data-theme-choice={theme.id}
          onclick={() => {
            setTheme(theme.id);
            open = false;
            triggerButton?.focus();
          }}
        >
          <span class="theme-choice">
            <span
              class="preview"
              class:glass={theme.motif === "glass"}
              style:background={theme.motif === "glass"
                ? `radial-gradient(60% 90% at 20% 20%, ${theme.graph[0]}, transparent 70%), radial-gradient(60% 90% at 85% 80%, ${theme.graph[2]}, transparent 70%), radial-gradient(50% 80% at 70% 15%, ${theme.graph[1]}, transparent 70%), ${theme.roles.void}`
                : theme.roles.void}
              style:color={theme.roles.structure}
              style:border-color={theme.roles.line}
              aria-hidden="true"
            >
              <span class="preview-panel" style:background={theme.motif === "glass" ? null : theme.roles.panel} style:border-color={theme.motif === "glass" ? null : theme.roles.lineHot}>
                {#each theme.graph.slice(0, 4) as color}<span style:background={color}></span>{/each}
              </span>
              {#if theme.motif === "blossom"}
                <span class="blossom">{KITTY_BLOSSOM}</span>
              {/if}
            </span>
            <span class="choice-label">{theme.label}<span class="selected-mark" aria-hidden="true">{selected === theme.id ? "✓" : ""}</span></span>
          </span>
        </Button>
      {/each}
    </div>
  </Popover>
</div>

<style>
  .theme-picker { display: inline-flex; flex-shrink: 0; }
  .theme-picker :global(.root[data-placement="bottom-end"] > [role="dialog"]) {
    top: calc(100% + 8px);
    bottom: auto;
    right: 0;
    width: min(360px, calc(100vw - 24px));
  }
  .active-swatch { width: 12px; height: 12px; border-radius: 50%; }
  .picker-heading { margin: 2px 4px 12px; font-size: 13px; font-weight: 600; color: var(--ink); }
  .theme-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .theme-options :global(.br-btn) { padding: 7px; min-width: 0; text-align: left; white-space: normal; }
  /* Tiles, not pills: the global pill radius would turn each two-line choice into an oval. */
  :global(:root body) .theme-options :global(.br-btn) { border-radius: 12px; }
  .theme-options :global(.br-btn > .contents) { width: 100%; }
  .theme-options :global(.br-btn[aria-checked="true"]) { border-color: var(--border-active, var(--violet)); background: var(--wash); }
  .theme-options :global(.br-btn) { border-color: var(--border-fine, var(--border)); transition: border-color var(--motion-fast, 140ms), background-color var(--motion-fast, 140ms), transform var(--motion-fast, 140ms); }
  .theme-options :global(.br-btn:hover) { border-color: var(--border-active, var(--accent)); transform: translateY(-1px); }
  .theme-choice { display: grid; gap: 7px; width: 100%; }
  .preview { position: relative; display: grid; place-items: center; height: 54px; border: 1px solid; border-radius: 6px; overflow: hidden; }
  .preview-panel { display: flex; align-items: center; gap: 5px; padding: 8px; border: 1px solid; border-radius: 6px; }
  .preview-panel > span { width: 10px; height: 10px; border-radius: 50%; }
  /* Liquid Glass: a frosted, lit pane over its own aurora. */
  .preview.glass .preview-panel {
    border-color: rgba(255, 255, 255, 0.35);
    border-radius: 9px;
    background: linear-gradient(165deg, rgba(255, 255, 255, 0.28), rgba(255, 255, 255, 0.06));
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7), 0 4px 10px rgba(0, 0, 0, 0.35);
    backdrop-filter: blur(6px) saturate(170%);
    -webkit-backdrop-filter: blur(6px) saturate(170%);
  }
  .blossom { position: absolute; right: 4px; bottom: 3px; margin: 0; font: 5px/1 var(--font-mono); opacity: 0.45; white-space: pre; }
  .choice-label { display: flex; justify-content: space-between; align-items: center; gap: 6px; font-size: 12px; line-height: 1.3; }
  .selected-mark { color: var(--violet); min-width: 12px; }
  @media (max-width: 480px) { .trigger-label { display: none; } .choice-label { min-height: 2.6em; } }
  @media (prefers-reduced-motion: reduce) { .theme-options :global(.br-btn) { transition: none; } .theme-options :global(.br-btn:hover) { transform: none; } }
</style>
