<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "./api";
  import HomeWidget from "./HomeWidget.svelte";
  import type { View } from "./workspace";

  /** Q20: today's events with a live now-line, current-event progress and a countdown to the next one. */
  let { id, revision, onview }: { id: string; revision: number; onview: (view: View) => unknown } = $props();

  type Event = { id: string; title: string; start: string; end: string; all_day: boolean; calendar_id: string; location: string };
  type Row = Event & { from: number; to: number };
  let events = $state<Event[] | null>(null);
  let colors = $state(new Map<string, string>());
  let error = $state("");
  let now = $state(Date.now());

  const midnight = (at: number) => {
    const day = new Date(at);
    day.setHours(0, 0, 0, 0);
    return day;
  };
  const today = $derived(midnight(now).getTime());
  const allDay = $derived((events ?? []).filter((event) => event.all_day));
  const timed = $derived(
    (events ?? [])
      .filter((event) => !event.all_day)
      .map((event): Row => ({ ...event, from: Date.parse(event.start), to: Date.parse(event.end) }))
      .sort((a, b) => a.from - b.from),
  );
  /** The now-line sits before the first event that has not started yet. */
  const lineAt = $derived.by(() => {
    const index = timed.findIndex((event) => event.from > now);
    return index < 0 ? timed.length : index;
  });
  const clock = (at: number) =>
    new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  /** Compact countdown: "12m", "1h 30m". */
  function span(ms: number) {
    const total = Math.max(1, Math.round(ms / 60_000));
    return total < 60 ? `${total}m` : `${Math.floor(total / 60)}h${total % 60 ? ` ${total % 60}m` : ""}`;
  }

  let request = 0;
  async function load(day: number) {
    const serial = ++request;
    const end = new Date(day);
    end.setDate(end.getDate() + 1);
    const query = new URLSearchParams({
      start: new Date(day).toISOString(),
      end: end.toISOString(),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    });
    try {
      const data = await api<{ events: Event[]; calendars: { id: string; color: string }[] }>(`/calendar?${query}`);
      if (serial !== request) return;
      events = data.events;
      colors = new Map(data.calendars.map((calendar) => [calendar.id, calendar.color]));
      error = "";
    } catch (cause) {
      if (serial === request) error = (cause as Error).message;
    }
  }
  $effect(() => {
    void revision;
    void load(today);
  });
  onMount(() => {
    const timer = window.setInterval(() => (now = Date.now()), 15_000);
    return () => window.clearInterval(timer);
  });
</script>

<HomeWidget {id} title="Today" eyebrow={new Date(today).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}>
  {#if error}
    <p class="note" role="alert">{error}</p>
  {:else if !events}
    <p class="note" role="status">Reading today's calendar…</p>
  {:else if !events.length}
    <p class="note">Nothing on the calendar today.</p>
    <button class="text-button" onclick={() => onview("calendar")}>Open the calendar</button>
  {:else}
    <ol>
      {#each allDay as event (event.id)}
        <li>
          <button onclick={() => onview("calendar")}>
            <time>all day</time>
            <span class="bar" style:--calendar-color={colors.get(event.calendar_id) || "var(--accent)"}></span>
            <span class="what"><strong>{event.title}</strong>{#if event.location}<small>{event.location}</small>{/if}</span>
          </button>
        </li>
      {/each}
      {#each timed as event, index (event.id)}
        {@const past = event.to <= now}
        {@const live = event.from <= now && now < event.to}
        {#if index === lineAt}
          <li class="now-line" aria-label={`Now, ${clock(now)}`}><span>{clock(now)}</span><i></i></li>
        {/if}
        <li class:past class:live>
          <button onclick={() => onview("calendar")}>
            <time>{clock(event.from)}</time>
            <span class="bar" style:--calendar-color={colors.get(event.calendar_id) || "var(--accent)"}>
              {#if live}
                {@const percent = Math.round(((now - event.from) / (event.to - event.from)) * 100)}
                <span
                  class="progress"
                  role="progressbar"
                  aria-label={`${event.title} progress`}
                  aria-valuemin="0"
                  aria-valuemax="100"
                  aria-valuenow={percent}
                  aria-valuetext={`${span(event.to - now)} left`}
                  style:height={`${percent}%`}
                ></span>
              {/if}
            </span>
            <span class="what">
              <strong>{event.title}</strong>
              <small>{clock(event.from)}–{clock(event.to)}{event.location ? ` · ${event.location}` : ""}</small>
            </span>
            {#if live}<em class="chip now">now · {span(event.to - now)} left</em>{:else if index === lineAt}<em class="chip">in {span(event.from - now)}</em>{/if}
          </button>
        </li>
      {/each}
      {#if lineAt === timed.length && timed.length}
        <li class="now-line" aria-label={`Now, ${clock(now)}`}><span>{clock(now)}</span><i></i></li>
      {/if}
    </ol>
  {/if}
</HomeWidget>

<style>
  ol {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 1px;
  }
  button {
    width: 100%;
    display: grid;
    grid-template-columns: 38px 3px minmax(0, 1fr) auto;
    align-items: center;
    gap: 9px;
    padding: 5px 4px 5px 2px;
    border: 1px solid transparent;
    border-radius: 8px;
    background: transparent;
    color: var(--ink);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  button:hover,
  button:focus-visible {
    background: color-mix(in srgb, var(--ink) 5%, transparent);
  }
  time {
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  .bar {
    position: relative;
    align-self: stretch;
    min-height: 28px;
    border-radius: 2px;
    background: var(--calendar-color);
    opacity: 0.85;
    overflow: hidden;
  }
  .progress {
    position: absolute;
    inset: 0 0 auto 0;
    background: var(--ink);
  }
  .what {
    display: grid;
    min-width: 0;
  }
  strong,
  small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  strong {
    font-weight: 500;
    font-size: 12.5px;
  }
  small {
    color: var(--muted);
    font-size: 11px;
  }
  .past {
    opacity: 0.45;
  }
  .live strong {
    font-weight: 600;
  }
  .chip {
    padding: 1px 7px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--muted);
    font: 500 9.5px var(--font-mono);
    font-style: normal;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    white-space: nowrap;
  }
  .chip.now {
    border-color: transparent;
    background: var(--violet);
    color: var(--solid-void);
  }
  .now-line {
    display: grid;
    grid-template-columns: 38px minmax(0, 1fr);
    align-items: center;
    gap: 7px;
    height: 12px;
    padding-left: 2px;
  }
  .now-line span {
    color: var(--accent);
    font: 600 9.5px var(--font-mono);
  }
  .now-line i {
    position: relative;
    height: 1px;
    background: var(--accent);
  }
  .now-line i::before {
    content: "";
    position: absolute;
    left: -3px;
    top: -3px;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--accent);
  }
  .note {
    margin: 0 0 6px;
    color: var(--muted);
    font-size: 12px;
  }
  /* A narrow rail drops the time range and puts the chip under the title. */
  @container (max-width: 240px) {
    small {
      display: none;
    }
    button {
      grid-template-columns: 34px 3px minmax(0, 1fr);
      gap: 7px;
    }
    .chip {
      grid-column: 3;
      justify-self: start;
    }
    .now-line {
      grid-template-columns: 34px minmax(0, 1fr);
      gap: 5px;
    }
  }
</style>
