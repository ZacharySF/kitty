<script lang="ts">
  import { notifier } from "./notifications.svelte";
</script>

<!-- The drawer it opens (NotificationCenter) sits outside the top bar, whose glass would otherwise flatten the drawer's own blur. -->
<button
  class="bell"
  data-notification-bell
  aria-label={`Notifications, ${notifier.unread} unread`}
  aria-expanded={notifier.drawer}
  aria-haspopup="dialog"
  title="Notifications"
  onclick={() => (notifier.drawer = !notifier.drawer)}
>
  <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4Zm4 4h4" /></svg>
  {#if notifier.unread}<b>{notifier.unread}</b>{/if}
</button>

<style>
  .bell {
    position: relative;
    display: grid;
    flex-shrink: 0;
    place-items: center;
    width: 32px;
    height: 32px;
    padding: 0;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 40%, transparent);
    color: var(--ink);
    cursor: pointer;
  }
  .bell[aria-expanded="true"] {
    border-color: var(--br-lineHot);
    background: color-mix(in srgb, var(--violet) 16%, var(--solid-panel, #1c1c1c));
  }
  .bell svg {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.6;
    stroke-linejoin: round;
  }
  .bell b {
    position: absolute;
    top: -4px;
    right: -4px;
    display: grid;
    place-items: center;
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    border-radius: 999px;
    background: var(--violet);
    color: var(--solid-void, #141414);
    font: 600 9.5px var(--font-mono);
  }
</style>
