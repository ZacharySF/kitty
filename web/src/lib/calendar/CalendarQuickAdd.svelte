<script lang="ts">
  import Icon from "../Icon.svelte";
  import QuickAdd from "../ui/QuickAdd.svelte";
  import Select from "../ui/Select.svelte";
  import { clock, planQuick, type QuickKind, type QuickPlan, type QuickSlot } from "./quickAdd";

  /**
   * Q20 A: the calendar's one create affordance. A typed phrase ("gym tue 6pm 1h")
   * becomes an event or a task; a clicked slot prefills the time. The trigger is
   * QuickAdd's own "+ label" primary action.
   */
  let {
    label,
    today,
    at,
    shortcut = "",
    kind = $bindable("event"),
    open = $bindable(false),
    onsave,
  }: {
    label: string;
    today: string;
    /** Where the add starts: the selected day, or a clicked slot or range. */
    at: QuickSlot;
    /** The single key that opens it, named in the button's tooltip. */
    shortcut?: string;
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

<QuickAdd {label} bind:open bind:value={text} placement="bottom-end"
  placeholder={kind === "event" ? "gym tue 6pm 1h" : "pay rent fri"} onsave={save} onmore={more}>
  {#snippet trigger(props)}
    <button type="button" class="pill primary page-action" title={shortcut ? `${label} (${shortcut})` : undefined}
      aria-keyshortcuts={shortcut || undefined} {...props}><Icon name="plus" size={14} />{label}</button>
  {/snippet}
  {#snippet chips()}
    <span class="calendar-kind"><Select label="Kind" bind:value={kind} options={[{ value: "event", label: "Event" }, { value: "task", label: "Task" }]} /></span>
    <span class="when" title={plan.where ? `at ${plan.where}` : undefined}><Icon name="calendar" size={12} />{when}{#if span}<b>{span}</b>{/if}</span>
  {/snippet}
</QuickAdd>

<style>
  /* The calendar draws no text under 13px, its quick add included. */
  .calendar-kind {
    display: contents;
  }
  :global(:root body .kitty-popover.quick-add:has(.calendar-kind) :is(kbd, .br-btn)) {
    font-size: 13px;
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
    font: 500 max(13px, var(--fs-control)) var(--font-ui);
    white-space: nowrap;
  }
  .when b {
    font: 600 max(13px, var(--fs-control)) var(--font-ui);
  }
</style>
