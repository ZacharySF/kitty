<script lang="ts">
  import { Button, Popover } from "@nunera/bracket";
  import { onMount, tick } from "svelte";
  import { currentTheme, setTheme, THEME_EVENT } from "./themes";
  import { getTheme, THEMES } from "./themes/catalog";

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
              data-surface={theme.surface.style}
              style:background={`radial-gradient(60% 90% at 20% 20%, color-mix(in srgb, ${theme.graph[0]} 30%, transparent), transparent 70%), radial-gradient(60% 90% at 85% 80%, color-mix(in srgb, ${theme.graph[2]} 26%, transparent), transparent 70%), ${theme.roles.void}`}
              style:border-color={theme.roles.line}
              style:--panel={theme.roles.panel}
              style:--raised={theme.roles.raised}
              style:--line={theme.roles.line}
              style:--hot={theme.roles.lineHot}
              style:--radius={`${Math.round(theme.surface.radius / 2)}px`}
              aria-hidden="true"
            >
              <span class="preview-panel">
                {#each theme.graph.slice(0, 4) as color}<span style:background={color}></span>{/each}
              </span>
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
  .theme-options :global(.br-btn) { border-color: var(--border-fine, var(--border)); transition: border-color var(--motion-ms, 140ms), background-color var(--motion-ms, 140ms), transform var(--motion-ms, 140ms); }
  .theme-options :global(.br-btn:hover) { border-color: var(--border-active, var(--accent)); transform: translateY(-1px); }
  .theme-choice { display: grid; gap: 7px; width: 100%; }
  .preview { position: relative; display: grid; place-items: center; height: 54px; border: 1px solid; border-radius: 6px; overflow: hidden; }
  .preview-panel { display: flex; align-items: center; gap: 5px; padding: 8px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); }
  .preview-panel > span { width: 10px; height: 10px; border-radius: 50%; }
  /* Each tile shows what the theme's surfaces are made of (lib/themes/contract.ts). */
  [data-surface="glass"] .preview-panel {
    border-color: color-mix(in srgb, var(--hot) 40%, transparent);
    background: linear-gradient(165deg, rgba(255, 255, 255, 0.22), rgba(255, 255, 255, 0.04)), color-mix(in srgb, var(--panel) 40%, transparent);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.55), 0 4px 10px rgba(0, 0, 0, 0.35);
    backdrop-filter: blur(6px) saturate(170%);
    -webkit-backdrop-filter: blur(6px) saturate(170%);
  }
  [data-surface="paper"] .preview-panel { border-color: transparent; background: var(--raised); box-shadow: 0 1px 2px rgba(0, 0, 0, 0.5), 0 6px 12px -6px rgba(0, 0, 0, 0.7); }
  [data-surface="frame"] .preview-panel { box-shadow: inset 0 0 0 2px var(--panel), inset 0 0 0 3px var(--line); }
  .choice-label { display: flex; justify-content: space-between; align-items: center; gap: 6px; font-size: 12px; line-height: 1.3; }
  .selected-mark { color: var(--violet); min-width: 12px; }
  /* The notification bell shares the narrow top bar; the swatch alone still names the theme (aria-label). */
  @media (max-width: 760px) { .trigger-label { display: none; } }
  @media (max-width: 480px) { .choice-label { min-height: 2.6em; } }
  @media (prefers-reduced-motion: reduce) { .theme-options :global(.br-btn) { transition: none; } .theme-options :global(.br-btn:hover) { transform: none; } }
</style>
