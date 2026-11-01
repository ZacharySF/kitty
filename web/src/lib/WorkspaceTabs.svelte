<script lang="ts">
  import { tick } from "svelte";
  import { Button } from "@nunera/bracket";
  import Icon from "./Icon.svelte";
  /** `tone` is the tab's group colour (navigationGroups); it tints the icon and the active underline. */
  let { tabs, activeId, onselect, onclose, onnew }: {
    tabs: { id: string; label: string; icon: string; tone: string }[];
    activeId: string;
    onselect: (id: string) => void;
    onclose: (id: string) => void;
    onnew: () => void;
  } = $props();
  let menu = $state<{ x: number; y: number } | null>(null);
  let menuElement: HTMLDivElement | undefined = $state();
  let previousFocus: HTMLElement | null = null;

  export function closeMenu(restoreFocus = true) {
    menu = null;
    if (restoreFocus && previousFocus?.isConnected) previousFocus.focus();
  }
  export function openMenu(event: MouseEvent) {
    event.preventDefault();
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    menu = {
      x: Math.max(8, Math.min(event.clientX || rect.left, window.innerWidth - 200)),
      y: Math.max(8, Math.min(event.clientY || rect.bottom, window.innerHeight - 60)),
    };
    void tick().then(() => menuElement?.querySelector<HTMLButtonElement>("button")?.focus());
  }
  async function tabKey(event: KeyboardEvent, index: number) {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    const id = tabs[next].id;
    onselect(id);
    await tick();
    document.getElementById(`workspace-tab-${id}`)?.focus();
  }
</script>

<svelte:window
  onpointerdown={event => {
    if (menu && event.button !== 2 && !menuElement?.contains(event.target as Node)) closeMenu(false);
  }}
  onkeydown={event => {
    if (menu && event.key === "Escape") { event.preventDefault(); closeMenu(); }
  }}
/>
<nav class="workspace-tabbar" aria-label="Workspace tabs" oncontextmenu={openMenu}>
  <div class="tab-list" role="tablist" aria-label="Open workspaces">
    {#each tabs as tab, index (tab.id)}
      <div class="tab" class:active={tab.id === activeId} role="presentation" style:--tone={tab.tone}>
        <button
          id={`workspace-tab-${tab.id}`} role="tab"
          aria-selected={tab.id === activeId} aria-controls={`workspace-pane-${tab.id}`}
          tabindex={tab.id === activeId ? 0 : -1}
          onclick={() => onselect(tab.id)} onkeydown={event => void tabKey(event, index)}
          title={tab.label}
        ><span class="tab-icon" aria-hidden="true"><Icon name={tab.icon} size={14} /></span><span class="tab-name">{tab.label}</span></button>
        <button class="close-tab" aria-label={`Close ${tab.label} tab`} title="Close tab · Ctrl+W" onclick={() => onclose(tab.id)}>×</button>
      </div>
    {/each}
  </div>
  <Button variant="ghost" size="sm" onclick={onnew} aria-label="New workspace tab" title="New tab · Ctrl+T">+</Button>
</nav>
{#if menu}
  <div bind:this={menuElement} class="tab-menu" role="menu" aria-label="Workspace actions" tabindex="-1" style:left={`${menu.x}px`} style:top={`${menu.y}px`}>
    <button role="menuitem" onclick={() => { closeMenu(false); onnew(); }}>New tab <kbd>Ctrl T</kbd></button>
  </div>
{/if}

<style>
  /* Underline tabs: flat text with a group-coloured icon; the active tab gets a soft glowing underline. */
  .workspace-tabbar { display: flex; align-items: center; gap: .35rem; padding: .25rem .5rem 0; min-width: 0; border-bottom: 1px solid var(--hairline, var(--border)); }
  .tab-list { display: flex; align-items: stretch; gap: 2px; overflow-x: auto; scrollbar-width: thin; min-width: 0; }
  .tab { position: relative; display: flex; align-items: center; flex: 0 0 auto; color: var(--muted); }
  .tab.active { color: var(--ink); }
  .tab.active::after {
    content: ""; position: absolute; left: 8px; right: 8px; bottom: -1px; height: 2px; border-radius: 2px 2px 0 0;
    background: var(--tone); box-shadow: 0 0 10px color-mix(in srgb, var(--tone) 60%, transparent);
  }
  .tab > button { appearance: none; border: 0; background: none; color: inherit; cursor: pointer; }
  .tab > button[role="tab"] { display: inline-flex; align-items: center; gap: 7px; max-width: 14rem; padding: 8px 4px 9px 10px; font: 500 12.5px var(--heading); }
  .tab-icon { display: inline-grid; place-items: center; color: var(--tone); opacity: .8; }
  .tab.active .tab-icon { opacity: 1; }
  .tab-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tab > .close-tab { visibility: hidden; padding: .2rem .4rem; margin-right: 2px; font-size: 1rem; line-height: 1; border-radius: 6px; }
  .tab:hover > .close-tab, .tab.active > .close-tab, .tab:focus-within > .close-tab { visibility: visible; }
  .tab > button:hover, .tab > button:focus-visible { color: var(--ink); }
  .tab > .close-tab:hover, .tab > .close-tab:focus-visible { background: var(--wash); }
  .tab > button:focus-visible { outline-color: var(--accent); }
  .tab-menu { position: fixed; z-index: 1000; min-width: 190px; padding: .3rem; border: 1px solid var(--border); border-radius: 7px; background: var(--surface-raised); box-shadow: var(--overlay-shadow); }
  .tab-menu button { display: flex; align-items: center; justify-content: space-between; gap: 1rem; width: 100%; border: 0; border-radius: 4px; background: transparent; color: var(--ink); padding: .6rem .7rem; font: inherit; font-size: .85rem; cursor: pointer; }
  .tab-menu button:hover, .tab-menu button:focus-visible { background: var(--wash); outline-color: var(--accent); }
</style>
