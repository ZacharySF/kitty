<script lang="ts">
  import Icon from "../Icon.svelte";
  import QuickAdd from "../ui/QuickAdd.svelte";
  import Select from "../ui/Select.svelte";
  import { clock, planQuick, type QuickKind, type QuickPlan, type QuickSlot } from "./quickAdd";

  /**
   * Q20 A: the calendar's one create affordance. A typed phrase ("gym tue 6pm 1h")
   * becomes an event or a task; a clicked slot prefills the time. `round` is the +
   * on the range pill; otherwise QuickAdd's own "+ label" action.
   */
  let {
    label,
    today,
    at,
    round = false,
    kind = $bindable("event"),
    open = $bindable(false),
    onsave,
  }: {
    label: string;
    today: string;
    /** Where the add starts: the selected day, or a clicked slot or range. */
    at: QuickSlot;
    round?: boolean;
    kind?: QuickKind;
    open?: boolean;
    /** `more` opens the created item in the drawer. Return false to keep the popover open. */
    onsave: (plan: QuickPlan, kind: QuickKind, more: boolean) => Promise<boolean>;
  } = $props();

  let text = $state("");
  const plan = $derived(planQuick(text, today, at));
  const when = $derived(new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" }).format(new Date(`${plan.day}T12:00:00`)));
  const span = $derived(plan.start === null ? (kind === "event" ? "All day" : "")
    : kind === "task" ? clock(plan.start) : `${clock(plan.start)}–${clock(plan.end ?? plan.start + 60)}`);

  function save() {
    return plan.title ? onsave(plan, kind, false) : false;
  }
  function more() {
    if (plan.title) void onsave(plan, kind, true).then((done) => done && (text = ""));
  }
</script>

<QuickAdd {label} bind:open bind:value={text} placement={round ? "top-end" : "bottom-end"}
  placeholder={kind === "event" ? "gym tue 6pm 1h" : "pay rent fri"} onsave={save} onmore={more}>
  {#snippet trigger(props)}
    {#if round}
      <button type="button" class="round-add" aria-label={label} title={`${label} (N)`} aria-keyshortcuts="N" {...props}><Icon name="plus" size={15} /></button>
    {:else}
      <button type="button" class="pill primary page-action" {...props}><Icon name="plus" size={14} />{label}</button>
    {/if}
  {/snippet}
  {#snippet chips()}
    <Select label="Kind" bind:value={kind} options={[{ value: "event", label: "Event" }, { value: "task", label: "Task" }]} />
    <span class="when" title={plan.where ? `at ${plan.where}` : undefined}><Icon name="calendar" size={12} />{when}{#if span}<b>{span}</b>{/if}</span>
  {/snippet}
</QuickAdd>

<style>
  .round-add {
    display: inline-grid;
    place-items: center;
    width: 28px;
    height: 28px;
    padding: 0;
    border: 1px solid transparent;
    border-radius: 999px;
    background: var(--ink);
    color: var(--solid-void);
    cursor: pointer;
  }
  .round-add:hover,
  .round-add[aria-expanded="true"] {
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--ink) 18%, transparent);
  }
  .when {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 24px;
    padding: 0 9px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--ink);
    font: 500 var(--fs-control) var(--font-ui);
    white-space: nowrap;
  }
  .when b {
    font: 600 var(--fs-control) var(--font-mono);
  }
</style>
