<script lang="ts">
  import HomeWidget from "./HomeWidget.svelte";
  import { api } from "./api";
  import type { View } from "./workspace";

  /** The next seven days on home: one column a day, a mark per event. */
  let { id, revision, onview }: { id: string; revision: number; onview: (view: View) => unknown } = $props();
  const DAY = 86_400_000;
  type Event = { start: string; all_day?: boolean };
  let days = $state<{ at: number; count: number }[] | null>(null);
  let error = $state("");
  const midnight = (at: number) => { const day = new Date(at); day.setHours(0, 0, 0, 0); return day.getTime(); };
  const instant = (value: string) => (/^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00`).getTime() : Date.parse(value));
  $effect(() => {
    void revision;
    const start = midnight(Date.now());
    api<{ events: Event[] }>(`/calendar?${new URLSearchParams({
      start: new Date(start).toISOString(),
      end: new Date(start + 7 * DAY).toISOString(),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    })}`)
      .then((result) => {
        const counts = Array.from({ length: 7 }, (_, index) => ({ at: start + index * DAY, count: 0 }));
        for (const event of result.events) {
          const slot = Math.floor((midnight(instant(event.start)) - start) / DAY);
          if (slot >= 0 && slot < 7) counts[slot].count += 1;
        }
        days = counts;
        error = "";
      })
      .catch((cause) => (error = (cause as Error).message));
  });
</script>

<HomeWidget {id} title="This week">
  {#snippet tail()}<button type="button" class="text-button" onclick={() => onview("calendar")}>Calendar</button>{/snippet}
  {#if error}<p class="note" role="alert">{error}</p>
  {:else if days}
    <ol>
      {#each days as day, index (day.at)}
        <li class:today={index === 0} aria-label={`${new Date(day.at).toLocaleDateString([], { weekday: "long" })}: ${day.count} ${day.count === 1 ? "event" : "events"}`}>
          <span class="name">{new Date(day.at).toLocaleDateString([], { weekday: "short" })}</span>
          <strong>{new Date(day.at).getDate()}</strong>
          <span class="marks" aria-hidden="true">{#each { length: Math.min(day.count, 4) } as _}<i></i>{/each}</span>
        </li>
      {/each}
    </ol>
  {/if}
</HomeWidget>

<style>
  ol {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 4px;
    margin: 0;
    padding: 0;
    list-style: none;
    text-align: center;
  }
  li {
    display: grid;
    justify-items: center;
    gap: 2px;
    padding: 6px 0;
    color: var(--muted);
  }
  .name {
    font-size: 13px;
  }
  strong {
    font-size: 19px;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }
  li.today {
    color: var(--ink);
  }
  .marks {
    display: flex;
    gap: 3px;
    height: 5px;
  }
  .marks i {
    width: 5px;
    height: 5px;
    border-radius: 999px;
    background: currentColor;
  }
  .note {
    color: var(--muted);
    font-size: 14px;
  }
</style>
