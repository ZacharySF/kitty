<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api, safeUrl } from "./api";
  import Questions from "./Questions.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  const pane = useWorkspaceContext();

  let {
    jobIds = [],
    track = "",
    compact = false,
    demo = false,
    onchanged,
  }: {
    jobIds?: number[];
    track?: string;
    compact?: boolean;
    demo?: boolean;
    onchanged?: () => void;
  } = $props();
  type Report = {
    job_id: number;
    company: string;
    title: string;
    status: string;
    filled: string[];
    unanswered: string[];
    note: string | null;
    error: string | null;
    resumed: boolean;
    resume_label: string | null;
  };
  type Status = {
    running: boolean;
    phase: string;
    job_ids: number[];
    reports: Report[];
    observed: { job_id: number; label: string }[];
    error: string | null;
    watched_count: number;
  };
  type Plan = {
    fingerprint: string;
    mode: string;
    track: string | null;
    cap: number;
    job_ids: number[];
    jobs: {
      id: number;
      company: string;
      title: string;
      url: string;
      apply_url: string | null;
      ats: string;
      fill_state: string;
      blocker: string | null;
      retained_tab: boolean;
      resume: { label: string; available: boolean } | null;
      warnings: string[];
    }[];
    left: { job_id: number; company: string; title: string; reason: string }[];
    note: string;
    watch_hours: number;
    firefox_configured: boolean;
    known_answers: number;
    authentication: {
      enabled: boolean;
      allow_account_creation: boolean;
      approved_origins: string[];
      configured_origins: string[];
      credentials_available: boolean;
      credential_problem: string | null;
      google_email: string | null;
      google_available: boolean;
      mode: "auto" | "configured" | "login" | "create" | "google";
    };
  };
  let status = $state<Status | null>(null);
  let plan = $state<Plan | null>(null);
  let busy = $state(false);
  let error = $state("");
  let limit = $state(10);
  let watching = $state(true);
  let allowAuth = $state(false);
  let showQuestions = $state(false);
  let showOptions = $state(false);
  const labels: Record<string, string> = {
    idle: "Not running",
    starting: "Opening Firefox",
    filling: "Filling known answers",
    watching: "Waiting for your submission confirmation",
    stopping: "Stopping safely",
    stopped: "Stopped",
    completed: "Preparation finished",
    failed: "Needs attention",
    unattempted: "Not prepared yet",
    ready: "Ready at last check — review again",
    partial: "Needs your answers",
    blocked: "Needs your help",
    unsupported: "Fill this form yourself",
    error: "Could not prepare",
    skipped: "Not included",
    submitted: "Submission observed",
  };
  function label(value: string) {
    return labels[value] || value.replaceAll("_", " ");
  }
  let dialog: HTMLDialogElement;
  let cancel = $state<HTMLButtonElement>();
  let questionPanel = $state<{ focusQuestion: () => void }>();
  let planInput = $state<Record<string, unknown>>({});
  let timer: ReturnType<typeof setTimeout> | undefined;
  let mounted = $state(false);
  let observedCount = 0;
  let previousRunning = false;
  let previousReports = "";

  async function refresh() {
    if (!pane.active) return;
    try {
      const next = await api<Status>("/automation/status");
      if (!mounted) return;
      status = next;
      const reportStamp = next.reports.map(report =>
        `${report.job_id}:${report.status}:${(report.note || "").startsWith("closed:")}`,
      ).join("|");
      if (
        next.observed.length !== observedCount ||
        (previousRunning && !next.running) || reportStamp !== previousReports
      )
        onchanged?.();
      observedCount = next.observed.length;
      previousRunning = next.running;
      previousReports = reportStamp;
      clearTimeout(timer);
      if (next.running && pane.active)
        timer = setTimeout(() => {
          void refresh();
        }, 1800);
    } catch (cause) {
      if (mounted)
        error =
          cause instanceof Error
            ? cause.message
            : "Unable to load browser status";
    }
  }
  $effect(() => {
    if (!pane.active) {
      clearTimeout(timer);
      allowAuth = false;
      plan = null;
      if (dialog?.open) dialog.close();
    } else if (mounted) void refresh();
  });
  async function inspect(
    mode: "selected" | "queue" | "queued" | "top" | "favorites",
    ids?: number[],
  ) {
    if (!pane.active || busy || dialog?.open) return;
    busy = true;
    error = "";
    allowAuth = false;
    try {
      planInput = {
        mode,
        ...(track && !ids ? { track } : {}),
        limit,
        ...(mode === "selected" ? { job_ids: ids ?? jobIds } : {}),
      };
      plan = await api<Plan>("/automation/preflight", planInput);
      allowAuth = plan.authentication.approved_origins.length > 0 &&
        (plan.authentication.mode === "google"
          ? plan.authentication.google_available : plan.authentication.credentials_available);
      await tick();
      if (!pane.active) { plan = null; allowAuth = false; return; }
      dialog.showModal();
      cancel?.focus({ preventScroll: true });
      dialog.scrollTop = 0;
    } catch (cause) {
      error =
        cause instanceof Error ? cause.message : "Unable to prepare a plan";
    } finally {
      busy = false;
    }
  }
  async function start() {
    if (demo || busy || !plan?.job_ids.length) return;
    busy = true;
    error = "";
    try {
      status = await api<Status>("/automation/start", {
        ...planInput,
        confirmed: true,
        watch: watching,
        authorize_authentication: allowAuth,
        fingerprint: plan.fingerprint,
      });
      dialog.close();
      onchanged?.();
      await refresh();
    } catch (cause) {
      error =
        cause instanceof Error
          ? cause.message
          : "Unable to start browser preparation";
    } finally {
      busy = false;
    }
  }
  async function stop() {
    busy = true;
    error = "";
    try {
      status = await api<Status>("/automation/stop", {});
      await refresh();
    } catch (cause) {
      error =
        cause instanceof Error
          ? cause.message
          : "Unable to stop browser worker";
    } finally {
      busy = false;
    }
  }
  export async function reviewSelected() {
    await inspect("selected");
  }
  export function handleKey(event: KeyboardEvent): boolean {
    const target = event.target as HTMLElement | null;
    if (
      !pane.active ||
      event.defaultPrevented ||
      event.ctrlKey ||
      event.metaKey ||
      event.altKey ||
      target?.closest(
        "input:not([type='checkbox']):not([type='radio']), textarea, select, [contenteditable]:not([contenteditable='false']), dialog",
      ) ||
      dialog?.open
    )
      return false;
    if (event.key === "f") {
      event.preventDefault();
      void inspect(jobIds.length ? "selected" : "queue");
      return true;
    }
    if (event.key === "F") {
      event.preventDefault();
      void inspect(jobIds.length ? "selected" : "favorites");
      return true;
    }
    if (event.key === "w") {
      event.preventDefault();
      void stop();
      return true;
    }
    if (event.key === "q") {
      event.preventDefault();
      showOptions = true;
      showQuestions = true;
      void tick().then(() => { if (pane.active) questionPanel?.focusQuestion(); });
      return true;
    }
    return false;
  }
  onMount(() => {
    mounted = true;
    return () => {
      mounted = false;
      clearTimeout(timer);
    };
  });
</script>

<section
  class:panel={!compact}
  class="automation"
  class:compact
  aria-label="Application preparation"
>
  <header class="row">
    <div>
      <h3>
        {compact ? "Prepare this application" : "Review before preparing"}
      </h3>
      <p class="muted">
        First review the jobs and resumes. Then fill known answers in Firefox.
        Unknown answers stay blank; you review every form and submit yourself.
      </p>
    </div>
    <div class="row actions">
      {#if jobIds.length}<button
          class="button primary"
          onclick={() => inspect("selected")}
          disabled={busy}
          >{compact
            ? "Fill this job for review"
            : `Fill ${jobIds.length} selected for review`}</button
        >{:else}<p class="muted">
          Select jobs with the checkboxes, or choose a group below.
        </p>{/if}
      {#if status?.running}<button
          class="button"
          onclick={stop}
          disabled={busy || status.phase === "stopping"}
          >Stop preparation / monitoring</button
        >{/if}
    </div>
  </header>
  <details bind:open={showOptions} class="preparation-options">
    <summary>More preparation options &amp; browser status</summary>
    {#if !compact}
      <p class="muted">
        Choose another group to review. These options use the category filter,
        not the other search filters.
      </p>
      <div class="row">
        <button class="button" onclick={() => inspect("queue")} disabled={busy}
          >Review recommended jobs (A &amp; B)</button
        >
        <button class="button" onclick={() => inspect("queued")} disabled={busy}
          >Review jobs added to queue</button
        >
        <button class="button" onclick={() => inspect("top")} disabled={busy}
          >Review all new &amp; queued jobs</button
        >
        <button
          class="button"
          onclick={() => inspect("favorites")}
          disabled={busy}>Review saved jobs</button
        >
      </div>
      <p class="muted">
        A means highest priority for review, B means worth considering, and C
        means lower priority or filtered out. A rank is not a guarantee of
        eligibility.
      </p>
    {/if}
    {#if !compact}<label class="limit"
        >Maximum jobs this time <input
          type="number"
          min="1"
          max="1000"
          step="1"
          bind:value={limit}
        />
        <span class="muted"
          >Also limited by your saved preparation settings{track
            ? ` · category: ${track}`
            : " · all categories"}.</span
        ></label
      >{/if}
    <button class="button" onclick={refresh} disabled={busy}
      >Refresh browser status</button
    >
    <button
      class="button"
      aria-expanded={showQuestions}
      onclick={() => (showQuestions = !showQuestions)}
      >{showQuestions ? "Hide" : "Show"} screening questions</button
    >
    {#if showQuestions}<Questions
        bind:this={questionPanel}
        jobId={compact && jobIds.length === 1 ? jobIds[0] : undefined}
        {demo}
      />{/if}
  </details>
  {#if status}
    <div aria-live="polite">
      <span class="badge">Browser: {label(status.phase)}</span
      >{#if status.running}<span class="muted">
          {status.watched_count} tabs watched</span
        >{/if}
    </div>
    {#if status.phase === "stopping"}<p class="muted">
        Stopping at the next safe boundary; an in-flight form operation may
        finish. Application tabs stay open.
      </p>{/if}
    {#if status.error}<p class="error-banner" role="alert">
        {status.error}
      </p>{/if}
    {#if status.reports.length}<details open>
        <summary>Latest preparation · {status.reports.length} jobs</summary>
        <ul class="reports">
          {#each status.reports as report (report.job_id)}<li>
              <strong
                >#{report.job_id} · {report.company} — {report.title}</strong
              > <span class="badge">{label(report.status)}</span>
              <p>
                {report.filled.length} fields filled{report.resumed
                  ? " · Reused your existing application tab"
                  : ""}{report.resume_label
                  ? ` · Resume: ${report.resume_label}`
                  : ""}
              </p>
              {#if report.filled.length}<p>
                  Completed: {report.filled.join("; ")}
                </p>{/if}
              {#if report.unanswered.length}<p>
                  Needs your answers: {report.unanswered.join("; ")}
                </p>{/if}
              {#if report.note || report.error}<p class="muted">
                  {report.error || report.note}
                </p>{/if}
              {#if ["partial", "blocked", "error"].includes(report.status) &&
                !/^(closed|listing):/.test(report.note || "")}
                {#if /^(login|consent|captcha|verification):/.test(report.note || "")}
                  <p>Complete that step in Firefox, then continue here. Existing answers are kept.</p>
                {/if}
                <button class="button" onclick={() => inspect("selected", [report.job_id])}
                  disabled={busy || (status.running && status.phase !== "watching")}>
                  Continue filling
                </button>
              {/if}
            </li>{/each}
        </ul>
      </details>{/if}
    {#each status.observed as item (item.job_id)}<p class="observed">
        Observed confirmation: {item.label}. Application and follow-ups
        recorded.
      </p>{/each}
  {/if}
  <p class="muted safety">
    You review consent, security checks and final submission. Leaving this
    screen does not stop preparation.
  </p>
  {#if demo}<p class="muted">
      Demo: you can review sample plans, but Firefox preparation is disabled.
    </p>{/if}
  {#if status?.running}<p class="muted" role="status">
      {label(status.phase)}. Keep Firefox open to review your applications.
    </p>{/if}
  {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
</section>

<dialog bind:this={dialog} aria-label="Review preparation plan">
  <h3>Review your autofill plan</h3>
  {#if plan}
    <p>
      {plan.jobs.length}
      {plan.jobs.length === 1 ? "job" : "jobs"} included · maximum {plan.cap} this
      time · {plan.known_answers}
      saved answers
    </p>
    <p>
      Check the jobs and resumes below. kitty! fills your saved details and
      leaves unknown answers blank.
    </p>
    {#if demo}<p class="muted">
        This is a demo: you can review the plan, but no employer pages will
        open.
      </p>
    {:else if !plan.firefox_configured}<p class="error-banner">
        Firefox is not configured. Install Firefox or set its location in your
        configuration before starting.
      </p>{/if}
    <ul class="reports">
      {#each plan.jobs as job (job.id)}<li>
          <strong>#{job.id} · {job.company} — {job.title}</strong>
          {#if safeUrl(job.apply_url || job.url)}<p>
              <a
                href={safeUrl(job.apply_url || job.url)}
                target="_blank"
                rel="noopener noreferrer"
                >Review destination: {job.apply_url || job.url}</a
              >
            </p>{/if}
          <p>
            {job.retained_tab
              ? "Will try your existing application tab"
              : "Will open a new application tab"} · {label(job.fill_state)}
          </p>
          <p>
            Resume: {job.resume?.label || "None chosen"}{job.resume &&
            !job.resume.available
              ? " — unavailable or changed"
              : ""}
          </p>
          {#if job.blocker}<p class="muted">
              Previous blocker: {job.blocker}
            </p>{/if}{#each job.warnings as warning}<p class="warning">
              {warning}
            </p>{/each}
          <details>
            <summary>Application website details</summary>
            <p>Form provider: {job.ats}</p>
          </details>
        </li>{/each}
    </ul>
    {#if plan.left.length}<details>
        <summary>Jobs not included · {plan.left.length}</summary>
        <ul>
          {#each plan.left as job}<li>
              #{job.job_id}
              {job.company}: {job.reason}
            </li>{/each}
        </ul>
      </details>{/if}
    <details>
      <summary>How this plan is protected</summary>
      <p class="muted">{plan.note}</p>
    </details>
    <label class="check"
      ><input
        type="checkbox"
        bind:checked={watching}
        disabled={plan.watch_hours <= 0}
      />
      Record my application when a supported confirmation page appears after I submit.
      Watch for up to {plan.watch_hours} hours.</label
    >
    <div class="auth">
      <h4>Account access</h4>
      {#if plan.authentication.mode === "google"}
        <p>Reuse the signed-in Google account <strong>{plan.authentication.google_email}</strong>. Google passwords and permission screens stay manual.</p>
      {:else}
        <p>kitty! detects sign-in or account creation and uses your saved email and password. No account-type selection is needed.</p>
        {#if plan.authentication.credential_problem}<p class="warning">{plan.authentication.credential_problem}</p>{/if}
      {/if}
      <details>
        <summary>Approved account destinations</summary>
        <ul>{#each plan.authentication.approved_origins as origin}<li><code>{origin}</code></li>{:else}<li>No eligible HTTPS destination.</li>{/each}</ul>
      </details>
      <label class="check">
        <input type="checkbox" bind:checked={allowAuth} disabled={busy ||
          !(plan.authentication.mode === "google" ? plan.authentication.google_available : plan.authentication.credentials_available) ||
          !plan.authentication.approved_origins.length} />
        {plan.authentication.mode === "google" ? "Use the reviewed signed-in Google account" : "Sign in or create required employer accounts with my saved credentials"}
      </label>
      <p class="muted">Only reviewed HTTPS destinations receive credentials.
        {#if plan.authentication.allow_account_creation && plan.authentication.mode !== "google"}
          Required routine account terms are included.
        {/if}
        Other consent, CAPTCHA, MFA, email verification and final submission stay manual.
      </p>
    </div>
    <p>
      Continuing opens these employer pages and may upload the resumes listed
      above. Review every filled form in Firefox, then press Submit yourself.
      Changes to this plan require another review.
    </p>
    <div class="row">
      <button bind:this={cancel} class="button" onclick={() => dialog.close()}
        >Cancel</button
      ><button
        class="button primary"
        onclick={start}
        disabled={demo ||
          busy ||
          (status?.running && status.phase !== "watching") ||
          !plan.job_ids.length ||
          !plan.firefox_configured}>Prepare in Firefox — I submit myself</button
      >
    </div>
  {/if}
  {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
</dialog>

<style>
  .automation {
    padding: 1rem;
  }
  .compact {
    padding: 0.5rem 0;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    align-items: center;
  }
  .actions {
    margin-left: auto;
  }
  h3 {
    margin: 0 0 0.4rem;
  }
  p {
    line-height: 1.5;
  }
  .preparation-options {
    margin-top: 1rem;
    border-top: 1px solid var(--border);
    padding-top: 0.8rem;
  }
  summary {
    cursor: pointer;
    color: var(--ink);
    padding: 0.4rem 0;
  }
  a {
    color: var(--accent);
    overflow-wrap: anywhere;
  }
  .limit {
    display: flex;
    gap: 0.6rem;
    align-items: center;
    flex-wrap: wrap;
    margin: 0.7rem 0;
  }
  input[type="number"] {
    width: 5rem;
    padding: 0.4rem;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--ink);
    border-radius: 0.4rem;
  }
  .reports {
    padding-left: 1.2rem;
  }
  .reports li {
    padding: 0.6rem 0;
    border-bottom: 1px solid var(--border);
  }
  .reports p {
    margin: 0.3rem 0;
  }
  .safety {
    font-size: 0.85rem;
  }
  .check {
    display: flex;
    gap: 0.5rem;
    align-items: start;
    margin: 1rem 0;
  }
  .auth {
    border: 1px solid var(--warning);
    padding: 0.7rem;
    border-radius: 0.5rem;
  }
  .auth code {
    overflow-wrap: anywhere;
  }
  .observed {
    color: var(--success);
  }
  .warning {
    color: var(--warning);
  }
  dialog {
    color: var(--ink);
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 0.8rem;
    max-width: 46rem;
    max-height: 85vh;
    width: calc(100% - 3rem);
    overflow: auto;
  }
  dialog::backdrop {
    background: color-mix(in srgb, var(--canvas) 80%, transparent);
  }
  @media (max-width: 600px) {
    .actions {
      margin-left: 0;
    }
    .automation {
      padding: 0.7rem;
    }
  }
</style>
