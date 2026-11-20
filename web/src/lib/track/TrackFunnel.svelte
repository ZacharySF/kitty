<script lang="ts" module>
  export type FunnelStage = { id: string; label: string; reached: number; now: number; tone: string };
  export type Closed = { rejected: number; ghosted: number; withdrawn: number };
</script>

<script lang="ts">
  import Icon from "../Icon.svelte";
  /** Track's top strip: how many applications reached each stage, and the share that moved on. */
  let { stages, closed }: { stages: FunnelStage[]; closed: Closed } = $props();
  const total = $derived(stages[0]?.reached ?? 0);
  const closedTotal = $derived(closed.rejected + closed.ghosted + closed.withdrawn);
</script>

<section class="funnel" aria-label="Search funnel">
  {#each stages as step, index (step.id)}
    {#if index > 0}
      {@const before = stages[index - 1]}
      {@const rate = before.reached ? Math.round((step.reached / before.reached) * 100) : null}
      <div
        class="rate"
        role="note"
        aria-label={rate === null ? `${step.label}: nothing to move on yet` : `${rate}% of ${before.label} moved on to ${step.label}`}
      >
        <Icon name="arrow" size={13} /><b>{rate === null ? "–" : `${rate}%`}</b>
      </div>
    {/if}
    <div class="step" role="group" aria-label={step.label} style:--tone={step.tone}>
      <span class="label"><i></i>{step.label}</span>
      <strong title={`${step.reached} reached ${step.label.toLowerCase()} or further`}>{step.reached}</strong>
      <span class="bar"><span style:width={`${total ? (step.reached / total) * 100 : 0}%`}></span></span>
      <small>{step.now} here now</small>
    </div>
  {/each}
  <div class="step closed" role="group" aria-label="Closed">
    <span class="label"><i></i>Closed</span>
    <strong>{closedTotal}</strong>
    <small class="split"
      ><span class="rejected">{closed.rejected} rejected</span><span>{closed.ghosted} no response</span><span
        >{closed.withdrawn} withdrawn</span
      ></small
    >
  </div>
</section>

<style>
  .funnel {
    display: grid;
    grid-template-columns: 1fr auto 1fr auto 1fr auto 1fr 0.9fr;
    align-items: stretch;
    gap: 6px;
    min-width: 780px;
  }
  .step {
    display: grid;
    align-content: start;
    gap: 4px;
    padding: 10px 12px 11px;
    border: 1px solid color-mix(in srgb, var(--hairline) 80%, transparent);
    border-radius: 12px;
    background:
      linear-gradient(160deg, color-mix(in srgb, var(--tone) 12%, transparent), transparent 70%),
      color-mix(in srgb, var(--solid-panel) 45%, transparent);
  }
  .step.closed {
    --tone: var(--subtle);
    margin-left: 10px;
    border-style: dashed;
    background: transparent;
  }
  .label {
    display: flex;
    align-items: center;
    gap: 7px;
    color: var(--muted);
    font: 500 10.5px var(--font-ui);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .label i {
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--tone);
  }
  strong {
    color: var(--ink);
    font: 600 24px / 1.1 var(--font-ui);
    font-variant-numeric: tabular-nums;
  }
  .bar {
    height: 4px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 9%, transparent);
    overflow: hidden;
  }
  .bar span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--tone);
  }
  small {
    color: var(--subtle);
    font: 11px var(--font-ui);
  }
  .split {
    display: grid;
    gap: 2px;
  }
  .split .rejected {
    color: color-mix(in srgb, var(--error) 80%, var(--muted));
  }
  .rate {
    display: grid;
    place-content: center;
    justify-items: center;
    gap: 2px;
    padding: 0 2px;
    color: var(--subtle);
  }
  .rate b {
    color: var(--ink);
    font: 600 11.5px var(--font-ui);
    font-variant-numeric: tabular-nums;
  }
</style>
