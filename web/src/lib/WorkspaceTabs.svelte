<script lang="ts">
  import { tick } from "svelte";
  import { Button } from "@nunera/bracket";
  let { tabs, activeId, onselect, onclose, onnew }: {
    tabs: { id: string; label: string }[];
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
      <div class="tab" class:active={tab.id === activeId} role="presentation">
        <button
          id={`workspace-tab-${tab.id}`} role="tab"
          aria-selected={tab.id === activeId} aria-controls={`workspace-pane-${tab.id}`}
          tabindex={tab.id === activeId ? 0 : -1}
          onclick={() => onselect(tab.id)} onkeydown={event => void tabKey(event, index)}
          title={tab.label}
        >{tab.label}</button>
        <button class="close-tab" aria-label={`Close ${tab.label} tab`} title="Close tab" onclick={() => onclose(tab.id)}>×</button>
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
  .workspace-tabbar { display: flex; align-items: center; gap: .35rem; padding: .35rem .5rem 0; min-width: 0; border-bottom: 1px solid var(--border); }
  .tab-list { display: flex; align-items: stretch; gap: .25rem; overflow-x: auto; scrollbar-width: thin; min-width: 0; }
  .tab { display: flex; align-items: center; flex: 0 0 auto; border: 1px solid transparent; border-bottom: 0; border-radius: 6px 6px 0 0; color: var(--muted); }
  .tab.active { color: var(--ink); background: var(--surface-raised); border-color: var(--border); box-shadow: inset 0 2px var(--accent); }
  .tab > button { appearance: none; border: 0; background: none; color: inherit; font: inherit; font-size: .8rem; padding: .55rem .7rem; cursor: pointer; }
  .tab > button[role="tab"] { max-width: 13rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tab > .close-tab { padding: .35rem .45rem; margin-right: .2rem; font-size: 1rem; border-radius: 4px; }
  .tab > button:hover, .tab > button:focus-visible { color: var(--ink); background: var(--wash); outline-color: var(--accent); }
  .tab-menu { position: fixed; z-index: 1000; min-width: 190px; padding: .3rem; border: 1px solid var(--border); border-radius: 7px; background: var(--surface-raised); box-shadow: var(--overlay-shadow); }
  .tab-menu button { display: flex; align-items: center; justify-content: space-between; gap: 1rem; width: 100%; border: 0; border-radius: 4px; background: transparent; color: var(--ink); padding: .6rem .7rem; font: inherit; font-size: .85rem; cursor: pointer; }
  .tab-menu button:hover, .tab-menu button:focus-visible { background: var(--wash); outline-color: var(--accent); }
</style>
