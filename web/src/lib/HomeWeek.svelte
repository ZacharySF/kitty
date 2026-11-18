<script lang="ts">
  import HomeWidget from "./HomeWidget.svelte";
  import { api } from "./api";
  import { isSlot } from "./calendar/palette";
  import { midnight, upcoming, weekDays, type WeekEvent } from "./home/week";
  import type { View } from "./workspace";

  /**
   * The next seven days on home: one column a day, today on a capsule, with a point per
   * event in its calendar's colour; then the three events that come next.
   */
  let { id, revision, onview }: { id: string; revision: number; onview: (view: View) => unknown } = $props();
  type Event = WeekEvent & { id: string; calendar_id: string };
  let events = $state<Event[] | null>(null);
  let colors = $state(new Map<string, string>());
  let start = $state(midnight(Date.now()));
  let error = $state("");
  $effect(() => {
    void revision;
    const from = midnight(Date.now());
    const to = new Date(from);
    to.setDate(to.getDate() + 7);
    api<{ events: Event[]; calendars: { id: string; color: string }[] }>(`/calendar?${new URLSearchParams({
      start: new Date(from).toISOString(),
      end: to.toISOString(),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    })}`)
      .then((result) => {
        events = result.events;
        // A calendar on one of the theme's six colours takes the matching tile colour; a picked colour stays as it is.
        colors = new Map(result.calendars.map((calendar) => [calendar.id, isSlot(calendar.color) ? `var(--tile-${calendar.color.slice(1)})` : calendar.color]));
        start = from;
        error = "";
      })
      .catch((cause) => (error = (cause as Error).message));
  });
  const days = $derived(events ? weekDays(events, start) : null);
  const next = $derived(events ? upcoming(events, Date.now(), 3) : []);
  /** "3:00 PM" today, "Thu 9:00 AM" later, "Thu" for an all-day event. */
  function when(event: WeekEvent & { at: number }) {
    const day = new Date(event.at).toLocaleDateString([], { weekday: "short" });
    if (event.all_day) return day;
    const time = new Date(event.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return midnight(event.at) === start ? time : `${day} ${time}`;
  }
</script>

<HomeWidget {id} title="This week" icon="week" tint="var(--tile-2)" opens="calendar" onopen={() => onview("calendar")}>
  {#if error}<p class="note" role="alert">{error}</p>
  {:else if days}
    <ol class="days">
      {#each days as day, index (day.at)}
        <li aria-current={index === 0 ? "date" : undefined} aria-label={`${new Date(day.at).toLocaleDateString([], { weekday: "long" })}: ${day.events.length} ${day.events.length === 1 ? "event" : "events"}`}>
          <span class="name">{new Date(day.at).toLocaleDateString([], { weekday: "short" })}</span>
          <strong>{new Date(day.at).getDate()}</strong>
          <span class="marks" aria-hidden="true">{#each day.events.slice(0, 4) as event (event.id)}<i style:--calendar-color={colors.get(event.calendar_id) ?? null}></i>{/each}</span>
        </li>
      {/each}
    </ol>
    {#if next.length}
      <ul class="next">
        {#each next as event (event.id)}
          <li style:--calendar-color={colors.get(event.calendar_id) ?? null}>
            <i aria-hidden="true"></i>
            <span class="title">{event.title}</span>
            <time datetime={new Date(event.at).toISOString()}>{when(event)}</time>
          </li>
        {/each}
      </ul>
    {:else}<p class="note">Nothing scheduled.</p>{/if}
  {/if}
</HomeWidget>

<style>
  .days {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 4px;
    margin: 0;
    padding: 0;
    list-style: none;
    text-align: center;
  }
  .days li {
    display: grid;
    justify-items: center;
    gap: 2px;
    padding: 8px 0;
    border-radius: var(--r-control);
    color: var(--muted);
  }
  .name {
    font-size: 13px;
  }
  strong {
    font-size: 19px;
    font-weight: 500;
    line-height: 1.3;
    font-variant-numeric: tabular-nums;
  }
  /* Today: the whole day stands on a capsule of the ink colour. */
  .days li[aria-current] {
    background: var(--ink);
    color: var(--solid-void, var(--br-void));
  }
  .days li[aria-current] strong {
    font-weight: 600;
  }
  /* A point per event, in its calendar's colour. */
  .marks {
    display: flex;
    gap: 3px;
    height: 6px;
  }
  .marks i {
    width: 6px;
    height: 6px;
    border-radius: var(--r-control);
    background: var(--calendar-color, var(--tile-1));
  }
  /* On a tile taller than the week and its events, the events keep the foot. */
  .next {
    display: grid;
    gap: 8px;
    margin: auto 0 0;
    padding: 14px 0 0;
    list-style: none;
  }
  .next li {
    display: grid;
    grid-template-columns: 3px minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    font-size: 15px;
    line-height: 1.3;
  }
  .next i {
    align-self: stretch;
    border-radius: var(--r-control);
    background: var(--calendar-color, var(--tile-1));
  }
  .title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  time,
  .note {
    color: var(--muted);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }
  .note {
    margin: auto 0 0;
    padding-top: 14px;
  }
</style>
