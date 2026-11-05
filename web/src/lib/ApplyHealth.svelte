<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "./api";

  /**
   * Apply → Health: how often apply-ai leaves a job ready for review without the owner
   * (unattended ready rate, per job, Wilson 95% interval), per ATS family, plus the top
   * causes of the rest and how many kitty-filled fields the owner kept as filled.
   * Read-only; GET /api/apply/health loads once when the panel opens.
   */
  let { id = "apply-health" }: { id?: string } = $props();

  type Family = {
    family: string;
    jobs: number;
    ready: number;
    closed: number;
    urr: number | null;
    low: number | null;
    high: number | null;
    insufficient: boolean;
    ready_no_input: number;
    urr_no_input: number | null;
    no_input_low: number | null;
    no_input_high: number | null;
  };
  type Outcome = "ready" | "needs_owner" | "unsupported" | "failed" | "closed";
  type Health = {
    min_n: number;
    confidence: number;
    attempts: number;
    last_attempt_at: string | null;
    overall: Family;
    families: Family[];
    causes: { cause: string; label: string; outcome: Outcome; jobs: number; attempts: number }[];
    correctness: {
      class: string;
      compared: number;
      edited: number;
      rate: number | null;
      low: number | null;
      high: number | null;
      insufficient: boolean;
    }[];
  };

  const OUTCOMES: Record<Outcome, string> = {
    ready: "Ready",
    needs_owner: "Needs you",
    unsupported: "Unsupported",
    failed: "Failed",
    closed: "Closed",
  };
  const CLASSES: Record<string, string> = {
    profile: "Profile",
    saved_answer: "Saved answer",
    confirmed_rule: "Confirmed rule",
    resume_derived: "From resume",
    ai_draft: "AI draft",
    consent_ruling: "Consent ruling",
    account: "Account",
  };

  let health = $state<Health | null>(null);
  let loading = $state(false);
  let error = $state("");
  let mounted = false;

  const percent = (value: number) => `${Math.round(value * 100)}%`;
  /** "40% (23–59%)", or "insufficient data (n = 5)" below the minimum sample. */
  function rate(value: number | null, low: number | null, high: number | null, n: number, insufficient: boolean) {
    if (insufficient || value === null || low === null || high === null) return `insufficient data (n = ${n})`;
    return `${percent(value)} (${Math.round(low * 100)}–${percent(high)})`;
  }
  const outcomeLabel = (value: string) => OUTCOMES[value as Outcome] ?? value.replaceAll("_", " ");
  const classLabel = (value: string) => CLASSES[value] ?? value.replaceAll("_", " ");
  const when = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  };

  async function load() {
    loading = true;
    error = "";
    try {
      const next = await api<Health>("/apply/health");
      if (mounted) health = next;
    } catch (cause) {
      if (mounted) error = cause instanceof Error ? cause.message : "Unable to load apply health";
    } finally {
      if (mounted) loading = false;
    }
  }

  onMount(() => {
    mounted = true;
    void load();
    return () => {
      mounted = false;
    };
  });
</script>

<section {id} class="apply-health" aria-labelledby={`${id}-title`}>
  <header class="head">
    <h3 id={`${id}-title`}>Apply health</h3>
    {#if health?.last_attempt_at}<span class="meta">Last attempt {when(health.last_attempt_at)}</span>{/if}
    <button type="button" class="text-button refresh" onclick={load} disabled={loading}>Refresh</button>
  </header>

  {#if loading && !health}
    <p class="muted" role="status">Loading…</p>
  {:else if health && health.attempts === 0}
    <p class="muted">No apply attempts recorded yet</p>
  {:else if health}
    {@const overall = health.overall}
    <p class="overall" title="Jobs left ready for your review without your input, with a Wilson 95% interval. Closed postings are excluded.">
      <span class="field-label">Ready unattended</span>
      <span class="figure" data-overall
        >{overall.insufficient || overall.urr === null
          ? `insufficient data (n = ${overall.jobs})`
          : `${rate(overall.urr, overall.low, overall.high, overall.jobs, false)}, n = ${overall.jobs}`}</span
      >
      {#if !overall.insufficient && overall.urr_no_input !== null}
        <span class="meta">without any input {rate(overall.urr_no_input, overall.no_input_low, overall.no_input_high, overall.jobs, false)}</span>
      {/if}
    </p>

    <h4 id={`${id}-families`}>By form provider</h4>
    {#if health.families.length}
      <div class="table-scroll">
        <table aria-labelledby={`${id}-families`}>
          <thead>
            <tr>
              <th scope="col">Provider</th>
              <th scope="col" class="num">Jobs</th>
              <th scope="col" class="num">Ready</th>
              <th scope="col">Ready rate</th>
              <th scope="col" class="num" title="Closed postings, removed from the job count">Closed</th>
            </tr>
          </thead>
          <tbody>
            {#each health.families as family (family.family)}
              <tr data-family={family.family}>
                <th scope="row">{family.family}</th>
                <td class="num">{family.jobs}</td>
                <td class="num">{family.ready}</td>
                <td class:insufficient={family.insufficient || family.urr === null}
                  >{rate(family.urr, family.low, family.high, family.jobs, family.insufficient)}</td
                >
                <td class="num">{family.closed}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}
      <p class="muted">No live jobs attempted</p>
    {/if}

    <h4 id={`${id}-causes`}>Top causes</h4>
    {#if health.causes.length}
      <div class="table-scroll">
        <table aria-labelledby={`${id}-causes`}>
          <thead>
            <tr>
              <th scope="col">Cause</th>
              <th scope="col">Outcome</th>
              <th scope="col" class="num">Jobs</th>
            </tr>
          </thead>
          <tbody>
            {#each health.causes as cause (cause.cause)}
              <tr data-cause={cause.cause}>
                <th scope="row">{cause.label}</th>
                <td
                  ><span class="outcome"
                    ><span class="dot" data-outcome={cause.outcome} aria-hidden="true"></span>{outcomeLabel(cause.outcome)}</span
                  ></td
                >
                <td class="num" title={`${cause.attempts} ${cause.attempts === 1 ? "attempt" : "attempts"}`}>{cause.jobs}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}
      <p class="muted">No open causes</p>
    {/if}

    <h4 id={`${id}-correctness`} title="Share of kitty-filled fields you left unchanged before submitting">Fill correctness</h4>
    {#if health.correctness.length}
      <div class="table-scroll">
        <table aria-labelledby={`${id}-correctness`}>
          <thead>
            <tr>
              <th scope="col">Source</th>
              <th scope="col" class="num">Compared</th>
              <th scope="col" class="num">Edited</th>
              <th scope="col">Kept as filled</th>
            </tr>
          </thead>
          <tbody>
            {#each health.correctness as row (row.class)}
              <tr data-class={row.class}>
                <th scope="row">{classLabel(row.class)}</th>
                <td class="num">{row.compared}</td>
                <td class="num">{row.edited}</td>
                <td class:insufficient={row.insufficient || row.rate === null}
                  >{rate(row.rate, row.low, row.high, row.compared, row.insufficient)}</td
                >
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}
      <p class="muted">No submitted forms compared yet</p>
    {/if}
  {/if}

  {#if error}
    <p class="error-banner" role="alert">
      {error}<button type="button" class="button" onclick={load} disabled={loading}>Retry</button>
    </p>
  {/if}
</section>

<style>
  .apply-health {
    margin-top: 12px;
    border-top: 1px solid var(--border);
    padding-top: 10px;
  }
  .head {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 10px;
  }
  h3 {
    margin: 0;
    font-size: var(--fs-heading);
    font-weight: 600;
  }
  h4 {
    margin: 14px 0 0;
    padding-top: 10px;
    border-top: 1px solid var(--border);
    font-size: var(--fs-label);
    font-weight: 600;
    color: var(--ink);
  }
  .refresh {
    margin-left: auto;
  }
  .meta {
    font-family: var(--font-mono);
    font-size: var(--fs-chrome);
    color: var(--muted);
  }
  .overall {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 10px;
    margin: 10px 0 0;
  }
  .field-label {
    font-size: var(--fs-label);
    font-weight: 600;
    color: var(--muted);
  }
  .figure {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: var(--fs-subheading);
    color: var(--ink);
  }
  .table-scroll {
    overflow-x: auto;
    margin-top: 6px;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: var(--fs-control);
  }
  th,
  td {
    height: var(--control-h);
    padding: 0 10px 0 0;
    text-align: left;
    white-space: nowrap;
    border-bottom: 1px solid var(--border);
  }
  thead th {
    font-size: var(--fs-label);
    font-weight: 600;
    color: var(--muted);
  }
  tbody th {
    font-weight: 500;
    color: var(--ink);
  }
  td {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    color: var(--ink);
  }
  .num {
    text-align: right;
  }
  td.insufficient {
    font-family: var(--font-ui);
    color: var(--muted);
    /* The drawer is narrow: let the long note wrap so every column stays visible. */
    white-space: normal;
    line-height: 1.3;
    padding-block: 4px;
  }
  .outcome {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: var(--font-ui);
  }
  /* Q3 status dot: 6px, one theme role per outcome. */
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 999px;
    background: var(--subtle);
    flex-shrink: 0;
  }
  .dot[data-outcome="ready"] {
    background: var(--success);
  }
  .dot[data-outcome="needs_owner"] {
    background: var(--warning);
  }
  .dot[data-outcome="failed"] {
    background: var(--error);
  }
  .muted {
    margin: 8px 0 0;
  }
</style>
