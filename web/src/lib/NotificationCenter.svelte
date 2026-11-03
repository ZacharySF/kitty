<script lang="ts">
  import { notifier } from "./notifications.svelte";
  import { SOURCE_LABEL, type Notice, type Source } from "./notify";

  let { onopen }: { onopen: (notice: Notice) => void } = $props();
  let tab = $state<"all" | "unread">("all");
  let source = $state<Source | null>(null);
  let drawer = $state<HTMLElement>();
  const sources = $derived([...new Set(notifier.visible.map(n => n.source))]);
  const items = $derived(
    [...notifier.visible]
      .reverse()
      .filter(n => (tab === "all" || !notifier.ledger.read.includes(n.key)) && (!source || n.source === source)),
  );

  function open(notice: Notice) {
    notifier.read(notice.key);
    notifier.drawer = false;
    onopen(notice);
  }
  function outside(event: PointerEvent) {
    const target = event.target as Element;
    if (notifier.drawer && !drawer?.contains(target) && !target.closest?.("[data-notification-bell]")) notifier.drawer = false;
  }
  function key(event: KeyboardEvent) {
    if (event.key !== "Escape" || !notifier.drawer) return;
    event.preventDefault();
    notifier.drawer = false;
    document.querySelector<HTMLElement>("[data-notification-bell]")?.focus();
  }
  const when = (notice: Notice) => {
    const due = new Date(notice.due);
    return Number.isNaN(due.getTime()) ? "" : due.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
  };
</script>

<svelte:window onpointerdown={outside} onkeydown={key} />
{#if notifier.drawer}
  <div class="drawer" role="dialog" aria-label="Notification center" bind:this={drawer}>
    <header>
      <h2>Notifications</h2>
      <button class="link" onclick={() => notifier.markAllRead()} disabled={!notifier.unread}>Mark all read</button>
    </header>
    <div class="tabs" role="tablist" aria-label="Show">
      <button role="tab" aria-selected={tab === "all"} onclick={() => (tab = "all")}>All</button>
      <button role="tab" aria-selected={tab === "unread"} onclick={() => (tab = "unread")}>Unread {notifier.unread}</button>
    </div>
    {#if sources.length > 1}
      <div class="sources" role="group" aria-label="Sources">
        <button class="chip" aria-pressed={!source} onclick={() => (source = null)}>All sources</button>
        {#each sources as item (item)}
          <button class="chip" data-source={item} aria-pressed={source === item} onclick={() => (source = item)}>{SOURCE_LABEL[item]}</button>
        {/each}
      </div>
    {/if}
    <ul>
      {#each items as notice (notice.key)}
        {@const unread = !notifier.ledger.read.includes(notice.key)}
        <li class:unread data-source={notice.source}>
          <button class="item" onclick={() => open(notice)}>
            <span class="meta">
              <i class="dot" aria-hidden="true"></i><span>{SOURCE_LABEL[notice.source]}</span>
              {#if notifier.ledger.held.includes(notice.key)}<span class="held">Held</span>{/if}
              <em>{when(notice)}</em>
            </span>
            <strong>{notice.title}</strong>
            <small>{notice.body}{#if unread}<span class="sr-only"> · unread</span>{/if}</small>
          </button>
          <button class="clear" onclick={() => notifier.clear(notice)} aria-label={`${notice.source === "job" ? "Mark done" : "Dismiss"}: ${notice.title}`}
            >{notice.source === "job" ? "Done" : "Dismiss"}</button
          >
        </li>
      {:else}
        <li class="empty">You're all caught up.</li>
      {/each}
    </ul>
    {#if notifier.error}<p class="error" role="status">{notifier.error}</p>{/if}
    <footer>
      <span>Checks while rika! is open</span>
      <button class="link" aria-pressed={notifier.system} onclick={() => notifier.toggleSystem()}
        >{notifier.system ? "System alerts on" : "System alerts off"}</button
      >
    </footer>
  </div>
{/if}

<style>
  .drawer {
    position: fixed;
    top: calc(var(--workbench-top, 56px) + 8px);
    right: 12px;
    z-index: 45;
    display: flex;
    flex-direction: column;
    width: min(340px, calc(100vw - 24px));
    max-height: calc(100vh - var(--workbench-top, 56px) - var(--rika-space, 0px) - 24px);
    border: 1px solid var(--hairline);
    border-radius: 16px;
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 94%, transparent);
    box-shadow: var(--overlay-shadow);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    color: var(--ink);
    animation: slide 240ms cubic-bezier(0.2, 0.8, 0.2, 1);
  }
  @keyframes slide {
    from {
      transform: translateX(24px);
      opacity: 0;
    }
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 14px 6px;
  }
  h2 {
    margin: 0;
    font: 700 17px var(--font-ui);
  }
  .link {
    border: 0;
    background: none;
    color: var(--violet);
    font: 10px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
  }
  .link:disabled {
    color: var(--subtle);
    cursor: default;
  }
  .tabs,
  .sources {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding: 0 12px 8px;
  }
  .sources {
    padding-top: 2px;
  }
  .tabs {
    border-bottom: 1px solid var(--hairline);
  }
  .tabs button,
  .chip {
    padding: 3px 10px;
    border: 1px solid transparent;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font: 500 10px var(--font-mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    cursor: pointer;
  }
  .tabs button[aria-selected="true"],
  .chip[aria-pressed="true"] {
    border-color: var(--hairline);
    background: color-mix(in srgb, var(--ink) 10%, transparent);
    color: var(--ink);
  }
  ul {
    display: grid;
    align-content: start;
    gap: 2px;
    margin: 0;
    padding: 6px;
    flex: 1 1 auto;
    min-height: 0;
    overflow: auto;
    list-style: none;
  }
  li {
    --tone: var(--violet);
    position: relative;
    display: flex;
    align-items: start;
    border-radius: 10px;
  }
  li[data-source="calendar"],
  .chip[data-source="calendar"] {
    --tone: var(--c1, var(--violet));
  }
  li[data-source="job"],
  .chip[data-source="job"] {
    --tone: var(--c4, var(--violet));
  }
  li:hover {
    background: color-mix(in srgb, var(--ink) 6%, transparent);
  }
  li.unread {
    background: color-mix(in srgb, var(--tone) 9%, transparent);
  }
  li.unread::before {
    content: "";
    position: absolute;
    left: 0;
    top: 10px;
    bottom: 10px;
    width: 2px;
    border-radius: 999px;
    background: var(--tone);
  }
  .item {
    display: grid;
    flex: 1;
    gap: 2px;
    min-width: 0;
    padding: 8px 6px 8px 12px;
    border: 0;
    background: transparent;
    color: var(--ink);
    text-align: left;
    cursor: pointer;
  }
  .meta {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--muted);
    font: 9.5px var(--font-mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .meta em {
    margin-left: auto;
    color: var(--subtle);
    font-style: normal;
  }
  .held {
    padding: 0 5px;
    border: 1px dashed var(--hairline);
    border-radius: 999px;
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--tone);
  }
  .item strong {
    overflow: hidden;
    font: 500 13px var(--font-ui);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .item small {
    color: var(--muted);
    font-size: 11.5px;
  }
  .clear {
    align-self: center;
    margin-right: 6px;
    padding: 2px 8px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font: 9.5px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
    opacity: 0;
  }
  li:hover .clear,
  .clear:focus-visible {
    opacity: 1;
  }
  .empty {
    display: block;
    padding: 20px;
    color: var(--muted);
    text-align: center;
  }
  .error {
    margin: 0;
    padding: 4px 14px;
    color: var(--warning);
    font-size: 12px;
  }
  footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 8px 14px 10px;
    border-top: 1px solid var(--hairline);
    color: var(--subtle);
    font: 9.5px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  @media (hover: none) {
    .clear {
      opacity: 1;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .drawer {
      animation: none;
    }
  }
</style>
