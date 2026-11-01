<script lang="ts">
  import { duration } from "./plan";

  /** Twelve mini months; each day is tinted by its booked hours. Hover reads out, click pins. */
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
  const busiest = $derived(Math.max(1, ...months.map((m) => m.total)));
  let hover = $state<string | null>(null);
  let pinned = $state<string | null>(null);
  const shown = $derived(hover ?? pinned ?? (today.startsWith(String(year)) ? today : `${year}-01-01`));
  const longDate = (day: string) =>
    new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${day}T12:00:00Z`));
</script>

<section class="year" aria-label="Year overview">
  <header>
    <strong>{year}</strong>
    <span class="legend" aria-hidden="true">quiet {#each [0, 1, 2, 3, 4] as l}<i class="l{l}"></i>{/each} packed</span>
  </header>
  <div class="months">
    {#each months as m, index (m.name)}
      <div class="month" class:current={today.startsWith(`${year}-${String(index + 1).padStart(2, "0")}`)}
        role="group" aria-label={`${m.name} ${year}, ${duration(m.total)} booked`}>
        <h3>{m.name.slice(0, 3)}<span style:width={`${(m.total / busiest) * 100}%`}></span></h3>
        <div class="grid">
          {#each WEEKDAYS as w, i (i)}<b aria-hidden="true">{w}</b>{/each}
          {#each Array(m.lead) as _, i (i)}<span></span>{/each}
          {#each m.days as day (day)}
            <button class="d l{level(busy[day] ?? 0)}" class:today={day === today}
              aria-pressed={day === pinned} aria-label={`${day}: ${duration(busy[day] ?? 0)} booked`}
              onmouseenter={() => (hover = day)} onmouseleave={() => (hover = null)}
              onfocus={() => (hover = day)} onblur={() => (hover = null)}
              onclick={() => (pinned = pinned === day ? null : day)}></button>
          {/each}
        </div>
      </div>
    {/each}
  </div>
  <footer role="status">
    <i class="l{level(busy[shown] ?? 0)}"></i>
    <strong>{longDate(shown)}</strong>
    <span>{duration(busy[shown] ?? 0)} booked{shown === today ? " · today" : ""}</span>
    <button class="open" onclick={() => onopen(shown)}>Open day</button>
  </footer>
</section>

<style>
  .year {
    display: grid;
    padding-bottom: 3.6rem;
  }
  header {
    display: flex;
    align-items: center;
    padding: 10px 14px 2px;
  }
  header strong {
    font: 700 1.2rem var(--font-title);
  }
  .legend {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    margin-left: auto;
    color: var(--muted);
    font: 500 0.6rem var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .legend i,
  footer i {
    width: 10px;
    height: 10px;
    border-radius: 3px;
  }
  .months {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 14px 18px;
    padding: 10px 14px 14px;
  }
  h3 {
    position: relative;
    margin: 0 0 5px;
    padding-bottom: 3px;
    color: var(--muted);
    font: 500 0.68rem var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  h3 span {
    position: absolute;
    left: 0;
    bottom: 0;
    height: 2px;
    border-radius: 2px;
    background: color-mix(in srgb, var(--c1) 55%, transparent);
  }
  .current h3 {
    color: var(--accent);
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 2px;
  }
  .grid b {
    color: var(--subtle);
    font: 500 0.5rem var(--font-mono);
    text-align: center;
  }
  .d {
    aspect-ratio: 1;
    min-width: 0;
    padding: 0;
    border: 0;
    border-radius: 3px;
    cursor: pointer;
    transition: transform 120ms ease;
  }
  .d:hover,
  .d:focus-visible {
    position: relative;
    z-index: 1;
    transform: scale(1.35);
  }
  .l0 {
    background: color-mix(in srgb, var(--ink) 6%, transparent);
  }
  .l1 {
    background: color-mix(in srgb, var(--c1) 22%, transparent);
  }
  .l2 {
    background: color-mix(in srgb, var(--c1) 45%, transparent);
  }
  .l3 {
    background: color-mix(in srgb, var(--c1) 70%, transparent);
  }
  .l4 {
    background: var(--c1);
  }
  .d.today {
    box-shadow: 0 0 0 1.5px var(--ink);
  }
  .d[aria-pressed="true"] {
    outline: 1.5px solid var(--accent);
    outline-offset: 1px;
  }
  footer {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 34px;
    padding: 0 14px 0 14px;
    border-top: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--solid-panel) 85%, transparent);
    font-size: 0.8rem;
  }
  footer span {
    color: var(--muted);
    font: 500 0.68rem var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .open {
    margin-left: auto;
    padding: 2px 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font-size: 0.75rem;
  }
  @media (prefers-reduced-motion: reduce) {
    .d {
      transition: none;
    }
  }
</style>
