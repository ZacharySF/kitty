<script lang="ts">
  import { tick, untrack } from "svelte";
  import { addDays, dateAt, weekStart } from "../calendar/dates";
  import Popover from "./Popover.svelte";

  /**
   * A date field: the ISO date (YYYY-MM-DD) stays typeable, and the calendar button beside
   * it opens a month in a popover. Weeks start on Monday, as in the calendar's mini month.
   * Arrow keys walk the days (Page Up/Down a month, Home/End the week), Enter or a click
   * picks one and returns focus to the button; Escape closes only the calendar.
   */
  let { value = $bindable(""), id, label, required = false, disabled = false }: {
    /** `YYYY-MM-DD`, or empty for no date. */
    value?: string;
    /** The input's id, for the field's own `<label for>`. */
    id: string;
    /** The field's name ("Target date"); the button and calendar are "Choose target date". */
    label: string;
    required?: boolean;
    disabled?: boolean;
  } = $props();

  const ISO = /^\d{4}-\d{2}-\d{2}$/;
  /** A real calendar day written as `YYYY-MM-DD` (no 2027-02-30). */
  function real(day: string) {
    if (!ISO.test(day)) return false;
    const at = dateAt(day);
    return !Number.isNaN(at.getTime()) && at.toISOString().slice(0, 10) === day;
  }
  /** `day` moved by `step` months, clamped to the last day of the month it lands in. */
  function monthShift(day: string, step: number) {
    const year = Number(day.slice(0, 4));
    const month = Number(day.slice(5, 7)) - 1 + step;
    const last = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    return new Date(Date.UTC(year, month, Math.min(Number(day.slice(8)), last), 12)).toISOString().slice(0, 10);
  }
  const localToday = () => new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const monthTitle = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
  const weekdayFormat = new Intl.DateTimeFormat(undefined, { weekday: "narrow", timeZone: "UTC" });
  /** 2024-01-01 is a Monday. */
  const weekdays = Array.from({ length: 7 }, (_, i) => weekdayFormat.format(dateAt(addDays("2024-01-01", i))));

  const pickLabel = $derived(`Choose ${label.toLowerCase()}`);
  let popover = $state<ReturnType<typeof Popover>>();
  let input = $state<HTMLInputElement>();
  let grid = $state<HTMLElement>();
  let open = $state(false);
  let today = $state("");
  /** The day keyboard focus sits on; the calendar always shows its month. */
  let cursor = $state("");
  const month = $derived(cursor.slice(0, 7));
  const title = $derived(cursor ? monthTitle.format(dateAt(`${month}-01`)) : "");
  const days = $derived.by(() => {
    if (!cursor) return [];
    const first = weekStart(`${month}-01`);
    return Array.from({ length: 42 }, (_, i) => addDays(first, i));
  });

  // Each opening starts at the field's date, or today when it holds none.
  $effect.pre(() => {
    if (!open) return;
    untrack(() => {
      today = localToday();
      cursor = real(value) ? value : today;
    });
  });

  // A typed date that is not a real day stops the form, as the native date field did.
  $effect(() => {
    input?.setCustomValidity(value && !real(value) ? "Use a date like 2027-04-12." : "");
  });

  async function move(day: string) {
    cursor = day;
    await tick();
    grid?.querySelector<HTMLElement>(`[data-day="${day}"]`)?.focus();
  }

  function pick(day: string) {
    value = day;
    popover?.close(true);
  }

  function clear() {
    value = "";
    popover?.close(true);
  }

  const STEPS: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
  function key(event: KeyboardEvent, from: string) {
    let next: string;
    if (event.key in STEPS) next = addDays(from, STEPS[event.key]);
    else if (event.key === "Home") next = weekStart(from);
    else if (event.key === "End") next = addDays(weekStart(from), 6);
    else if (event.key === "PageUp") next = monthShift(from, -1);
    else if (event.key === "PageDown") next = monthShift(from, 1);
    else return;
    event.preventDefault();
    void move(next);
  }
</script>

<div class="date-field">
  <input bind:this={input} {id} bind:value {required} {disabled} inputmode="numeric" autocomplete="off" placeholder="YYYY-MM-DD" maxlength="10" />
  <Popover bind:this={popover} bind:open label={pickLabel} placement="bottom-end">
    {#snippet trigger(props)}
      <button type="button" class="pick" {disabled} {...props}>
        <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><rect x="2.5" y="3.5" width="11" height="10" rx="2" /><path d="M2.5 6.5h11M5.5 2v3M10.5 2v3" /></svg>
        <span class="sr-only">{pickLabel}</span>
      </button>
    {/snippet}
    <div class="calendar">
      <header>
        <strong aria-live="polite">{title}</strong>
        <button type="button" class="nav" aria-label="Previous month" onclick={() => (cursor = monthShift(cursor, -1))}>‹</button>
        <button type="button" class="nav" aria-label="Next month" onclick={() => (cursor = monthShift(cursor, 1))}>›</button>
      </header>
      <div class="weekdays" aria-hidden="true">{#each weekdays as day, i (i)}<span>{day}</span>{/each}</div>
      <div class="days" role="group" aria-label={title} bind:this={grid}>
        {#each days as day (day)}
          <button type="button" class="day" class:out={day.slice(0, 7) !== month} class:today={day === today}
            aria-label={day} aria-current={day === today ? "date" : undefined} aria-pressed={day === value}
            data-day={day} data-autofocus={day === cursor ? "" : undefined} tabindex={day === cursor ? 0 : -1}
            onkeydown={(event) => key(event, day)} onclick={() => pick(day)}>{Number(day.slice(8))}</button>
        {/each}
      </div>
      <footer>
        <button type="button" class="clear" disabled={!value} onclick={clear}>Clear date</button>
      </footer>
    </div>
  </Popover>
</div>

<style>
  /* The field with the calendar button tucked inside its right edge. */
  .date-field {
    position: relative;
    display: grid;
  }
  .date-field input {
    width: 100%;
    padding-right: 36px;
    font-variant-numeric: tabular-nums;
  }
  .pick {
    position: absolute;
    top: 50%;
    right: 3px;
    display: grid;
    place-items: center;
    width: calc(var(--control-h) - 6px);
    height: calc(var(--control-h) - 6px);
    padding: 0;
    border: 0;
    border-radius: var(--r-item);
    background: transparent;
    color: var(--muted);
    transform: translateY(-50%);
    cursor: pointer;
  }
  .pick:hover:not(:disabled),
  .pick[aria-expanded="true"] {
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    color: var(--ink);
  }
  .pick svg {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.4;
    stroke-linecap: round;
  }
  .calendar {
    display: grid;
    gap: 6px;
    padding: 5px;
    width: calc(7 * 38px);
  }
  header {
    display: flex;
    align-items: center;
    gap: 2px;
    padding-left: 6px;
  }
  header strong {
    margin-right: auto;
    font: 600 15px/1.3 var(--font-ui);
  }
  .nav {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    padding: 0 0 2px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font-size: 20px;
    line-height: 1;
  }
  .nav:hover {
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    color: var(--ink);
  }
  .weekdays,
  .days {
    display: grid;
    grid-template-columns: repeat(7, 38px);
  }
  .weekdays span {
    display: grid;
    place-items: center;
    height: 24px;
    color: var(--subtle);
    font: 500 12px var(--font-ui);
  }
  .day {
    width: 38px;
    height: 38px;
    padding: 0;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font: 400 13px var(--font-ui);
    font-variant-numeric: tabular-nums;
    cursor: pointer;
  }
  .day:hover {
    background: color-mix(in srgb, var(--ink) 8%, transparent);
  }
  .day.out {
    color: var(--subtle);
  }
  /* Today is ringed; the chosen day is filled. */
  .day.today {
    box-shadow: inset 0 0 0 1px var(--ink);
    font-weight: 600;
  }
  .day[aria-pressed="true"] {
    background: var(--ink);
    color: var(--solid-void, #141414);
    font-weight: 600;
  }
  footer {
    display: flex;
    justify-content: flex-end;
    padding-top: 6px;
    border-top: 1px solid var(--hairline);
  }
  .clear {
    min-height: var(--control-h);
    padding: 0 10px;
    border: 0;
    border-radius: var(--r-item);
    background: transparent;
    color: var(--muted);
    font: 400 var(--fs-control) var(--font-ui);
  }
  .clear:hover:not(:disabled) {
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    color: var(--ink);
  }
</style>
