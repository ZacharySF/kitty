<script lang="ts">
  import ToastStack from "../ui/ToastStack.svelte";
  import { usageReadout } from "./rika";
  import { rika, RIKA_TOAST_MS } from "./session.svelte";

  /**
   * Q2 D: each Rika reply lands as a toast above the bar in the notification stack's look:
   * the reply text, its tier chip, what it did, and Undo for anything reversible. A reply
   * still being written holds its toast; the drawer keeps everything after it leaves.
   */
  const time = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };
</script>

<ToastStack items={rika.toasts} total={RIKA_TOAST_MS} label="Rika replies" publish="--rika-toasts"
  tone={(toast) => (rika.message(toast.message)?.status === "failed" ? "var(--danger)" : "var(--c1, var(--violet))")}
  hold={(toast) => rika.message(toast.message)?.status === "pending"} ondismiss={(toast) => rika.dismiss(toast.id)}>
  {#snippet toast(toast)}
    {@const reply = rika.message(toast.message)}
    {@const actions = rika.actionsOf(toast.message)}
    {@const asks = rika.pending.filter((approval) => String(approval.message_id) === toast.message)}
    {@const readout = reply ? usageReadout(reply) : null}
    <div class="top">
      <i class="dot" aria-hidden="true"></i>
      <span>Rika</span>
      {#if readout}<span class="rika-chip" data-tier={readout.tier} title={`${readout.label} · ${readout.detail}`}>{readout.label}</span>
      {:else if reply?.tier}<span class="rika-chip" data-tier={reply.tier} title={reply.model}>{reply.tier}</span>{/if}
      <span class="when">{reply ? time(reply.created_at) : ""}</span>
      <button class="x" aria-label="Dismiss Rika reply" onclick={() => rika.dismiss(toast.id)}>×</button>
    </div>
    <p class="reply" class:thinking={!reply?.text}>{reply?.text || "Thinking…"}</p>
    {#each actions as action (action.id)}
      <span class="body did" data-status={action.status}>
        {action.summary}{#if action.status === "undone"}<em>Undone</em>{:else if action.status === "failed"}<em>Failed</em>{/if}
      </span>
    {/each}
    {#if asks.length || actions.some((action) => action.reversible && action.status === "done")}
      <div class="acts">
        {#if asks.length}<button class="pill primary" onclick={() => rika.showDrawer("conversation")}>Review</button>{/if}
        {#each actions.filter((action) => action.reversible && action.status === "done") as action (action.id)}
          <button class="pill" title={action.summary} onclick={() => rika.undo(action.id)}>Undo</button>
        {/each}
      </div>
    {/if}
  {/snippet}
</ToastStack>

<style>
  .reply {
    display: -webkit-box;
    margin: 2px 0 0;
    overflow: hidden;
    color: var(--ink);
    font: 500 13px/1.4 var(--font-ui);
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 4;
    line-clamp: 4;
  }
  .reply.thinking {
    color: var(--muted);
  }
  .did em {
    margin-left: 6px;
    color: var(--subtle);
    font: 500 9.5px var(--font-mono);
    font-style: normal;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
</style>
