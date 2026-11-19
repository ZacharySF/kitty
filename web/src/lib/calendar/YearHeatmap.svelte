<script lang="ts">
  import { duration } from "./plan";

  /** Twelve months of day numbers; a day's circle deepens with its booked hours and today's is filled. Hover reads out, click pins. */
  let { year, busy, today, onopen }: {
    year: number;
    /** Booked minutes per `YYYY-MM-DD`. */
    busy: Record<string, number>;
    today: string;
    onopen: (day: string) => void;
  } = $props();

  const WEEKDAYS = ["M", "T", "W", "T", "F", "S", "S"];
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const level = (m: number) => (m < 20 ? 0 : m < 120 ? 1 : m < 210 ? 2 : m < 300 ? 3 : 4);
  const months = $derived(Array.from({ length: 12 }, (_, month) => {
    const first = new Date(Date.UTC(year, month, 1, 12));
    const length = new Date(Date.UTC(year, month + 1, 0, 12)).getUTCDate();
    const days = Array.from({ length }, (_, i) => iso(new Date(Date.UTC(year, month, i + 1, 12))));
    return {
      name: new Intl.DateTimeFormat(undefined, { month: "long", timeZone: "UTC" }).format(first),
      lead: (first.getUTCDay() + 6) % 7,
      days,
      total: days.reduce((sum, day) => sum + (busy[day] ?? 0), 0),
    };
  }));
  let hover = $state<string | null>(null);
  let pinned = $state<string | null>(null);
  const shown = $derived(hover ?? pinned ?? (today.startsWith(String(year)) ? today : `${year}-01-01`));
  const longDate = (day: string) =>
    new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${day}T12:00:00Z`));
</script>

<section class="year" aria-label="Year overview">
  <div class="months">
    {#each months as m, index (m.name)}
      <div class="month" class:current={today.startsWith(`${year}-${String(index + 1).padStart(2, "0")}`)}
        role="group" aria-label={`${m.name} ${year}, ${duration(m.total)} booked`}>
        <h3>{m.name}{#if m.total}<span>{duration(m.total)}</span>{/if}</h3>
        <div class="grid">
          {#each WEEKDAYS as w, i (i)}<b aria-hidden="true">{w}</b>{/each}
          {#each Array(m.lead) as _, i (i)}<span></span>{/each}
          {#each m.days as day (day)}
            <button class="d l{level(busy[day] ?? 0)}" class:today={day === today}
              aria-pressed={day === pinned} aria-label={`${day}: ${duration(busy[day] ?? 0)} booked`}
              onmouseenter={() => (hover = day)} onmouseleave={() => (hover = null)}
              onfocus={() => (hover = day)} onblur={() => (hover = null)}
              onclick={() => (pinned = pinned === day ? null : day)}>{Number(day.slice(8))}</button>
          {/each}
        </div>
      </div>
    {/each}
  </div>
  <footer role="status">
    <strong>{longDate(shown)}</strong>
    <span>{duration(busy[shown] ?? 0)} booked{shown === today ? " · today" : ""}</span>
    <span class="legend" aria-hidden="true">Free {#each [1, 2, 3, 4] as l}<i class="l{l}"></i>{/each} Busy</span>
    <button class="open" onclick={() => onopen(shown)}>Open day</button>
  </footer>
</section>

<style>
  .year {
    display: grid;
  }
  footer .legend {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    margin-left: auto;
    font-size: 13px;
  }
  .legend i {
    width: 12px;
    height: 12px;
    border-radius: 999px;
  }
  .months {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
    gap: 18px 36px;
    padding-bottom: 16px;
  }
  h3 {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin: 0 0 2px;
    font: 600 16px/1.3 var(--font-ui);
  }
  h3 span {
    color: var(--muted);
    font-size: 13px;
    font-weight: 400;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
  }
  .grid b {
    height: 22px;
    color: var(--subtle);
    font: 400 13px/22px var(--font-ui);
    text-align: center;
  }
  .d {
    position: relative;
    isolation: isolate;
    height: 26px;
    min-width: 0;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--ink);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
    cursor: pointer;
  }
  /* Each day's circle: empty when free, deeper with every step of booked time. */
  .d::before {
    content: "";
    position: absolute;
    inset: 1px calc(50% - 12px);
    z-index: -1;
    border-radius: 999px;
    background: var(--heat, transparent);
  }
  .d:hover::before,
  .d:focus-visible::before {
    box-shadow: 0 0 0 1px var(--muted);
  }
  .l1 {
    --heat: color-mix(in srgb, var(--ink) 10%, transparent);
  }
  .l2 {
    --heat: color-mix(in srgb, var(--ink) 19%, transparent);
  }
  .l3 {
    --heat: color-mix(in srgb, var(--ink) 29%, transparent);
  }
  .l4 {
    --heat: color-mix(in srgb, var(--ink) 40%, transparent);
  }
  .legend i {
    background: var(--heat);
  }
  .d.today {
    --heat: var(--ink);
    color: var(--solid-void);
    font-weight: 600;
  }
  .d[aria-pressed="true"]::before {
    box-shadow: 0 0 0 1.5px var(--ink);
  }
  .d.today[aria-pressed="true"]::before {
    box-shadow: 0 0 0 2px var(--solid-void), 0 0 0 3.5px var(--ink);
  }
  footer {
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding-top: 12px;
    border-top: 1px solid var(--hairline);
    font-size: 15px;
  }
  footer span {
    color: var(--muted);
    font-size: 14px;
  }
  .open {
    padding: 4px 10px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font-size: 14px;
  }
  .open:hover {
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    color: var(--ink);
  }
</style>
