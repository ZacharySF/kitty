<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../api";
  import { minutesLabel, type Collection, type Item, type StudyDay, type Workspace } from "./types";

  /**
   * Progress: the selected period's days stacked in subject colours beside a
   * share ring, a month calendar tinted by each day's main subject with
   * weekly-goal checks, and goal rings for today, this week and flashcard reviews.
   */
  let { data, period, onedit }: {
    data: Workspace;
    period: "week" | "month";
    onedit: (kind: Collection, item?: Item, defaults?: Partial<Item>) => void;
  } = $props();

  const subject = (id: number | null) => data.subjects.find((item) => item.id === id);
  const colour = (id: number | null) => subject(id)?.color || "var(--subtle)";
  const nameOf = (id: number | null) => subject(id)?.name || "Unassigned";
  const at = (date: string) => new Date(`${date}T12:00:00`);
  const short = (date: string, options: Intl.DateTimeFormatOptions) => at(date).toLocaleDateString(undefined, options);

  // B · subject-stacked bars + share ring
  const days = $derived(data.stats.history.days);
  const ceiling = $derived(Math.max(1, Math.ceil(Math.max(3600, ...days.map((day) => day.seconds)) / 3600)) * 3600);
  const share = $derived([...data.stats.history.subjects].sort((a, b) => b.seconds - a.seconds));
  const shareTotal = $derived(share.reduce((sum, item) => sum + item.seconds, 0));
  const RADIUS = 52;
  const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
  const arcs = $derived(share.reduce<{ id: number | null; len: number; offset: number }[]>((list, item) => {
    const offset = list.length ? list[list.length - 1].offset + list[list.length - 1].len : 0;
    list.push({ id: item.id, len: shareTotal ? (item.seconds / shareTotal) * CIRCUMFERENCE : 0, offset });
    return list;
  }, []));
  /** The subject being isolated (hover or click in the legend); `undefined` when none. */
  let hot = $state<number | null | undefined>(undefined);
  let pinned = $state(false);
  let hotDay = $state<number | null>(null);
  const focus = $derived(hot === undefined ? null : share.find((item) => item.id === hot) ?? null);
  const dayLabel = (day: StudyDay) => period === "week" ? short(day.date, { weekday: "short" }) : String(at(day.date).getDate());

  // C · month calendar tinted by each day's main subject
  const byDate = $derived(new Map(data.stats.days.map((day) => [day.date, day])));
  const monthStart = $derived(`${data.stats.history.start.slice(0, 7)}-01`);
  const calendar = $derived.by(() => {
    const first = at(monthStart);
    const lead = (first.getDay() + 6) % 7;
    const length = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
    const cells: (string | null)[] = [...Array(lead).fill(null)];
    for (let index = 1; index <= length; index++) cells.push(`${monthStart.slice(0, 8)}${String(index).padStart(2, "0")}`);
    while (cells.length % 7) cells.push(null);
    return Array.from({ length: cells.length / 7 }, (_, week) => cells.slice(week * 7, week * 7 + 7));
  });
  const monthMax = $derived(Math.max(1, ...calendar.flat().map((date) => (date && byDate.get(date)?.seconds) || 0)));
  const studyDays = $derived(calendar.flat().filter((date) => date && byDate.get(date)?.seconds).length);
  const iso = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  const mondayOf = (week: (string | null)[]) => {
    const known = week.findIndex(Boolean);
    const date = at(week[known]!);
    date.setDate(date.getDate() - known);
    return iso(date);
  };
  const weekSeconds = (monday: string) => {
    const date = at(monday);
    let seconds = 0;
    for (let index = 0; index < 7; index++, date.setDate(date.getDate() + 1)) seconds += byDate.get(iso(date))?.seconds || 0;
    return seconds;
  };
  const allStudy = (period: string) => data.targets.find((item) => item.period === period && !item.subject_id && !item.project_id)
    ?? data.targets.find((item) => item.period === period);
  const weeklyGoal = $derived(allStudy("weekly"));
  let picked = $state<string | null>(null);
  /** Today when it is in the month shown, else that month's last day with study. */
  const selected = $derived(picked && calendar.flat().includes(picked) ? picked
    : calendar.flat().includes(data.stats.today) ? data.stats.today
    : calendar.flat().filter((date) => date && byDate.get(date)?.seconds).at(-1) ?? monthStart);
  const selectedDay = $derived(byDate.get(selected));

  // E · goal rings
  const goalFor = (target: Item | undefined) => target && data.stats.goals.find((item) => item.id === target.id);
  let reviews = $state<{ done: number; due: number } | null>(null);
  onMount(() => {
    api<{ stats: { due: number; days: { day: string; reviews: number }[] } }>("/flashcards")
      .then((overview) => (reviews = { done: overview.stats.days.find((day) => day.day === data.stats.today)?.reviews || 0, due: overview.stats.due }))
      .catch(() => (reviews = null));
  });
  const rings = $derived.by(() => {
    const daily = allStudy("daily"), weekly = weeklyGoal;
    const today = goalFor(daily), week = goalFor(weekly);
    return [
      { label: "Today", tone: "var(--c1)", have: today?.seconds ?? data.stats.today_seconds, want: today?.goal_seconds ?? 0, unit: "time", period: "daily" },
      { label: "This week", tone: "var(--c3)", have: week?.seconds ?? data.stats.week_seconds, want: week?.goal_seconds ?? 0, unit: "time", period: "weekly" },
      { label: "Flashcard reviews", tone: "var(--c6)", have: reviews?.done ?? 0, want: reviews ? reviews.done + reviews.due : 0, unit: "cards", period: "" },
    ];
  });
  const amount = (value: number, unit: string) => unit === "time" ? minutesLabel(value) : `${value} ${value === 1 ? "card" : "cards"}`;
  let hotRing = $state<number | null>(null);
</script>

<div class="progress">
  <section class="panel stacked" aria-label="Study by subject">
    <div class="bars">
      <header>
        <span class="eyebrow">{period === "week" ? "Week" : "Month"} · by subject</span>
        <strong>{hotDay === null ? minutesLabel(data.stats.history.seconds) : `${short(days[hotDay].date, { weekday: "short", day: "numeric", month: "short" })} · ${minutesLabel(days[hotDay].seconds)}`}</strong>
      </header>
      <div class="plot" style:--count={days.length}>
        {#each [1, 0.5, 0] as fraction (fraction)}
          <span class="grid" style:bottom={`calc(20px + ${fraction} * (100% - 20px))`}><em>{Math.round((ceiling * fraction) / 360) / 10}h</em></span>
        {/each}
        {#each days as day, index (day.date)}
          <button type="button" class="day-bar" data-today={day.date === data.stats.today}
            aria-label={`${short(day.date, { weekday: "long", day: "numeric", month: "long" })}: ${minutesLabel(day.seconds)}${day.subjects.length ? `, mostly ${nameOf(day.subjects[0].id)}` : ""}`}
            onmouseenter={() => (hotDay = index)} onmouseleave={() => (hotDay = null)} onfocus={() => (hotDay = index)} onblur={() => (hotDay = null)}>
            <span class="stack" style:height={`${(day.seconds / ceiling) * 100}%`}>
              {#each day.subjects as part (part.id)}
                <i data-subject={part.id ?? "none"} style:flex-grow={part.seconds} style:background={colour(part.id)} class:dim={hot !== undefined && hot !== part.id}></i>
              {/each}
            </span>
            {#if period === "week" || index % 2 === 0 || day.date === data.stats.today}<small>{dayLabel(day)}</small>{/if}
          </button>
        {/each}
      </div>
    </div>
    <div class="share">
      <svg viewBox="0 0 140 140" aria-hidden="true">
        <circle cx="70" cy="70" r={RADIUS} class="track" />
        {#each arcs as arc (arc.id)}
          <circle cx="70" cy="70" r={RADIUS} class="arc" class:dim={hot !== undefined && hot !== arc.id} class:hot={hot === arc.id}
            style:stroke={colour(arc.id)} style:stroke-dasharray={`${Math.max(0, arc.len - 2)} ${CIRCUMFERENCE}`} style:stroke-dashoffset={-arc.offset} />
        {/each}
        <text x="70" y="70" class="big">{focus ? `${Math.round((focus.seconds / Math.max(1, shareTotal)) * 100)}%` : minutesLabel(shareTotal)}</text>
        <text x="70" y="86" class="small">{focus ? nameOf(focus.id).split(" ")[0] : period === "week" ? "7 days" : "month"}</text>
      </svg>
      {#if share.length}
        <ul>
          {#each share as item (item.id)}
            <li>
              <button type="button" aria-pressed={pinned && hot === item.id}
                onmouseenter={() => { if (!pinned) hot = item.id; }} onmouseleave={() => { if (!pinned) hot = undefined; }}
                onclick={() => { pinned = !(pinned && hot === item.id); hot = pinned ? item.id : undefined; }}>
                <span class="dot" style:background={colour(item.id)}></span>{nameOf(item.id)}<b>{minutesLabel(item.seconds)}</b>
              </button>
            </li>
          {/each}
        </ul>
      {:else}
        <p class="muted">Your first completed session will appear here.</p>
      {/if}
    </div>
  </section>

  <section class="panel calendar" aria-label="Study calendar">
    <div>
      <header>
        <strong>{short(monthStart, { month: "long", year: "numeric" })}</strong>
        <span class="eyebrow">{studyDays} study {studyDays === 1 ? "day" : "days"}{data.stats.daily_streak ? ` · ${data.stats.daily_streak}-day streak` : ""} · {data.stats.weekly_streak}-week streak</span>
      </header>
      <div class="dow" aria-hidden="true">{#each ["M", "T", "W", "T", "F", "S", "S"] as letter, index (index)}<span>{letter}</span>{/each}<span></span></div>
      {#each calendar as week, row (row)}
        {@const monday = mondayOf(week)}
        {@const seconds = weekSeconds(monday)}
        {@const current = week.includes(data.stats.today)}
        {@const hit = weeklyGoal ? seconds >= (weeklyGoal.minutes || 0) * 60 : seconds > 0}
        <div class="week">
          {#each week as date, index (date ?? `blank-${row}-${index}`)}
            {#if date}
              {@const day = byDate.get(date)}
              {@const main = day?.subjects[0]}
              <button type="button" class="day" class:on={selected === date} data-today={date === data.stats.today}
                style:--tone={main ? colour(main.id) : "var(--subtle)"} style:--depth={day?.seconds ? `${18 + (day.seconds / monthMax) * 70}%` : "0%"}
                aria-label={`${short(date, { day: "numeric", month: "long" })}: ${day?.seconds ? `${minutesLabel(day.seconds)}, mostly ${nameOf(main!.id)}` : "rest day"}`}
                aria-pressed={selected === date} onclick={() => (picked = date)}>
                <span>{Number(date.slice(8))}</span>
              </button>
            {:else}<span></span>{/if}
          {/each}
          <span class="week-check" class:hit role="img"
            aria-label={`${current ? "This week" : `Week of ${short(monday, { day: "numeric", month: "short" })}`}: ${minutesLabel(seconds)}${weeklyGoal ? ` of ${weeklyGoal.minutes} min goal${hit ? ", met" : ""}` : hit ? ", studied" : ", no study"}`}
            title={weeklyGoal ? `Weekly goal: ${weeklyGoal.minutes} min` : "A week with study keeps the weekly streak"}>✓</span>
        </div>
      {/each}
      <p class="muted note">Reports use {data.stats.timezone}; streaks count completed sessions, never logins.</p>
    </div>
    <aside>
      <span class="eyebrow">{short(selected, { weekday: "short", day: "numeric", month: "short" })}{selected === data.stats.today ? " · today" : ""}</span>
      <strong class="big">{selectedDay?.seconds ? minutesLabel(selectedDay.seconds) : "Rest day"}</strong>
      {#if selectedDay?.seconds}
        <p class="main"><span class="dot" style:background={colour(selectedDay.subjects[0].id)}></span>Mostly {nameOf(selectedDay.subjects[0].id)}</p>
        <div class="split" aria-hidden="true">{#each selectedDay.subjects as part (part.id)}<i style:flex-grow={part.seconds} style:background={colour(part.id)}></i>{/each}</div>
        <ul class="parts">{#each selectedDay.subjects as part (part.id)}<li><span class="dot" style:background={colour(part.id)}></span>{nameOf(part.id)}<b>{minutesLabel(part.seconds)}</b></li>{/each}</ul>
      {:else}
        <p class="muted">Rest days don't break a weekly streak.</p>
      {/if}
    </aside>
  </section>

  <section class="panel rings" aria-label="Goal rings">
    <svg viewBox="0 0 170 170" width="170" height="170" aria-hidden="true">
      {#each rings as ring, index (ring.label)}
        {@const radius = 74 - index * 18}
        {@const length = 2 * Math.PI * radius}
        <circle cx="85" cy="85" r={radius} class="ring-track" style:stroke={ring.tone} />
        {#if ring.want && ring.have}
          <circle cx="85" cy="85" r={radius} class="ring-arc" class:dim={hotRing !== null && hotRing !== index} style:stroke={ring.tone}
            style:stroke-dasharray={`${length * Math.min(1, ring.have / ring.want)} ${length}`} style:--length={length} />
        {/if}
      {/each}
    </svg>
    <div class="ring-side">
      <ul>
        {#each rings as ring, index (ring.label)}
          <li onmouseenter={() => (hotRing = index)} onmouseleave={() => (hotRing = null)}>
            <span class="bar" style:background={ring.tone}></span>
            <div>
              <span class="eyebrow">{ring.label}</span>
              {#if ring.want}
                <strong>{amount(ring.have, ring.unit)} <small>/ {amount(ring.want, ring.unit)}</small></strong>
              {:else if ring.unit === "cards"}
                <strong>{amount(ring.have, ring.unit)} <small>{reviews ? "· nothing due" : "· flashcards unavailable"}</small></strong>
              {:else}
                <strong>{amount(ring.have, ring.unit)} <small>· no goal</small></strong>
              {/if}
            </div>
            {#if ring.want}<b style:color={ring.tone}>{Math.round((ring.have / ring.want) * 100)}%</b>
            {:else if ring.period}<button type="button" class="set" onclick={() => onedit("targets", undefined, { title: ring.period === "daily" ? "Daily study" : "Weekly study", period: ring.period, minutes: ring.period === "daily" ? 60 : 300 })}>Set goal</button>{/if}
          </li>
        {/each}
      </ul>
      <details>
        <summary>All goals · {data.targets.length}</summary>
        <ul class="goal-list">
          {#each data.targets as target (target.id)}
            {@const goal = goalFor(target)}
            <li>
              <span>{target.title} · {target.period} · {nameOf(target.subject_id ?? null)}</span>
              <progress value={goal?.seconds || 0} max={goal?.goal_seconds || 1}></progress>
              <small>{minutesLabel(goal?.seconds || 0)} / {target.minutes} min</small>
              <button type="button" class="set" onclick={() => onedit("targets", target)}>Edit</button>
            </li>
          {/each}
        </ul>
        <button type="button" class="set" onclick={() => onedit("targets")}>Add goal</button>
      </details>
    </div>
  </section>
</div>

<style>
  .progress {
    container-type: inline-size;
    display: grid;
    grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);
    gap: 12px;
    align-items: start;
  }
  .progress > .panel {
    margin: 0;
  }
  .stacked {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
    gap: 18px;
  }
  @container (max-width: 760px) {
    .progress,
    .stacked,
    .calendar {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 10px;
  }
  header strong {
    color: var(--ink);
    font: 600 18px var(--font-title);
  }
  .plot {
    position: relative;
    display: grid;
    grid-template-columns: repeat(var(--count), minmax(0, 1fr));
    gap: clamp(2px, calc(70px / var(--count)), 10px);
    height: 200px;
    padding: 0 0 20px 30px;
  }
  .grid {
    position: absolute;
    left: 30px;
    right: 0;
    border-top: 1px dashed color-mix(in srgb, var(--border) 60%, transparent);
  }
  .grid em {
    position: absolute;
    left: -30px;
    top: -7px;
    color: var(--subtle);
    font: normal 500 9px var(--font-mono);
  }
  .day-bar {
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    align-items: center;
    height: 100%;
    padding: 0;
    border: 0;
    background: none;
  }
  .stack {
    display: flex;
    flex-direction: column-reverse;
    gap: 2px;
    width: min(100%, 30px);
    transition: transform 0.2s ease;
  }
  .stack i {
    min-height: 2px;
    border-radius: 3px;
    transition: opacity 0.2s ease;
  }
  .day-bar:hover .stack,
  .day-bar:focus-visible .stack {
    transform: scaleX(1.12);
  }
  .dim {
    opacity: 0.18;
  }
  .day-bar small {
    position: absolute;
    bottom: -18px;
    color: var(--subtle);
    font: 500 9.5px var(--font-mono);
    text-transform: uppercase;
  }
  .day-bar[data-today="true"] small {
    color: var(--ink);
  }
  .share {
    display: grid;
    grid-template-columns: 140px minmax(0, 1fr);
    align-items: center;
    gap: 14px;
  }
  .share svg circle {
    fill: none;
    stroke-width: 14;
    transform: rotate(-90deg);
    transform-origin: 70px 70px;
  }
  .share .track {
    stroke: color-mix(in srgb, var(--ink) 8%, transparent);
  }
  .share .arc {
    transition: opacity 0.2s ease, stroke-width 0.2s ease;
  }
  .share .arc.hot {
    stroke-width: 18;
  }
  .big {
    fill: var(--ink);
    font: 700 18px var(--font-title);
    text-anchor: middle;
  }
  .small {
    fill: var(--muted);
    font: 500 8px var(--font-mono);
    letter-spacing: 0.1em;
    text-anchor: middle;
    text-transform: uppercase;
  }
  ul {
    display: grid;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .share button {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 5px 8px;
    border: 0;
    border-radius: 8px;
    background: none;
    color: var(--ink);
    font: 12.5px var(--font-ui);
    text-align: left;
  }
  .share button:hover,
  .share button[aria-pressed="true"] {
    background: color-mix(in srgb, var(--ink) 6%, transparent);
  }
  li b {
    margin-left: auto;
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
  }
  .dot {
    flex: none;
    width: 8px;
    height: 8px;
    border-radius: 999px;
  }
  .calendar {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 190px;
    gap: 16px;
    align-items: start;
  }
  .dow,
  .week {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr)) 22px;
    gap: 5px;
  }
  .week {
    margin-top: 5px;
  }
  .dow span {
    color: var(--subtle);
    font: 500 9px var(--font-mono);
    text-align: center;
  }
  .day {
    position: relative;
    height: 34px;
    padding: 0;
    border: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
    border-radius: 8px;
    background: color-mix(in srgb, var(--tone) var(--depth), var(--surface-raised));
    color: var(--ink);
    transition: transform 0.12s ease, border-color 0.12s ease;
  }
  .day:hover {
    transform: translateY(-1px);
    border-color: var(--tone);
  }
  .day.on {
    border-color: var(--ink);
    box-shadow: 0 0 0 1px var(--ink);
  }
  .day span {
    position: absolute;
    top: 3px;
    left: 6px;
    font: 500 9.5px var(--font-mono);
    text-shadow: 0 0 4px var(--solid-void);
  }
  .day[data-today="true"]::after {
    content: "";
    position: absolute;
    right: 5px;
    bottom: 5px;
    width: 5px;
    height: 5px;
    border-radius: 999px;
    background: var(--ink);
  }
  .week-check {
    display: grid;
    place-items: center;
    color: var(--subtle);
    font-size: 11px;
    opacity: 0.35;
  }
  .week-check.hit {
    color: var(--success);
    opacity: 1;
  }
  .note {
    margin: 10px 0 0;
    font-size: 11.5px;
  }
  aside {
    display: grid;
    gap: 8px;
    padding: 12px;
    border: 1px solid var(--hairline);
    border-radius: 12px;
    background: var(--surface-raised);
  }
  aside .big {
    color: var(--ink);
    font: 700 22px var(--font-title);
  }
  .main {
    display: flex;
    align-items: center;
    gap: 7px;
    margin: 0;
    color: var(--ink);
    font-size: 13px;
  }
  .split {
    display: flex;
    gap: 2px;
    height: 6px;
  }
  .split i {
    border-radius: 3px;
  }
  .parts b {
    white-space: nowrap;
  }
  .parts li {
    display: flex;
    align-items: center;
    gap: 7px;
    color: var(--muted);
    font-size: 12px;
  }
  .rings {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    gap: 20px;
  }
  .ring-track {
    fill: none;
    stroke-width: 13;
    opacity: 0.13;
  }
  .ring-arc {
    fill: none;
    stroke-width: 13;
    stroke-linecap: round;
    transform: rotate(-90deg);
    transform-origin: 85px 85px;
    animation: sweep 1.1s cubic-bezier(0.2, 0.8, 0.2, 1) both;
    transition: opacity 0.2s ease;
  }
  .ring-arc.dim {
    opacity: 0.25;
  }
  @keyframes sweep {
    from {
      stroke-dasharray: 0 var(--length);
    }
  }
  .ring-side {
    display: grid;
    gap: 10px;
    min-width: 0;
  }
  .ring-side > ul {
    gap: 6px;
  }
  .ring-side > ul > li {
    display: grid;
    grid-template-columns: 3px minmax(0, 1fr) auto;
    align-items: center;
    gap: 12px;
    padding: 6px 8px;
    border-radius: 9px;
  }
  .ring-side > ul > li:hover {
    background: color-mix(in srgb, var(--ink) 5%, transparent);
  }
  .bar {
    align-self: stretch;
    border-radius: 3px;
  }
  .ring-side strong {
    display: block;
    color: var(--ink);
    font: 700 17px var(--font-title);
  }
  .ring-side strong small {
    color: var(--muted);
    font: 500 11px var(--font-mono);
  }
  .ring-side li > b {
    font: 600 12px var(--font-mono);
  }
  .set {
    padding: 2px 9px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font: 500 11px var(--font-ui);
  }
  .set:hover {
    color: var(--ink);
    border-color: var(--border);
  }
  .goal-list {
    gap: 6px;
    margin: 8px 0;
  }
  .goal-list li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 2px 8px;
    font-size: 12px;
  }
  .goal-list progress {
    accent-color: var(--accent);
    width: 100%;
  }
  .goal-list small {
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
  }
  @media (prefers-reduced-motion: reduce) {
    .ring-arc {
      animation: none;
    }
    .stack,
    .day {
      transition: none;
    }
  }
</style>
