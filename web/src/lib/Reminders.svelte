<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api, dateLabel } from "./api";
  import Loader from "./Loader.svelte";
  import { beginDrag } from "./pointerDrag";
  let {
    onopenjob,
    onchange,
  }: { onopenjob: (id: number) => void; onchange: () => Promise<void> } =
    $props();
  type Reminder = {
    id: number;
    kind: string;
    due_at: string;
    note: string | null;
    job_id: number | null;
    label: string;
  };
  /** The last snooze or delete, until it is undone or replaced. */
  type Undo =
    | { kind: "snooze"; id: number; label: string; previous: string }
    | { kind: "delete"; label: string; row: Record<string, unknown> };
  /** How far a row must be swiped before letting go snoozes (right) or deletes (left). */
  const SWIPE = 80;
  const UNDO_MS = 10_000;
  const KIND_TONE: Record<string, string> = {
    followup: "var(--c1)",
    deadline: "var(--warning)",
    opens: "var(--c3)",
    custom: "var(--violet)",
  };
  let data = $state<{ due: Reminder[]; upcoming: Reminder[] }>({
    due: [],
    upcoming: [],
  });
  let loading = $state(true);
  let busy = $state(false);
  let error = $state("");
  let date = $state("");
  let when = $state("");
  let undo = $state<Undo | null>(null);
  let editing = $state<{ id: number; note: string; date: string; was: string } | null>(null);
  let swipe = $state<{ id: number; dx: number } | null>(null);
  function chooseWhen(value: string) {
    when = value;
    if (value === "today") {
      const now = new Date();
      date = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    } else date = value === "custom" ? "" : value;
  }
  let note = $state("");
  async function load(quiet = false) {
    if (!quiet) loading = true;
    try {
      data = await api("/reminders");
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      loading = false;
    }
  }
  onMount(() => {
    void load();
  });
  /** The undo pill leaves on its own after a while; each new snooze or delete restarts it. */
  $effect(() => {
    if (!undo) return;
    const shown = undo;
    const timer = window.setTimeout(() => {
      if (undo === shown) undo = null;
    }, UNDO_MS);
    return () => window.clearTimeout(timer);
  });
  /** Runs one change, then reloads the list quietly and refreshes the counts elsewhere. */
  async function run(work: () => Promise<unknown>) {
    busy = true;
    error = "";
    try {
      await work();
      await load(true);
      await onchange();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
  async function create(event: SubmitEvent) {
    event.preventDefault();
    await run(async () => {
      await api("/reminders", { date, note });
      date = "";
      when = "";
      note = "";
    });
  }
  const complete = (reminder: Reminder) => run(() => api(`/reminders/${reminder.id}/complete`, {}));
  const snooze = (reminder: Reminder) =>
    run(async () => {
      const result = await api<{ previous: string }>(`/reminders/${reminder.id}/snooze`, {});
      undo = { kind: "snooze", id: reminder.id, label: reminder.label, previous: result.previous };
    });
  const remove = (reminder: Reminder) =>
    run(async () => {
      const result = await api<{ reminder: Record<string, unknown> }>(`/reminders/${reminder.id}/delete`, {});
      undo = { kind: "delete", label: reminder.label, row: result.reminder };
    });
  const revert = () =>
    run(async () => {
      const last = undo;
      if (!last) return;
      if (last.kind === "snooze") await api(`/reminders/${last.id}`, { date: last.previous });
      else await api("/reminders/restore", { reminder: last.row });
      undo = null;
    });
  /** The local calendar day (YYYY-MM-DD) of a stored timestamp, as the list shows it. */
  function localDay(value: string): string {
    const day = new Date(value);
    return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
  }
  async function startEdit(reminder: Reminder) {
    editing = { id: reminder.id, note: reminder.note ?? "", date: localDay(reminder.due_at), was: localDay(reminder.due_at) };
    await tick();
    document.querySelector<HTMLInputElement>(`[data-edit="${reminder.id}"]`)?.focus();
  }
  function cancelOnEscape(event: KeyboardEvent) {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    editing = null;
  }
  async function saveEdit(event: SubmitEvent) {
    event.preventDefault();
    const draft = editing;
    if (!draft) return;
    editing = null;
    // A changed day is saved at local noon, so it shows as the same day wherever it is read.
    const changed = draft.date !== draft.was ? { date: new Date(`${draft.date}T12:00`).toISOString() } : {};
    await run(() => api(`/reminders/${draft.id}`, { note: draft.note, ...changed }));
  }
  function startSwipe(event: PointerEvent, reminder: Reminder) {
    if ((event.target as HTMLElement).closest("button, input, a") || editing?.id === reminder.id) return;
    const x0 = event.clientX;
    beginDrag(event, {
      start: () => (swipe = { id: reminder.id, dx: 0 }),
      move: (x) => (swipe = { id: reminder.id, dx: Math.max(-140, Math.min(140, x - x0)) }),
      drop: () => {
        const dx = swipe?.dx ?? 0;
        swipe = null;
        if (dx > SWIPE) void snooze(reminder);
        else if (dx < -SWIPE) void remove(reminder);
      },
      cancel: () => (swipe = null),
    });
  }
</script>

{#snippet glyph(name: "edit" | "snooze" | "trash" | "check")}
  <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" class="glyph">
    {#if name === "edit"}<path d="M4 20h4L19 9l-4-4L4 16Zm9-13 4 4" />
    {:else if name === "snooze"}<path d="M12 21a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm0-12v4l3 2M4 4 2 6m18-2 2 2" />
    {:else if name === "trash"}<path d="M4 7h16M9 7V4h6v3m-9 0 1 13h10l1-13M10 11v6m4-6v6" />
    {:else}<path d="m5 12 4 4 10-10" />{/if}
  </svg>
{/snippet}

<section class="panel">
  <header class="panel-heading">
    <div>
      <span class="eyebrow">THE NEXT SMALL STEP</span>
      <h2>Reminders & follow-ups</h2>
    </div>
    <button class="button" disabled={loading} onclick={() => load()}>Refresh</button>
  </header>
  <div class="reminders-body">
    {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
    <form onsubmit={create}>
      <label>When<select required value={when} disabled={busy}
        onchange={(event) => chooseWhen(event.currentTarget.value)}>
        <option value="" disabled>Choose when…</option>
        <option value="today">Today</option>
        <option value="tomorrow">Tomorrow</option>
        <option value="+7">In one week</option>
        <option value="+14">In two weeks</option>
        <option value="+30">In 30 days</option>
        <option value="custom">Choose a date…</option>
      </select></label>
      {#if when === "custom"}<label>Date<input type="date" required bind:value={date} disabled={busy} /></label>{/if}
      <label
        >Reminder<input
          required
          bind:value={note}
          maxlength="20000"
          placeholder="A next step worth remembering"
        /></label
      ><button class="button primary" disabled={busy}>Add reminder</button>
    </form>
    <p class="hint" aria-hidden="true">Hover a reminder for its actions, or swipe it: → snooze a day · ← delete</p>
    {#if loading}<Loader label="Loading reminders…" />{:else}{#each [{ title: "Due now", items: data.due, late: true }, { title: "Coming up", items: data.upcoming, late: false }] as group (group.title)}<section
          class="reminder-group"
        >
          <h3>{group.title}<span class="badge">{group.items.length}</span></h3>
          {#if !group.items.length}<p class="muted">Nothing here right now.</p>{/if}
          <ul class="rows">
            {#each group.items as reminder (reminder.id)}
              {@const dx = swipe?.id === reminder.id ? swipe.dx : 0}
              <li class="sw" class:armed-right={dx > SWIPE} class:armed-left={dx < -SWIPE}>
                <span class="under left" aria-hidden="true">{@render glyph("snooze")} Snooze a day</span>
                <span class="under right" aria-hidden="true">Delete {@render glyph("trash")}</span>
                <div
                  class="row"
                  class:moving={swipe?.id === reminder.id}
                  style:transform={dx ? `translateX(${dx}px)` : undefined}
                  role="group"
                  aria-label={reminder.label}
                  onpointerdown={(event) => startSwipe(event, reminder)}
                >
                  <button
                    class="tick"
                    style:--tone={KIND_TONE[reminder.kind] ?? "var(--violet)"}
                    aria-label={`Complete ${reminder.label}`}
                    title="Complete"
                    disabled={busy}
                    onclick={() => complete(reminder)}>{@render glyph("check")}</button
                  >
                  <div class="body">
                    {#if editing?.id === reminder.id}
                      <form class="edit" onsubmit={saveEdit}>
                        <input
                          data-edit={reminder.id}
                          onkeydown={cancelOnEscape}
                          aria-label="Reminder text"
                          maxlength="20000"
                          placeholder="Note"
                          bind:value={editing.note}
                        />
                        <input type="date" aria-label="Due date" required bind:value={editing.date} onkeydown={cancelOnEscape} />
                        <button class="pill" disabled={busy}>Save</button>
                        <button type="button" class="pill" onclick={() => (editing = null)}>Cancel</button>
                      </form>
                    {:else}
                      <strong>{reminder.label}</strong>
                      <small
                        ><i style:background={KIND_TONE[reminder.kind] ?? "var(--violet)"}></i>{reminder.kind} ·
                        <span class="due" class:late={group.late}>{dateLabel(reminder.due_at)}</span
                        >{#if reminder.note && reminder.note !== reminder.label}<span> · {reminder.note}</span>{/if}</small
                      >
                    {/if}
                  </div>
                  {#if reminder.job_id}<button class="pill open-job" onclick={() => onopenjob(reminder.job_id!)}
                      >Open job</button
                    >{/if}
                  <span class="acts">
                    <button title="Edit" aria-label="Edit" onclick={() => startEdit(reminder)}>{@render glyph("edit")}</button>
                    <button title="Snooze a day" aria-label="Snooze a day" disabled={busy} onclick={() => snooze(reminder)}
                      >{@render glyph("snooze")}</button
                    >
                    <button title="Delete" aria-label="Delete" class="danger" disabled={busy} onclick={() => remove(reminder)}
                      >{@render glyph("trash")}</button
                    >
                  </span>
                </div>
              </li>
            {/each}
          </ul>
        </section>{/each}{/if}
  </div>
</section>
{#if undo}<div class="undo" role="status">
    <span>{undo.kind === "snooze" ? "Snoozed a day" : "Deleted"}: <strong>{undo.label}</strong></span>
    <button class="pill" disabled={busy} onclick={revert}>Undo</button>
  </div>{/if}

<style>
  .reminders-body {
    padding: 22px;
  }
  form:not(.edit) {
    display: flex;
    align-items: end;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }
  label {
    display: grid;
    gap: 8px;
    font-size: 12px;
    color: var(--muted);
    flex: 1;
    min-width: 180px;
  }
  .hint {
    max-width: none;
    margin: 0 0 8px;
    color: var(--subtle);
    font: 10.5px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .reminder-group {
    margin-top: 24px;
  }
  .reminder-group h3 {
    font-size: 16px;
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
  }
  .reminder-group > p {
    font-size: 13px;
  }
  .rows {
    display: grid;
    gap: 6px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .sw {
    position: relative;
    overflow: hidden;
    border-radius: 12px;
  }
  .under {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 16px;
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .under.left {
    justify-content: start;
    background: color-mix(in srgb, var(--c3) 22%, transparent);
    color: var(--c3);
  }
  .under.right {
    justify-content: end;
    background: color-mix(in srgb, var(--error) 22%, transparent);
    color: var(--error);
  }
  .armed-left .under.right,
  .armed-right .under.left {
    filter: saturate(1.6) brightness(1.15);
  }
  .row {
    position: relative;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 12px;
    border: 1px solid var(--hairline);
    border-radius: 12px;
    background: var(--solid-panel);
    touch-action: pan-y;
    user-select: none;
    cursor: grab;
    transition:
      transform 220ms cubic-bezier(0.2, 0.9, 0.3, 1.2),
      border-color 140ms;
  }
  .row.moving {
    transition: none;
    cursor: grabbing;
  }
  .row:hover {
    border-color: color-mix(in srgb, var(--violet) 50%, transparent);
  }
  .tick {
    display: grid;
    flex: none;
    place-items: center;
    width: 26px;
    height: 26px;
    padding: 0;
    border: 1.5px solid var(--tone);
    border-radius: 999px;
    background: transparent;
    color: transparent;
    cursor: pointer;
  }
  .tick:hover:not(:disabled),
  .tick:focus-visible {
    background: color-mix(in srgb, var(--tone) 20%, transparent);
    color: var(--ink);
  }
  .glyph {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .body {
    display: grid;
    flex: 1;
    gap: 2px;
    min-width: 0;
  }
  .body strong {
    overflow: hidden;
    color: var(--ink);
    font: 500 13.5px var(--font-ui);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .body small {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0 5px;
    color: var(--muted);
    font-size: 11.5px;
  }
  .body small i {
    width: 6px;
    height: 6px;
    border-radius: 999px;
  }
  .late {
    color: var(--warning);
  }
  .edit {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
  }
  .edit input {
    min-height: 28px;
    padding: 2px 8px;
    border: 1px solid var(--violet);
    border-radius: 6px;
    color: var(--ink);
    font: 500 13px var(--font-ui);
  }
  .edit input:first-child {
    flex: 1;
    min-width: 160px;
  }
  .edit .pill,
  .open-job {
    min-height: 26px;
    padding: 2px 10px;
    font-size: 12px;
  }
  .acts {
    display: flex;
    gap: 2px;
    opacity: 0;
    transform: translateX(6px);
    transition:
      opacity 140ms,
      transform 140ms;
  }
  .row:hover .acts,
  .row:focus-within .acts {
    opacity: 1;
    transform: none;
  }
  .acts button {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    padding: 0;
    border: 1px solid transparent;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
  }
  .acts button:hover:not(:disabled) {
    border-color: var(--hairline);
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    color: var(--ink);
  }
  .acts button.danger:hover:not(:disabled) {
    color: var(--error);
  }
  /* Fixed above the Rika bar so it stays in reach however far the list is scrolled. */
  .undo {
    position: fixed;
    bottom: max(24px, calc(var(--rika-space, 0px) + 12px));
    left: 50%;
    z-index: 25;
    display: inline-flex;
    align-items: center;
    gap: 10px;
    max-width: min(480px, calc(100vw - 48px));
    transform: translateX(-50%);
    box-shadow: 0 12px 32px color-mix(in srgb, var(--shadow-tint) 45%, transparent);
    padding: 3px 4px 3px 14px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-raised) 92%, transparent);
    backdrop-filter: blur(12px);
    color: var(--muted);
    font-size: 12px;
  }
  .undo span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .undo strong {
    color: var(--ink);
    font-weight: 500;
  }
  .undo .pill {
    min-height: 26px;
    padding: 2px 10px;
  }
  @media (prefers-reduced-motion: reduce) {
    .row,
    .acts {
      transition: none;
    }
  }
  @media (max-width: 650px) {
    .reminders-body {
      padding: 16px;
    }
  }
</style>
