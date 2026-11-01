<script lang="ts">
  import { untrack } from "svelte";
  import { api, safeUrl, type TaskRecord } from "./api";
  import TaskStatus from "./TaskStatus.svelte";
  import Loader from "./Loader.svelte";

  let {
    section,
    demo = false,
  }: { section: "finder" | "reports" | "mail" | "triage"; demo?: boolean } =
    $props();
  type Candidate = {
    id: number;
    display_name: string;
    area: string | null;
    score: number | null;
    reason: string | null;
    ats_kind: string | null;
    board_url: string | null;
    website: string | null;
    evidence: string[];
    decision: string;
  };
  type Rate = {
    key: string;
    applications: number;
    responses: number;
    response_rate: number;
    advanced: number;
    advance_rate: number;
    offers: number;
    open_count: number;
  };
  type Proposal = {
    event_id: number;
    company: string;
    title: string;
    current_status: string;
    to_status: string;
    occurred_at: string;
    evidence: string | null;
  };
  type Mail = {
    client_present: boolean;
    token_present: boolean;
    ready: boolean;
    scope: string;
    last_check: {
      started_at: string;
      status: string;
      fetched: number;
      proposed: number;
      error: string | null;
    } | null;
    proposals: Proposal[];
  };
  type Estimate = {
    estimate_id: string;
    model: string;
    cap_usd: number;
    to_send: number;
    cached: number;
    rules: number;
    now: { usd: number; input_tokens: number; output_tokens: number };
    batch: { usd: number; input_tokens: number; output_tokens: number };
  };
  type PendingBatch = {
    run_id: number;
    batch_id: string;
    sent: number;
    estimate_usd: number;
  };
  type FinderRun = {
    started_at: string;
    proposed: number;
    sources: { name: string; status: string; found: number; failed: boolean }[];
  };
  let loading = $state(true);
  let busy = $state(false);
  let error = $state("");
  let notice = $state("");
  let task = $state<TaskRecord | null>(null);
  let candidates = $state<Candidate[]>([]);
  let counts = $state<Record<string, number>>({});
  let watchlist = $state<{ name: string; tracks: string[] }[]>([]);
  let lastFinderRun = $state<FinderRun | null>(null);
  let decision = $state("pending");
  let selected = $state(0);
  let cap = $state(15);
  let rates = $state<Record<string, Rate[]>>({});
  let mail = $state<Mail | null>(null);
  let days = $state(30);
  let model = $state("");
  let configuredCap = $state(0);
  let approvedCap = $state(0);
  let tracks = $state<string[]>([]);
  let track = $state("");
  let limit = $state(50);
  let pending = $state<PendingBatch[]>([]);
  let estimate = $state<Estimate | null>(null);
  let confirmation = $state<{
    title: string;
    description: string;
    action: () => Promise<void>;
  } | null>(null);
  const running = $derived(busy || task?.status === "running");
  const titles = {
    finder: "Company finder",
    reports: "Response reports",
    mail: "Read-only Gmail",
    triage: "Job triage",
  };

  async function load() {
    loading = true;
    error = "";
    try {
      if (section === "finder") {
        const data = await api<{
          candidates: Candidate[];
          counts: Record<string, number>;
          watchlist: typeof watchlist;
          last_run: FinderRun | null;
        }>(`/finder?decision=${decision}`);
        candidates = data.candidates;
        counts = data.counts;
        watchlist = data.watchlist;
        lastFinderRun = data.last_run;
        selected = Math.min(selected, Math.max(0, candidates.length - 1));
      } else if (section === "reports") {
        rates = (await api<{ sections: Record<string, Rate[]> }>("/reports"))
          .sections;
      } else if (section === "mail") {
        mail = await api<Mail>("/mail");
      } else {
        const data = await api<{
          model: string;
          cap_usd: number;
          tracks: string[];
          pending: PendingBatch[];
        }>("/triage");
        model = data.model;
        configuredCap = data.cap_usd;
        tracks = data.tracks;
        pending = data.pending;
        if (!estimate) approvedCap = configuredCap;
      }
    } catch (cause) {
      error =
        cause instanceof Error ? cause.message : "Unable to load insights.";
    } finally {
      loading = false;
    }
  }

  async function request(path: string, body: Record<string, unknown> = {}) {
    busy = true;
    error = "";
    notice = "";
    try {
      const result = await api<{ task?: TaskRecord }>(path, body);
      if (result.task) {
        task = result.task;
        if (task.status !== "running") await complete(task);
      } else {
        notice = "Decision saved.";
        await load();
      }
    } catch (cause) {
      error =
        cause instanceof Error ? cause.message : "Action could not complete.";
    } finally {
      busy = false;
    }
  }

  function confirm(
    title: string,
    description: string,
    action: () => Promise<void>,
  ) {
    if (!running) confirmation = { title, description, action };
  }
  async function accept() {
    const action = confirmation?.action;
    confirmation = null;
    if (action) await action();
  }
  function cancelFocus(node: HTMLButtonElement) {
    node.focus();
  }

  async function complete(completed: TaskRecord) {
    task = completed;
    if (section === "triage" && completed.status === "completed") {
      const result = completed.result as Partial<Estimate> | null;
      if (result?.estimate_id) {
        estimate = result as Estimate;
        approvedCap = estimate.cap_usd;
      }
    }
    await load();
  }

  function decide(candidate: Candidate, action: "approve" | "reject") {
    confirm(
      `${action === "approve" ? "Approve" : "Reject"} ${candidate.display_name}?`,
      action === "approve"
        ? "Add this company to your local watchlist and future discovery. This does not apply to a job."
        : "Retain the rejected proposal in history so this company is not proposed again.",
      () => request(`/finder/${candidate.id}/${action}`, { confirm: true }),
    );
  }
  function propose(proposal: Proposal, action: "confirm" | "reject") {
    confirm(
      action === "confirm"
        ? `Confirm ${proposal.to_status}?`
        : "Reject mail proposal?",
      `${proposal.company} — ${proposal.title}. ${action === "confirm" ? `Change application status from ${proposal.current_status} to ${proposal.to_status}.` : "Keep the current application status; retain the rejected evidence."}`,
      () =>
        request(`/mail/proposals/${proposal.event_id}/${action}`, {
          confirm: true,
        }),
    );
  }
  function runTriage(mode: "now" | "batch" | "rules" | "collect") {
    const paid = mode === "now" || mode === "batch";
    if (paid && !estimate) return;
    confirm(
      paid
        ? `Approve ${mode} triage spending?`
        : mode === "rules"
          ? "Apply free rules tiers?"
          : "Collect pending batches?",
      paid
        ? `Send redacted resume context and job descriptions to ${estimate?.model}. Hard maximum $${approvedCap.toFixed(2)} for this run. ${mode === "batch" ? "Batch results may take up to 24 hours." : "Requests are sent one at a time."} No application is submitted.`
        : mode === "rules"
          ? "Recalculate rules-based tiers for all eligible jobs; preserve manually assigned tiers. No network calls or cost."
          : "Contact the configured model provider to retrieve previously submitted batches and apply valid results. This sends no new triage requests.",
      async () => {
        await request("/triage/run", {
          confirm: true,
          mode,
          ...(paid
            ? { estimate_id: estimate?.estimate_id, cap_usd: approvedCap }
            : {}),
        });
        if (paid) estimate = null;
      },
    );
  }
  function keydown(event: KeyboardEvent) {
    const target = event.target as HTMLElement;
    if (
      event.defaultPrevented ||
      event.isComposing ||
      event.ctrlKey ||
      event.metaKey ||
      event.altKey ||
      target.closest(
        "input,textarea,select,button,a,summary,dialog,[role=dialog],[contenteditable]:not([contenteditable=false])",
      )
    )
      return;
    if (confirmation) {
      if (event.key === "Escape") {
        event.preventDefault();
        confirmation = null;
      }
      return;
    }
    if (running || loading) return;
    if (event.key === "u" || (event.key === "r" && section !== "finder")) {
      event.preventDefault();
      void load();
    }
    if (section === "finder") {
      if (event.key === "j" || event.key === "ArrowDown") {
        event.preventDefault();
        selected = Math.min(candidates.length - 1, selected + 1);
      }
      if (event.key === "k" || event.key === "ArrowUp") {
        event.preventDefault();
        selected = Math.max(0, selected - 1);
      }
      const candidate = candidates[selected];
      if (
        candidate?.decision === "pending" &&
        ["a", "Enter", "r", "x"].includes(event.key)
      ) {
        event.preventDefault();
        decide(
          candidate,
          ["a", "Enter"].includes(event.key) ? "approve" : "reject",
        );
      }
      if (event.key === "R" && !demo) {
        event.preventDefault();
        confirm(
          "Run company finder?",
          "Fetch public sources and store proposals without changing the watchlist.",
          () => request("/finder/run", { confirm: true, cap }),
        );
      }
    }
  }
  $effect(() => {
    void section;
    untrack(() => {
      task = null;
      estimate = null;
      confirmation = null;
      notice = "";
      void load();
    });
  });
</script>

<svelte:window onkeydown={keydown} />
<section class="panel insights" aria-labelledby="insights-title">
  <header class="panel-heading">
    <div>
      <p class="eyebrow">Workspace insights</p>
      <h2 id="insights-title">{titles[section]}</h2>
    </div>
    <button class="button" onclick={load} disabled={loading || running}
      >Refresh status</button
    >
  </header>
  <div class="body">
    {#if demo}<p class="muted">
        Demo data only. Network, Gmail authorization and paid operations are
        disabled.
      </p>{/if}
    {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
    {#if notice}<p role="status">{notice}</p>{/if}
    <TaskStatus {task} oncomplete={complete} />
    {#if loading}<Loader label="Loading…" />{/if}

    {#if section === "finder"}
      <p class="muted">
        Discover niche companies from public sources. Review evidence before
        adding a company to your watchlist; nothing is approved automatically.
      </p>
      <form
        class="controls"
        onsubmit={(event) => {
          event.preventDefault();
          confirm(
            "Run company finder?",
            "Fetch public candidate sources and store proposals, without applying or changing the watchlist.",
            () => request("/finder/run", { confirm: true, cap }),
          );
        }}
      >
        <label
          >Proposal cap<input
            type="number"
            min="0"
            max="1000"
            bind:value={cap}
            required
          /></label
        >
        <button class="button primary" disabled={demo || running}
          >Find companies</button
        >
        <label
          >Decision<select bind:value={decision} onchange={load}
            ><option value="pending">Pending ({counts.pending ?? 0})</option
            ><option value="approved">Approved ({counts.approved ?? 0})</option
            ><option value="rejected">Rejected ({counts.rejected ?? 0})</option
            ></select
          ></label
        >
      </form>
      <p class="muted">
        Keyboard: j/k select · a/Enter approve · r/x reject · R run finder · u
        refresh. Actions always require confirmation.
      </p>
      {#if lastFinderRun}<details>
          <summary
            >Last finder run: {lastFinderRun.started_at} · {lastFinderRun.proposed}
            proposed</summary
          >
          <ul>
            {#each lastFinderRun.sources as source}<li>
                {source.name}: {source.status} · {source.found} found{source.failed
                  ? " · Source could not complete."
                  : ""}
              </li>{/each}
          </ul>
        </details>{:else}<p class="muted">
          Finder has not completed a run.
        </p>{/if}
      {#if !loading && candidates.length === 0}<p class="empty-state">
          No {decision} company proposals.
        </p>{/if}
      <div class="cards">
        {#each candidates as candidate, index (candidate.id)}
          <article class:selected={index === selected}>
            <button
              class="candidate-title"
              onclick={() => (selected = index)}
              aria-pressed={index === selected}>{candidate.display_name}</button
            >
            <div class="badges">
              <span class="badge">{candidate.area ?? "Unclassified"}</span><span
                class="badge">Score {candidate.score ?? "—"}</span
              ><span class="badge"
                >{candidate.ats_kind ?? "No supported board"}</span
              >
            </div>
            <p>{candidate.reason ?? "No reason recorded."}</p>
            <ul>
              {#each candidate.evidence as line}<li>{line}</li>{/each}
            </ul>
            <div class="controls">
              {#if candidate.board_url && safeUrl(candidate.board_url)}<a
                  class="button"
                  href={safeUrl(candidate.board_url)}
                  target="_blank"
                  rel="noreferrer">Review board</a
                >{/if}
              {#if candidate.website && safeUrl(candidate.website)}<a
                  class="button"
                  href={safeUrl(candidate.website)}
                  target="_blank"
                  rel="noreferrer">Company website</a
                >{/if}
              {#if candidate.decision === "pending"}<button
                  class="button primary"
                  disabled={running}
                  onclick={() => decide(candidate, "approve")}
                  >Approve to watchlist</button
                ><button
                  class="button"
                  disabled={running}
                  onclick={() => decide(candidate, "reject")}>Reject</button
                >{/if}
            </div>
          </article>
        {/each}
      </div>
      <details>
        <summary>Watchlist ({watchlist.length} companies)</summary>
        <ul>
          {#each watchlist as company}<li>
              {company.name}{company.tracks.length
                ? ` — ${company.tracks.join(", ")}`
                : ""}
            </li>{/each}
        </ul>
        <p class="muted">Manage company configuration in Profile.</p>
      </details>
    {:else if section === "reports"}
      <p class="muted">
        Outcome rates reflect recorded application history. Exports contain all
        stored jobs, applications and status changes, not just the current view.
        CSV exports escape formula-leading cells.
      </p>
      <div class="controls">
        <a
          class="button primary"
          href="/api/reports/export?format=json&dataset=all"
          download>Download all JSON</a
        ><a
          class="button"
          href="/api/reports/export?format=csv&dataset=all"
          download>Download all CSV (ZIP)</a
        >
      </div>
      <div class="controls">
        {#each ["jobs", "applications", "status_events"] as dataset}<a
            class="button"
            href={`/api/reports/export?format=csv&dataset=${dataset}`}
            download>{dataset.replaceAll("_", " ")} CSV</a
          ><a
            class="button"
            href={`/api/reports/export?format=json&dataset=${dataset}`}
            download>{dataset.replaceAll("_", " ")} JSON</a
          >{/each}
      </div>
      {#each Object.entries(rates) as [name, rows]}
        <section aria-label={`Response rates by ${name}`}>
          <h3>By {name}</h3>
          {#if rows.length === 0}<p class="empty-state">
              No applications recorded for this grouping.
            </p>{:else}<div class="table-scroll">
              <table>
                <thead
                  ><tr
                    ><th scope="col">{name}</th><th scope="col">Applied</th><th
                      scope="col">Heard back</th
                    ><th scope="col">Response rate</th><th scope="col">OA+</th
                    ><th scope="col">OA+ rate</th><th scope="col">Offers</th><th
                      scope="col">Open</th
                    ></tr
                  ></thead
                ><tbody
                  >{#each rows as row}<tr
                      ><th scope="row">{row.key}</th><td>{row.applications}</td
                      ><td>{row.responses}</td><td
                        >{(row.response_rate * 100).toFixed(0)}%</td
                      ><td>{row.advanced}</td><td
                        >{(row.advance_rate * 100).toFixed(0)}%</td
                      ><td>{row.offers}</td><td>{row.open_count}</td></tr
                    >{/each}</tbody
                >
              </table>
            </div>{/if}
        </section>
      {/each}
    {:else if section === "mail"}
      <p class="muted">
        Gmail uses only <strong>gmail.readonly</strong>: no sending, deleting or
        labeling. Reading recruiting mail proposes status changes. Only your
        confirmation moves an application.
      </p>
      {#if mail}
        <div class="badges">
          <span class="badge"
            >OAuth client: {mail.client_present ? "Present" : "Missing"}</span
          ><span class="badge"
            >Token: {mail.token_present
              ? "Present (not validated)"
              : "Not authorized"}</span
          >
        </div>
        {#if !mail.client_present}<details open>
            <summary>Set up a read-only Gmail OAuth client</summary>
            <ol>
              <li>Create a Google Cloud project and enable Gmail API.</li>
              <li>
                Configure the OAuth consent screen and add your account as a
                test user.
              </li>
              <li>
                Create a Desktop app OAuth client; download its JSON to the
                local workspace's <code>config/gmail_client.json</code>.
              </li>
              <li>
                Choose Authorize below and complete Google's browser consent. No
                credentials are displayed here.
              </li>
            </ol>
            <p>
              Testing-mode tokens can expire after seven days; authorize again
              when needed.
            </p>
          </details>{/if}
        <div class="controls">
          <button
            class="button"
            disabled={demo || running || !mail.client_present}
            onclick={() =>
              confirm(
                "Authorize read-only Gmail access?",
                "Open Google's consent page in your local browser. You must choose your account and grant read-only access yourself; the token is stored locally and never displayed.",
                () => request("/mail/auth", { confirm: true }),
              )}>Authorize / reconnect</button
          >
        </div>
        <form
          class="controls"
          onsubmit={(event) => {
            event.preventDefault();
            confirm(
              "Read recruiting mail?",
              `Read up to 200 matching messages from the last ${days} days and save proposed status changes locally. No status changes are confirmed automatically.`,
              () => request("/mail/check", { confirm: true, days }),
            );
          }}
        >
          <label
            >Look back (days)<input
              type="number"
              min="1"
              max="3650"
              required
              bind:value={days}
            /></label
          ><button
            class="button primary"
            disabled={demo || running || !mail.ready}>Check Gmail</button
          >
        </form>
        {#if mail.last_check}<p>
            Last check: {mail.last_check.started_at} · {mail.last_check.status} ·
            {mail.last_check.fetched} new messages · {mail.last_check.proposed} proposals
          </p>
          {#if mail.last_check.error}<p class="error-banner">
              {mail.last_check.error}
            </p>{/if}{:else}<p class="muted">
            Gmail has not been checked.
          </p>{/if}
        <h3>Pending status proposals</h3>
        {#if mail.proposals.length === 0}<p class="empty-state">
            No proposals waiting for your review.
          </p>{/if}
        <div class="cards">
          {#each mail.proposals as proposal (proposal.event_id)}<article>
              <h3>{proposal.company} — {proposal.title}</h3>
              <p>
                <span class="badge">{proposal.current_status}</span> →
                <span class="badge">{proposal.to_status}</span>
                · {proposal.occurred_at}
              </p>
              <blockquote>
                {proposal.evidence ?? "No evidence recorded."}
              </blockquote>
              <div class="controls">
                <button
                  class="button primary"
                  disabled={running}
                  onclick={() => propose(proposal, "confirm")}
                  >Confirm status change</button
                ><button
                  class="button"
                  disabled={running}
                  onclick={() => propose(proposal, "reject")}
                  >Reject proposal</button
                >
              </div>
            </article>{/each}
        </div>
      {/if}
    {:else}
      <p class="muted">
        Free rules and saved answers are reused. Paid triage sends
        contact-redacted resume context and job descriptions to the configured
        provider only after a fresh estimate and your explicit approval. It
        never submits applications.
      </p>
      <p>
        Model: <strong>{model || "Loading…"}</strong> · Configured hard cap:
        <strong>${configuredCap.toFixed(2)}</strong>
      </p>
      <form
        class="controls"
        onsubmit={(event) => {
          event.preventDefault();
          estimate = null;
          void request("/triage/estimate", { track: track || null, limit });
        }}
      >
        <label
          >Track<select bind:value={track} onchange={() => (estimate = null)}
            ><option value="">All tracks</option>{#each tracks as name}<option
                value={name}>{name}</option
              >{/each}</select
          ></label
        >
        <label
          >Maximum paid jobs<input
            type="number"
            min="1"
            max="1000"
            required
            bind:value={limit}
            oninput={() => (estimate = null)}
          /></label
        >
        <button class="button primary" disabled={running}
          >Estimate (no paid call)</button
        >
        <button
          class="button"
          type="button"
          disabled={running}
          onclick={() => runTriage("rules")}>Apply free rules only</button
        >
      </form>
      <p class="muted">
        The paid-job limit and track apply to model requests; free rules tiers
        cover the full workspace.
      </p>
      {#if estimate}<article class="estimate">
          <h3>Review estimate</h3>
          <p>
            {estimate.to_send} jobs to send · {estimate.cached} cached answers · {estimate.rules}
            free rule updates
          </p>
          <dl>
            <div>
              <dt>Now</dt>
              <dd>
                Up to ${estimate.now.usd.toFixed(4)} · {estimate.now.input_tokens.toLocaleString()}
                input / {estimate.now.output_tokens.toLocaleString()} output tokens
              </dd>
            </div>
            <div>
              <dt>Batch</dt>
              <dd>
                Up to ${estimate.batch.usd.toFixed(4)} · {estimate.batch.input_tokens.toLocaleString()}
                input / {estimate.batch.output_tokens.toLocaleString()} output tokens
              </dd>
            </div>
          </dl>
          <p class="muted">
            Conservative estimates; execution stops before exceeding your
            approved cap. Estimates expire after 30 minutes and changes require
            a new estimate.
          </p>
          <label
            >Your maximum spend (USD)<input
              type="number"
              min="0.0001"
              step="any"
              max={estimate.cap_usd}
              bind:value={approvedCap}
            /></label
          >
          <div class="controls">
            <button
              class="button primary"
              disabled={demo ||
                running ||
                !Number.isFinite(approvedCap) ||
                approvedCap <= 0 ||
                approvedCap > estimate.cap_usd}
              onclick={() => runTriage("now")}>Review & approve now</button
            ><button
              class="button"
              disabled={demo ||
                running ||
                !Number.isFinite(approvedCap) ||
                approvedCap <= 0 ||
                approvedCap > estimate.cap_usd}
              onclick={() => runTriage("batch")}>Review & approve batch</button
            >
          </div>
        </article>{/if}
      <h3>Pending batches</h3>
      {#if pending.length === 0}<p class="empty-state">
          No batches waiting.
        </p>{:else}<ul>
          {#each pending as batch}<li>
              {batch.batch_id} · {batch.sent} jobs · estimated ${batch.estimate_usd.toFixed(
                4,
              )}
            </li>{/each}
        </ul>{/if}
      <button
        class="button"
        disabled={demo || running || pending.length === 0}
        onclick={() => runTriage("collect")}>Collect batch results</button
      >
    {/if}
  </div>
</section>

{#if confirmation}
  <div class="modal-backdrop" role="presentation">
    <div
      class="confirm-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="insight-confirm-title"
      tabindex="-1"
      onkeydown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          confirmation = null;
        }
        if (event.key === "Tab") {
          const buttons =
            event.currentTarget.querySelectorAll<HTMLButtonElement>("button");
          const first = buttons[0];
          const last = buttons[buttons.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }
      }}
    >
      <h2 id="insight-confirm-title">{confirmation.title}</h2>
      <p>{confirmation.description}</p>
      <div class="controls">
        <button
          class="button"
          use:cancelFocus
          onclick={() => (confirmation = null)}>Cancel</button
        ><button class="button primary" onclick={accept}>Confirm</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .insights {
    min-width: 0;
  }
  .panel-heading,
  .controls,
  .badges {
    display: flex;
    gap: 0.7rem;
    flex-wrap: wrap;
    align-items: center;
  }
  .panel-heading {
    justify-content: space-between;
  }
  .body {
    padding: 1.25rem;
    display: grid;
    gap: 1.2rem;
  }
  .body > section {
    min-width: 0;
  }
  h2,
  h3,
  p {
    margin: 0;
  }
  h3 {
    font-size: 1rem;
  }
  p,
  li,
  blockquote,
  dd {
    overflow-wrap: anywhere;
  }
  .controls {
    align-items: end;
  }
  label {
    display: grid;
    gap: 0.35rem;
    font-size: 0.85rem;
  }
  input,
  select {
    max-width: 100%;
    padding: 0.6rem;
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--border);
    border-radius: 0.45rem;
  }
  .cards {
    display: grid;
    gap: 1rem;
  }
  article {
    padding: 1rem;
    border: 1px solid var(--border);
    border-radius: 0.7rem;
    display: grid;
    gap: 0.8rem;
    min-width: 0;
  }
  article.selected {
    border-color: var(--accent);
  }
  .candidate-title {
    border: 0;
    background: none;
    color: var(--ink);
    font: inherit;
    font-weight: 650;
    text-align: left;
    padding: 0.2rem 0;
    cursor: pointer;
  }
  .table-scroll {
    overflow: auto;
    margin-top: 0.7rem;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.9rem;
  }
  th,
  td {
    padding: 0.75rem;
    text-align: left;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  dl {
    margin: 0;
    display: grid;
    gap: 0.7rem;
  }
  dt {
    font-weight: 600;
  }
  dd {
    margin: 0.25rem 0 0;
  }
  blockquote {
    margin: 0;
    padding: 0.6rem 1rem;
    border-left: 3px solid var(--border);
  }
  summary {
    cursor: pointer;
  }
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgb(0 0 0 / 0.5);
    display: grid;
    place-items: center;
    z-index: 100;
    padding: 1rem;
  }
  .confirm-dialog {
    width: min(34rem, 100%);
    max-height: 90vh;
    overflow: auto;
    padding: 1.5rem;
    border-radius: 1rem;
    background: var(--surface);
    color: var(--ink);
    display: grid;
    gap: 1rem;
    box-shadow: 0 1rem 4rem rgb(0 0 0 / 0.2);
  }
  @media (max-width: 600px) {
    .body {
      padding: 1rem;
    }
    .controls > label {
      width: 100%;
    }
    th,
    td {
      padding: 0.5rem;
    }
  }
</style>
