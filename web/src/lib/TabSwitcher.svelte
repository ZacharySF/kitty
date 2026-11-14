<script lang="ts">
  import { tick } from "svelte";
  import Icon from "./Icon.svelte";
  import StatusDot from "./ui/StatusDot.svelte";
  import type { SaveStatus } from "./workspaceContext.svelte";

  /**
   * The top bar's one tab control (design lab, top bar C): open pages fold into "N tabs ▾".
   * The list switches and closes tabs and opens a new one; below a hairline sit the app's
   * own places that are not in the bar (Settings, notifications, shortcuts).
   * `status` is the view's save state (Q3), drawn as a dot beside the tab's name.
   */
  type Tab = { id: string; label: string; icon: string; status: SaveStatus | null };
  type Extra = { label: string; detail?: string; run: () => void };
  let { tabs, activeId, extras = [], onselect, onclose, onnew }: {
    tabs: Tab[];
    activeId: string;
    extras?: Extra[];
    onselect: (id: string) => void;
    onclose: (id: string) => void;
    onnew: () => void;
  } = $props();
  let open = $state(false);
  let root: HTMLDivElement | undefined = $state();
  let trigger: HTMLButtonElement | undefined = $state();
  const activeStatus = $derived(tabs.find(tab => tab.id === activeId)?.status ?? null);

  export function close(restoreFocus = true) {
    if (!open) return;
    open = false;
    if (restoreFocus) trigger?.focus();
  }
  async function toggle() {
    open = !open;
    if (!open) return;
    await tick();
    root?.querySelector<HTMLButtonElement>(".tab-row[aria-current] .tab-pick")?.focus();
  }
  function items() {
    return [...(root?.querySelectorAll<HTMLButtonElement>(".tab-list button, .tab-actions button") ?? [])];
  }
  function key(event: KeyboardEvent) {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return; }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const all = items().filter(item => !item.classList.contains("tab-close"));
    if (!all.length) return;
    event.preventDefault();
    const at = all.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? all.length - 1
      : (at + (event.key === "ArrowDown" ? 1 : -1) + all.length) % all.length;
    all[next].focus();
  }
  function pick(id: string) {
    close(false);
    onselect(id);
  }
  function run(action: () => void) {
    close(false);
    action();
  }
</script>

<svelte:window onpointerdown={event => { if (open && !root?.contains(event.target as Node)) close(false); }} />
<div class="tab-switcher" bind:this={root} onkeydown={key} role="presentation">
  <button bind:this={trigger} class="tab-trigger" aria-haspopup="dialog" aria-expanded={open}
    aria-label={`${tabs.length} open ${tabs.length === 1 ? "tab" : "tabs"}`} title="Open tabs · Ctrl Tab switches"
    onclick={() => void toggle()}>{tabs.length} {tabs.length === 1 ? "tab" : "tabs"}<span class="chevron" aria-hidden="true">▾</span></button>
  <!-- Q3: the current tab's unsaved or failed state shows beside the switcher, as it did on its tab. -->
  {#if activeStatus && activeStatus.state !== "saved"}<span class="trigger-status"><StatusDot state={activeStatus.state} detail={activeStatus.detail} /></span>{/if}
  {#if open}
    <div class="tab-popup" role="dialog" aria-label="Open tabs">
      <ul class="tab-list">
        {#each tabs as tab (tab.id)}
          <li class="tab-row" aria-current={tab.id === activeId ? "page" : undefined}>
            <button class="tab-pick" id={`workspace-tab-${tab.id}`} onclick={() => pick(tab.id)}
              ><Icon name={tab.icon} size={16} /><span class="tab-name">{tab.label}</span>{#if tab.status}<StatusDot state={tab.status.state} detail={tab.status.detail} />{/if}</button>
            <button class="tab-close" aria-label={`Close ${tab.label} tab`} title="Close tab · Ctrl W" onclick={() => onclose(tab.id)}><Icon name="close" size={14} /></button>
          </li>
        {/each}
      </ul>
      <div class="tab-actions">
        <button onclick={() => run(onnew)}><Icon name="plus" size={16} /><span>New tab</span><span class="detail">Ctrl T</span></button>
        {#if extras.length}<hr />{/if}
        {#each extras as extra}<button onclick={() => run(extra.run)}><span>{extra.label}</span>{#if extra.detail}<span class="detail">{extra.detail}</span>{/if}</button>{/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .tab-switcher { position: relative; display: inline-flex; }
  .tab-trigger {
    display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 12px; border: 0; border-radius: 999px;
    background: transparent; color: var(--muted); font: inherit; font-size: 15px; white-space: nowrap;
  }
  .tab-trigger:hover, .tab-trigger[aria-expanded="true"] { background: color-mix(in srgb, var(--ink) 10%, transparent); color: var(--ink); }
  .chevron { font-size: 11px; }
  .trigger-status { position: absolute; top: 3px; right: 3px; display: flex; pointer-events: none; }
  /* Solid and unblurred: the top bar is the one frosted surface. */
  :global(:root body) .tab-popup {
    position: absolute; top: calc(100% + 8px); right: 0; z-index: 60; width: 320px; max-height: min(70vh, 560px); overflow: auto;
    padding: 6px; border: 1px solid var(--hairline); border-radius: var(--r-menu); background: var(--solid-panel, var(--surface-raised));
    box-shadow: var(--overlay-shadow); backdrop-filter: none; -webkit-backdrop-filter: none;
    font-size: 15px; line-height: 1.3;
  }
  .tab-row { display: flex; align-items: center; border-radius: var(--r-item); color: var(--muted); }
  .tab-row[aria-current] { background: color-mix(in srgb, var(--ink) 10%, transparent); color: var(--ink); }
  .tab-row:hover { color: var(--ink); }
  .tab-popup button { border: 0; background: transparent; color: inherit; font: inherit; cursor: pointer; }
  .tab-pick { display: flex; flex: 1; align-items: center; gap: 10px; min-width: 0; padding: 9px 10px; text-align: left; border-radius: var(--r-item); }
  .tab-name { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tab-close { display: grid; place-items: center; width: 30px; height: 30px; margin-right: 4px; border-radius: 50%; opacity: .55; }
  .tab-actions button { display: flex; align-items: center; gap: 10px; padding: 9px 10px; border-radius: var(--r-item); color: var(--ink); text-align: left; }
  .tab-actions { display: grid; margin-top: 4px; padding-top: 4px; border-top: 1px solid var(--hairline); }
  .detail { margin-left: auto; color: var(--subtle); font-size: 13px; }
  .tab-actions button:hover, .tab-actions button:focus-visible, .tab-pick:focus-visible { background: color-mix(in srgb, var(--ink) 8%, transparent); }
  .tab-actions hr { width: 100%; margin: 4px 0; border: 0; border-top: 1px solid var(--hairline); }
</style>
