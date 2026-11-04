<script lang="ts">
  import Popover from "../ui/Popover.svelte";
  import BriefItems from "./BriefItems.svelte";
  import { morningBrief } from "./brief.svelte";
  import { mailLine } from "./goals";

  /**
   * Q15 A: the Rika bar's "Morning brief · N" chip. It opens a small popover above the
   * bar listing today's items; opening it marks the brief seen. Hidden while today has
   * no brief. Ctrl K "Morning brief" opens it too.
   */
  $effect(() => morningBrief.start());
  const brief = $derived(morningBrief.brief);
  const count = $derived(brief?.items.length ?? 0);
  const time = $derived(brief ? new Date(brief.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }) : "");
  const close = () => (morningBrief.open = false);
</script>

{#if brief}
  <Popover bind:open={morningBrief.open} label="Morning brief" placement="top-end" class="rika-brief-popover" onopen={() => void morningBrief.seen()}>
    {#snippet trigger(props)}
      <button type="button" class="brief-chip" class:unseen={!brief.seen}
        aria-label={`Morning brief, ${count} ${count === 1 ? "item" : "items"}${brief.seen ? "" : ", new"}`} title="Today's morning brief" {...props}
        ><span class="sun" aria-hidden="true"></span>Morning brief · {count}</button>
    {/snippet}
    <div class="brief-pop">
      <header>
        <strong>Morning brief</strong>
        <span>{time}</span>
      </header>
      {#if count}
        <BriefItems items={brief.items} onopen={close} />
      {:else}
        <p class="note">Nothing new for your goals today.</p>
      {/if}
      {#if mailLine(brief.mail)}<p class="note">Mail: {mailLine(brief.mail)}</p>{/if}
      {#each brief.notes as note, index (index)}<p class="note">{note}</p>{/each}
      <footer><a href="#goals" onclick={close}>Open Goals</a></footer>
    </div>
  </Popover>
{/if}

<style>
  .brief-chip {
    display: inline-flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 7px;
    min-height: 0;
    padding: 5px 11px;
    border: 1px solid color-mix(in srgb, var(--c2) 50%, var(--hairline));
    border-radius: 999px;
    background: color-mix(in srgb, var(--c2) 12%, transparent);
    color: var(--ink);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.08em;
    white-space: nowrap;
    cursor: pointer;
  }
  .brief-chip:hover,
  .brief-chip[aria-expanded="true"] {
    background: color-mix(in srgb, var(--c2) 24%, transparent);
  }
  .sun {
    width: 8px;
    height: 8px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--c2) 55%, var(--muted));
  }
  .unseen .sun {
    background: var(--c2);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--c2) 25%, transparent);
  }
  /* Outranks the popover's own padding and height (ui/Popover.svelte). */
  :global(html:root body .kitty-popover.rika-brief-popover) {
    width: min(380px, calc(100vw - 16px));
    max-height: min(560px, calc(100dvh - var(--rika-space, 0px) - 24px));
    padding: 10px 12px 12px;
  }
  .brief-pop {
    display: grid;
    gap: 8px;
  }
  header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
  }
  header strong {
    font: 600 13.5px var(--font-ui);
  }
  header span,
  .note {
    color: var(--subtle);
    font-size: 11px;
  }
  .note {
    margin: 0;
  }
  footer {
    display: flex;
    justify-content: flex-end;
    padding-top: 6px;
    border-top: 1px solid var(--hairline);
  }
  footer a {
    color: var(--accent);
    font-size: 12px;
    text-decoration: none;
  }
  footer a:hover {
    color: var(--ink);
  }
</style>
