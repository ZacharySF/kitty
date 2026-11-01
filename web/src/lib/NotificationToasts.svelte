<script lang="ts">
  import { onMount } from "svelte";
  import { notifier, TOAST_MS, type Toast } from "./notifications.svelte";
  import { SOURCE_LABEL, digestLine, type Notice } from "./notify";

  let { onopen }: { onopen: (notice: Notice) => void } = $props();
  /** Pointer or keyboard focus inside the stack fans it out and pauses every countdown. */
  let hovered = $state(false);
  let focused = $state(false);
  const paused = $derived(hovered || focused);
  const TICK = 100;

  onMount(() => {
    const timer = setInterval(() => {
      if (paused || document.hidden) return;
      for (const toast of notifier.toasts) toast.left -= TICK;
      const done = notifier.toasts.filter(toast => toast.left <= 0);
      for (const toast of done) notifier.dismiss(toast.id);
    }, TICK);
    return () => clearInterval(timer);
  });

  function open(toast: Toast) {
    notifier.dismiss(toast.id);
    if (toast.notice) {
      notifier.read(toast.notice.key);
      onopen(toast.notice);
    } else notifier.drawer = true;
  }
  const when = (notice: Notice) => {
    const due = new Date(notice.due);
    return Number.isNaN(due.getTime()) ? "" : due.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };
</script>

{#if notifier.toasts.length}
  <ol
    class="toasts"
    class:spread={paused}
    aria-label="New notifications"
    aria-live="polite"
    onpointerenter={() => (hovered = true)}
    onpointerleave={() => (hovered = false)}
    onfocusin={() => (focused = true)}
    onfocusout={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) focused = false; }}
  >
    {#each notifier.toasts as toast, index (toast.id)}
      {@const depth = notifier.toasts.length - 1 - index}
      {@const source = toast.notice?.source}
      <li class="toast" style:--d={depth} data-source={source ?? "digest"}>
        <div class="top">
          <i class="dot" aria-hidden="true"></i>
          <span>{source ? SOURCE_LABEL[source] : "Quiet hours"}</span>
          {#if toast.notice}<span class="when">{when(toast.notice)}</span>{/if}
          <button class="x" aria-label="Dismiss notification" onclick={() => notifier.dismiss(toast.id)}>×</button>
        </div>
        {#if toast.notice}
          <strong>{toast.notice.title}</strong>
          <span class="body">{toast.notice.body}</span>
        {:else}
          <strong>Morning digest</strong>
          <span class="body">Held during quiet hours: {digestLine(toast.digest ?? [])}</span>
        {/if}
        <div class="acts">
          {#if toast.notice}
            <button class="pill" onclick={() => toast.notice && notifier.snooze(toast.notice)}
              >{toast.notice.source === "calendar" ? "Snooze 10 min" : "Snooze a day"}</button
            >
          {/if}
          <button class="pill primary" onclick={() => open(toast)}>Open</button>
        </div>
        <span class="life" aria-hidden="true" data-countdown={toast.left} style:--left={toast.left / TOAST_MS}></span>
      </li>
    {/each}
  </ol>
{/if}

<style>
  .toasts {
    position: fixed;
    right: 16px;
    bottom: max(16px, calc(var(--dock-clearance, 0px) + 12px));
    z-index: 40;
    display: grid;
    width: min(320px, calc(100vw - 32px));
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .toast {
    --tone: var(--violet);
    position: relative;
    grid-area: 1 / 1;
    align-self: end;
    display: grid;
    gap: 3px;
    padding: 10px 12px 12px;
    overflow: hidden;
    border: 1px solid var(--hairline);
    border-left: 2px solid var(--tone);
    border-radius: 14px;
    background: color-mix(in srgb, var(--solid-raised) 88%, transparent);
    box-shadow: var(--overlay-shadow);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    transform: translateY(calc(var(--d) * -12px)) scale(calc(1 - var(--d) * 0.05));
    opacity: calc(1 - var(--d) * 0.28);
    z-index: calc(10 - var(--d));
    transition: transform 260ms cubic-bezier(0.2, 0.8, 0.2, 1), opacity 260ms;
    animation: rise 320ms cubic-bezier(0.2, 0.8, 0.2, 1);
  }
  .toast[data-source="calendar"] {
    --tone: var(--c1, var(--violet));
  }
  .toast[data-source="job"] {
    --tone: var(--c4, var(--violet));
  }
  .spread .toast {
    transform: translateY(calc(var(--d) * -100% - var(--d) * 8px));
    opacity: 1;
  }
  @keyframes rise {
    from {
      transform: translateY(24px);
      opacity: 0;
    }
  }
  .top {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--muted);
    font: 9.5px var(--font-mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .when {
    margin-left: auto;
    color: var(--subtle);
  }
  .dot {
    width: 7px;
    height: 7px;
    flex-shrink: 0;
    border-radius: 999px;
    background: var(--tone);
    box-shadow: 0 0 6px color-mix(in srgb, var(--tone) 70%, transparent);
  }
  .x {
    margin-left: 4px;
    padding: 0 2px;
    border: 0;
    background: transparent;
    color: var(--muted);
    font-size: 15px;
    line-height: 1;
    cursor: pointer;
  }
  .top span:not(.when) + .x {
    margin-left: auto;
  }
  strong {
    overflow: hidden;
    color: var(--ink);
    font: 600 13.5px var(--font-ui);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .body {
    color: var(--muted);
    font-size: 12px;
  }
  .acts {
    display: flex;
    justify-content: end;
    gap: 5px;
    margin-top: 4px;
  }
  .acts .pill {
    min-height: 24px;
    padding: 2px 10px;
    font-size: 9.5px;
  }
  .life {
    position: absolute;
    left: 0;
    bottom: 0;
    width: 100%;
    height: 2px;
    background: var(--tone);
    transform: scaleX(var(--left));
    transform-origin: left;
    transition: transform 100ms linear;
  }
  @media (prefers-reduced-motion: reduce) {
    .toast,
    .life {
      transition: none;
      animation: none;
    }
  }
</style>
