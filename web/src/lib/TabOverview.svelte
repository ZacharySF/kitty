<script lang="ts">
  import Icon from "./Icon.svelte";

  /**
   * Ctrl + O: every open tab laid out as a card over the dimmed page, as a tiling compositor's
   * overview does. Arrow keys walk the cards, 1–9 pick one, Enter or a click opens it, and
   * Escape (or a click beside the cards) leaves things as they were.
   */
  type Tab = { id: string; label: string; icon: string };
  let { tabs, activeId, onselect, onclose }: {
    tabs: Tab[];
    activeId: string;
    onselect: (id: string) => void;
    onclose: () => void;
  } = $props();

  let dialog = $state<HTMLDialogElement>();
  $effect(() => {
    const node = dialog;
    if (!node) return;
    node.showModal();
    node.querySelector<HTMLButtonElement>('[aria-current="page"]')?.focus();
    return () => node.close();
  });

  function cards() {
    return [...(dialog?.querySelectorAll<HTMLButtonElement>("[data-tab-card]") ?? [])];
  }
  function key(event: KeyboardEvent) {
    const all = cards();
    const at = all.indexOf(document.activeElement as HTMLButtonElement);
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (step) {
      event.preventDefault();
      all[(Math.max(0, at) + step + all.length) % all.length]?.focus();
    } else if (/^[1-9]$/.test(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const tab = tabs[Number(event.key) - 1];
      if (tab) { event.preventDefault(); onselect(tab.id); }
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "o") {
      // The chord that opened it closes it.
      event.preventDefault();
      onclose();
    }
  }
</script>

<dialog bind:this={dialog} class="tab-overview" aria-label="All tabs"
  oncancel={(event) => { event.preventDefault(); onclose(); }}
  onclick={(event) => { if (event.target === dialog) onclose(); }}
  onkeydown={key}>
  <div class="cards">
    {#each tabs as tab, index (tab.id)}
      <button type="button" class="card" data-tab-card aria-current={tab.id === activeId ? "page" : undefined}
        aria-label={`Go to ${tab.label}`} onclick={() => onselect(tab.id)}>
        <span class="icon"><Icon name={tab.icon} size={34} /></span>
        <span class="name">{tab.label}</span>
        {#if index < 9}<kbd>{index + 1}</kbd>{/if}
      </button>
    {/each}
  </div>
  <p class="hint">Arrows to move · Enter to open · Esc to stay</p>
</dialog>

<style>
  :global(:root body) dialog.tab-overview {
    position: fixed;
    inset: 0;
    width: 100vw;
    height: 100dvh;
    max-width: none;
    max-height: none;
    margin: 0;
    padding: 48px;
    box-sizing: border-box;
    border: 0;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
    color: var(--ink);
  }
  .tab-overview[open] {
    display: grid;
    place-content: center;
    gap: 28px;
  }
  .tab-overview::backdrop {
    background: color-mix(in srgb, var(--canvas, #000) 72%, transparent);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
  }
  .cards {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 22px;
    max-width: min(1200px, calc(100vw - 96px));
  }
  .card {
    position: relative;
    display: grid;
    place-items: center;
    align-content: center;
    gap: 14px;
    width: 220px;
    height: 150px;
    padding: 16px;
    border: 1px solid var(--hairline);
    border-radius: 18px;
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 88%, transparent);
    box-shadow: var(--overlay-shadow);
    color: var(--ink);
    font: 600 15px var(--font-ui);
    cursor: pointer;
    transition: transform 140ms ease, border-color 140ms ease;
  }
  .card:hover,
  .card:focus-visible {
    outline: none;
    border-color: var(--ink);
    transform: translateY(-3px) scale(1.03);
  }
  .card[aria-current="page"] {
    border-color: color-mix(in srgb, var(--accent) 70%, var(--hairline));
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 45%, transparent), var(--overlay-shadow);
  }
  .icon {
    color: var(--muted);
  }
  .name {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  kbd {
    position: absolute;
    top: 10px;
    right: 12px;
    color: var(--subtle);
    font: 500 11px var(--font-ui);
  }
  .hint {
    margin: 0;
    color: var(--muted);
    font-size: 13px;
    text-align: center;
  }
  @media (prefers-reduced-motion: reduce) {
    .card {
      transition: none;
    }
  }
</style>
