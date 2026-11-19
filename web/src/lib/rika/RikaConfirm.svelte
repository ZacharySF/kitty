<script lang="ts">
  import CalendarDateTime from "../CalendarDateTime.svelte";
  import { otherDate, radioStep, type PlanChoice, type PlanItem, type RikaPlan } from "./rika";
  import { rika } from "./session.svelte";

  /**
   * Rika's follow-ups, under the reply that asks them.
   *
   * A date question ("Is “Lab report” on Fri 20 Nov or Fri 27 Nov?"): before adding a dated
   * item whose date is not clear, Rika offers each reading as a chip, plus Another date.
   * Pick one and Add; the item is then added with Undo. Not now adds nothing.
   *
   * A check-in ("Did you finish “X”?"): Done, Tomorrow, Another day and Drop each apply at
   * once, with Undo. Not now leaves the task as it is.
   */
  let { plan }: { plan: RikaPlan } = $props();
  type Pick = number | "other";
  const checkin = $derived(plan.plan_kind === "checkin");
  const pending = $derived(plan.status === "pending");
  const asked = $derived(plan.items.filter((item) => item.question));
  // svelte-ignore state_referenced_locally
  let picked = $state<Record<string, Pick>>(Object.fromEntries(plan.items.map((item) => [item.id, item.choice ?? 0])));
  // svelte-ignore state_referenced_locally
  let other = $state<Record<string, string>>(Object.fromEntries(plan.items.map((item) => [item.id, ""])));
  let busy = $state(false);

  /** The chips of one question, in order: its choices, with the other date before a check-in's Drop. */
  function chips(item: PlanItem): { value: Pick; label: string }[] {
    const choices = (item.question?.choices ?? []).map((label, value) => ({ value: value as Pick, label }));
    if (!item.date) return choices;
    const another = { value: "other" as Pick, label: checkin ? "Another day" : "Another date" };
    return checkin ? [...choices.slice(0, -1), another, ...choices.slice(-1)] : [...choices, another];
  }
  function choice(item: PlanItem): PlanChoice | null {
    const pick = picked[item.id];
    return pick === "other" ? otherDate(other[item.id] ?? "") : pick;
  }
  const ready = $derived(asked.every((item) => choice(item) !== null));

  async function send(decision: "approve" | "dismiss") {
    if (busy) return;
    busy = true;
    const choices = Object.fromEntries(asked.map((item) => [item.id, choice(item) ?? 0]));
    await rika.decidePlan(plan.id, decision, plan.items.map((item) => item.id), choices);
    busy = false;
  }
  function pick(item: PlanItem, value: Pick) {
    picked[item.id] = value;
    // A check-in's answer applies at once; another day waits for its date.
    if (checkin && value !== "other") void send("approve");
  }
  /** Arrow keys move the choice within a question; Enter adds. */
  function keys(event: KeyboardEvent, item: PlanItem) {
    if (checkin) return;
    const options = chips(item);
    const at = options.findIndex((option) => option.value === picked[item.id]);
    if (event.key === "Enter") {
      event.preventDefault();
      if (ready) void send("approve");
      return;
    }
    const next = radioStep(at, options.length, event.key);
    if (next === null) return;
    event.preventDefault();
    picked[item.id] = options[next].value;
    (event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>("button")[next]?.focus();
  }
  /** The date field comes into view when it opens. */
  function reveal(node: HTMLElement) {
    node.scrollIntoView({ block: "nearest" });
  }
  /** What was answered, once it was. */
  function answer(item: PlanItem): string {
    if (plan.status === "dismissed") return "Not now";
    if (item.result?.status === "failed") return item.result.summary;
    return item.question?.choices[item.choice ?? 0] ?? "";
  }
</script>

<article class="confirm" data-kind={plan.plan_kind} data-status={plan.status} aria-label={checkin ? "Check-in" : "Date question"}>
  {#each asked as item (item.id)}
    <div class="item">
      {#if asked.length > 1}<p>{item.question?.text}</p>{/if}
      {#if pending}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="chips" role={checkin ? "group" : "radiogroup"} aria-label={item.question?.text} onkeydown={(event) => keys(event, item)}>
          {#each chips(item) as option (option.value)}
            {@const on = picked[item.id] === option.value}
            {#if checkin}
              <button type="button" class="pill" class:primary={on && option.value === "other"} aria-pressed={option.value === "other" ? on : undefined}
                disabled={busy} onclick={() => pick(item, option.value)}>{option.label}</button>
            {:else}
              <button type="button" class="pill" class:primary={on} role="radio" aria-checked={on} tabindex={on ? 0 : -1}
                disabled={busy} onclick={() => pick(item, option.value)}>{option.label}</button>
            {/if}
          {/each}
        </div>
        {#if picked[item.id] === "other"}
          <div class="other" use:reveal>
            <CalendarDateTime bind:value={other[item.id]} label={checkin ? "New" : "On"} timed={!checkin && item.area === "calendar"} />
            {#if checkin}<button type="button" class="pill primary" disabled={busy || !choice(item)} onclick={() => send("approve")}>Move</button>{/if}
          </div>
        {/if}
      {:else}
        <p class="answer" class:failed={item.result?.status === "failed"}>{answer(item)}</p>
      {/if}
    </div>
  {/each}
  {#if pending}
    <footer>
      <button type="button" class="text-button" disabled={busy} onclick={() => send("dismiss")}>Not now</button>
      {#if !checkin}<button type="button" class="pill primary" disabled={busy || !ready} onclick={() => send("approve")}>Add</button>{/if}
    </footer>
  {/if}
</article>

<style>
  /* Sits under the reply that asks, in line with its text; no card of its own. */
  .confirm {
    display: grid;
    gap: 10px;
    margin-left: 21px;
  }
  .item {
    display: grid;
    gap: 8px;
  }
  p {
    margin: 0;
    font-size: 13px;
    line-height: 1.45;
    overflow-wrap: anywhere;
  }
  .answer {
    color: var(--muted);
  }
  .answer.failed {
    color: var(--danger);
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .pill {
    min-height: 30px;
    padding: 4px 13px;
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }
  .other {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 8px;
    max-width: 22rem;
    font-size: 12px;
    color: var(--muted);
  }
  .other :global(.date-time-field) {
    flex: 1;
  }
  footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding-top: 8px;
    border-top: 1px solid var(--hairline);
  }
  footer .text-button {
    color: var(--muted);
    font-size: 13px;
  }
  footer .text-button:hover {
    color: var(--ink);
  }
</style>
