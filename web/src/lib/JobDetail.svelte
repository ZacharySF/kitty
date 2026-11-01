<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api, dateLabel, safeUrl, type JobDetail, type Resume } from "./api";
  import Icon from "./Icon.svelte";
  import Automation from "./Automation.svelte";
  import JobTools from "./JobTools.svelte";
  import PostingAvailability from "./PostingAvailability.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  import Loader from "./Loader.svelte";

  let {
    id,
    onclose,
    onchange,
    initialCommand = "",
    demo = false,
  }: {
    id: number;
    onclose: () => void;
    onchange: () => Promise<void>;
    initialCommand?: string;
    demo?: boolean;
  } = $props();
  const pane = useWorkspaceContext();
  const fieldId = (name: string) => `${pane.id}-job-${id}-${name}`;
  let job = $state<JobDetail | null>(null);
  let resumes = $state<Resume[]>([]);
  let error = $state("");
  let busy = $state(false);
  let notes = $state("");
  let confirmation = $state(false);
  let status = $state("interview");
  let resumeId = $state("");
  let message = $state("");
  let heading: HTMLHeadingElement | undefined = $state();
  let automation: { handleKey: (event: KeyboardEvent) => boolean } | undefined =
    $state();
  let tools: { undo: () => Promise<void> } | undefined = $state();
  let revision = $state(0);
  let eventDate = $state("");
  let eventNote = $state("");
  const labels: Record<string, string> = {
    eligible: "Meets known requirements",
    boosted: "Especially suitable",
    ambiguous: "Requirements need review",
    unknown: "Not checked",
    ineligible: "Not eligible",
    new: "Not reviewed",
    queued: "In preparation queue",
    later: "Review later",
    skipped: "Skipped",
    not_eligible: "Not eligible",
    applied: "Submitted",
    oa: "Online assessment",
    interview: "Interview",
    offer: "Offer",
    rejected: "Rejected",
    ghosted: "No response",
    withdrawn: "Withdrawn",
    unattempted: "Not prepared",
    ready: "Ready at last check",
    partial: "Needs your answers",
    blocked: "Needs your help",
    failed: "Needs attention",
    error: "Needs attention",
    unsupported: "Fill this form yourself",
  };
  function label(value: string) {
    return labels[value] || value.replaceAll("_", " ");
  }

  async function changed() {
    job = await api<JobDetail>(`/jobs/${id}`);
    revision += 1;
    await onchange();
  }

  export async function handleKey(event: KeyboardEvent): Promise<boolean> {
    if (!pane.active || event.ctrlKey || event.metaKey || event.altKey || busy) return false;
    if (["F", "w", "q"].includes(event.key) && job && !job.is_active) {
      event.preventDefault();
      message = "Posting unavailable. Recheck availability before preparing it.";
      return true;
    }
    if (["F", "w", "q"].includes(event.key))
      return automation?.handleKey(event) ?? false;
    if (event.key === "r") {
      event.preventDefault();
      await changed();
      return true;
    }
    if (event.key === "*") {
      event.preventDefault();
      await mutate("favorite", !job?.favorite);
      return true;
    }
    if (event.key === "u") {
      event.preventDefault();
      await tools?.undo();
      return true;
    }
    if (event.key === "o" && job) {
      event.preventDefault();
      const url = safeUrl(job.url);
      if (url) window.open(url, "_blank", "noopener,noreferrer");
      return true;
    }
    const control: Record<string, string> = {
      t: "job-tier",
      k: "job-state",
      s: "application-status",
      n: "next-action-date",
      d: "job-deadline",
      R: "resume-choice",
      e: "job-notes",
      a: "submission-confirmation",
    };
    if (control[event.key]) {
      event.preventDefault();
      const target = document.getElementById(fieldId(control[event.key]));
      let disclosure = target?.closest("details");
      while (disclosure) {
        disclosure.open = true;
        disclosure = disclosure.parentElement?.closest("details") ?? null;
      }
      await tick();
      target?.focus();
      return true;
    }
    return false;
  }

  onMount(() => {
    void (async () => {
      try {
        const [detail, library] = await Promise.all([
          api<JobDetail>(`/jobs/${id}`),
          api<{ resumes: Resume[] }>("/resumes"),
        ]);
        job = detail;
        notes = detail.my_notes || "";
        resumes = library.resumes;
        resumeId = detail.resume?.id?.toString() || "";
        await tick();
        if (pane.active) heading?.focus();
        if (
          pane.active && initialCommand &&
          !["t", "k", "d", "n", "u"].includes(initialCommand)
        )
          await handleKey(
            new KeyboardEvent("keydown", { key: initialCommand }),
          );
      } catch (cause) {
        error = (cause as Error).message;
      }
    })();
  });

  async function mutate(
    action: string,
    value?: string | number | boolean | null,
  ) {
    busy = true;
    error = "";
    message = "";
    try {
      await api(`/jobs/${id}`, {
        action,
        value,
        ...(["applied", "status"].includes(action)
          ? { when: eventDate, note: eventNote }
          : {}),
      });
      await changed();
      message =
        action === "applied"
          ? "Submission recorded. No application was sent."
          : "Saved to your workspace.";
      confirmation = false;
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<aside class="detail panel" aria-label="Opportunity details" aria-busy={busy}>
  <div class="panel-heading">
    <span class="eyebrow">Job details</span><button
      class="icon-button"
      aria-label="Close details"
      onclick={onclose}><Icon name="close" /></button
    >
  </div>
  {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
  {#if !job && !error}<Loader kind="view" label="Opening opportunity…" />{/if}
  {#if job}
    <div class="detail-body">
      <div class="detail-company">
        <span class="company-mark">{job.company.slice(0, 2).toUpperCase()}</span
        ><span>{job.company}</span>
      </div>
      <h2 bind:this={heading} tabindex="-1">{job.title}</h2>
      <p class="muted location">
        <Icon name="location" size={16} />{job.location ||
          "Location not provided"}
      </p>
      <div class="tag-row">
        <span class="badge">{job.tier ? `Rank ${job.tier}` : "Not ranked"}</span
        ><span class="badge">{label(job.eligibility)}</span><span class="badge"
          >{label(job.status || job.queue_state)}</span
        >
      </div>
      <div class="detail-actions">
        {#if safeUrl(job.url) && job.is_active}<a
            class="button"
            href={safeUrl(job.url)}
            target="_blank"
            rel="noopener noreferrer"
            >Read original posting <Icon name="external" size={16} /></a
          >{:else if !job.is_active}<span class="badge">Posting unavailable</span>{/if}
        <button
          class="button"
          disabled={busy}
          aria-pressed={job.favorite}
          onclick={() => mutate("favorite", !job?.favorite)}
          ><Icon name="favorites" size={16} />{job.favorite
            ? "Saved"
            : "Save"}</button
        >
        <PostingAvailability jobIds={[id]} {demo} oncomplete={() => changed()} />
      </div>
      <p class="muted" title={job.verification_reason || undefined}>
        {job.verification_checked_at ? `Last checked ${dateLabel(job.verification_checked_at)}` : "Availability not yet checked"}
      </p>
      {#if !job.application}<details class="job-organizing">
          <summary>Organize this job</summary>
          <div class="detail-actions">
            {#if job.queue_state !== "queued"}<button
                class="button"
                disabled={busy}
                onclick={() => mutate("queue")}>Add to preparation queue</button
              >{/if}
            {#if job.queue_state !== "skipped"}<button
                class="button"
                disabled={busy}
                onclick={() => mutate("skip")}>Skip this job</button
              >
            {:else}<button
                class="button"
                disabled={busy}
                onclick={() => mutate("restore")}>Restore this job</button
              >{/if}
          </div>
        </details>{/if}
      {#if message}<p class="success-message" role="status">{message}</p>{/if}
      <div class="detail-section">
        <h3>Is this job right for you?</h3>
        <p>
          {job.tier_reason ||
            job.eligibility_reason ||
            "No fit assessment recorded. Review the posting requirements before applying."}
        </p>
        {#if job.tier_reason && job.eligibility_reason}<p class="muted">
            {job.eligibility_reason}
          </p>{/if}
        <dl class="facts">
          <div>
            <dt>Categories</dt>
            <dd>{job.tracks.join(" · ") || "Unassigned"}</dd>
          </div>
          <div>
            <dt>Posted</dt>
            <dd>{dateLabel(job.posted_at)}</dd>
          </div>
          <div>
            <dt>Deadline</dt>
            <dd>{dateLabel(job.deadline_at)}</dd>
          </div>
        </dl>
      </div>
      <details class="detail-section">
        <summary>Full job description</summary>
        <p class="description">
          {job.description ||
            "No description saved. Open the original posting for details."}
        </p>
      </details>
      <div class="detail-section">
        <h3>Resume for this application</h3>
        <p class="muted">{job.resume?.label || "No resume selected"}</p>
        {#if !job.application}
          <form
            class="inline-form"
            onsubmit={(event) => {
              event.preventDefault();
              void mutate(
                "resume",
                resumeId === "default" ? null : Number(resumeId),
              );
            }}
          >
            <label class="sr-only" for={fieldId("resume-choice")}>Choose a resume</label>
            <select id={fieldId("resume-choice")} bind:value={resumeId}
              ><option value="" disabled>Choose a resume</option><option
                value="default">Use category default</option
              >{#each resumes as resume}<option
                  value={String(resume.id)}
                  disabled={!resume.available}
                  >{resume.label}{resume.available
                    ? ""
                    : " — missing PDF"}</option
                >{/each}</select
            >
            <button
              class="button"
              disabled={busy ||
                (resumeId !== "default" &&
                  !resumes.some(
                    (resume) =>
                      String(resume.id) === resumeId && resume.available,
                  ))}>Choose</button
            >
          </form>
        {:else}<p class="muted">
            This application keeps the resume you submitted with it.
          </p>{/if}
        {#if job.fill_state && job.fill_state !== "unattempted"}<p
            class="muted"
          >
            Last preparation: {label(job.fill_state)}. Check the live form
            again; this only describes the previous attempt.
          </p>{/if}
        {#if job.fill_blocker}<p class="error-banner">
            {job.fill_blocker}
          </p>{/if}
      </div>
      <Automation
        bind:this={automation}
        jobIds={[id]}
        compact
        {demo}
        onchanged={changed}
      />
      <details class="detail-section">
        <summary>Record a submission or update application status</summary>
        {#if job.application}
          <p class="muted">
            Current status: <strong>{label(job.application.status)}</strong>
          </p>
          <form
            class="inline-form"
            onsubmit={(event) => {
              event.preventDefault();
              void mutate("status", status);
            }}
          >
            <label class="sr-only" for={fieldId("application-status")}
              >New application status</label
            >
            <select id={fieldId("application-status")} bind:value={status}
              ><option value="oa">Online assessment</option><option
                value="interview">Interview</option
              ><option value="offer">Offer</option><option value="rejected"
                >Rejected</option
              ><option value="ghosted">Ghosted</option><option value="withdrawn"
                >Withdrawn</option
              ></select
            >
            <button class="button" disabled={busy}>Update</button>
          </form>
          <div class="event-fields">
            <label for={fieldId("event-date")}>Event date</label><input
              id={fieldId("event-date")}
              bind:value={eventDate}
              placeholder="Today, YYYY-MM-DD, or +7"
            /><label for={fieldId("event-evidence")}>Evidence / note</label><input
              id={fieldId("event-evidence")}
              bind:value={eventNote}
              maxlength="20000"
              placeholder="What changed?"
            />
          </div>
          <ol class="history">
            {#each job.application.history as event}<li>
                <span
                  >{event.from_status
                    ? `${label(event.from_status)} → `
                    : ""}{event.to_status
                    ? label(event.to_status)
                    : event.status
                      ? label(event.status)
                      : "Status change"}</span
                >
                <time>{dateLabel(event.occurred_at)}</time>
                <p class="muted">
                  {event.source} · {event.state}{event.undoes_event_id
                    ? ` · undoes #${event.undoes_event_id}`
                    : ""}
                </p>
                {#if event.evidence}<p class="muted">{event.evidence}</p>{/if}
              </li>{/each}
          </ol>
        {:else}
          <p class="muted">
            Apply on the employer’s website, then record it here. kitty! never
            submits for you.
          </p>
          <label class="confirm"
            ><input
              id={fieldId("submission-confirmation")}
              type="checkbox"
              bind:checked={confirmation}
            />I have submitted this application myself.</label
          >
          <div class="event-fields">
            <label for={fieldId("submitted-date")}>Submission date</label><input
              id={fieldId("submitted-date")}
              bind:value={eventDate}
              placeholder="Today or YYYY-MM-DD"
            /><label for={fieldId("submission-evidence")}>Evidence / note</label><input
              id={fieldId("submission-evidence")}
              bind:value={eventNote}
              maxlength="20000"
              placeholder="Optional confirmation reference"
            />
          </div>
          <button
            class="button"
            disabled={busy || !confirmation}
            onclick={() => mutate("applied", true)}
            ><Icon name="check" size={16} />Record submission</button
          >
        {/if}
      </details>
      <details class="detail-section">
        <summary>Your notes</summary>
        <form
          class="notes-form"
          onsubmit={(event) => {
            event.preventDefault();
            void mutate("notes", notes);
          }}
        >
          <label for={fieldId("job-notes")}>Contacts, questions, or your next step</label>
          <textarea
            id={fieldId("job-notes")}
            rows="4"
            bind:value={notes}
            placeholder="Contacts, questions, or a next step…"
          ></textarea>
          <button class="button" disabled={busy}>Save notes</button>
        </form>
      </details>
      <JobTools
        bind:this={tools}
        {id}
        {revision}
        {initialCommand}
        onchange={changed}
      />
    </div>
  {/if}
</aside>

<style>
  .job-organizing {
    margin-top: 14px;
  }
  .job-organizing .detail-actions {
    margin-top: 10px;
  }
  .notes-form {
    margin-top: 14px;
  }
  .event-fields {
    display: grid;
    gap: 8px;
    margin: 14px 0;
    font-size: 12px;
    color: var(--muted);
  }
  .detail {
    align-self: start;
    min-width: 0;
    overflow: hidden;
    position: sticky;
    top: calc(var(--workbench-top, 0px) + 16px);
    max-height: calc(100vh - var(--workbench-top, 0px) - 32px);
    overflow-y: auto;
  }
  .detail-body {
    padding: 24px;
  }
  .detail-company {
    display: flex;
    align-items: center;
    gap: 12px;
    color: var(--muted);
    font-size: 13px;
  }
  h2 {
    font-size: 24px;
    line-height: 1.3;
    margin: 20px 0 10px;
    letter-spacing: -0.025em;
  }
  .location {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .detail-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 24px;
  }
  .detail-section {
    border-top: 1px solid var(--border);
    padding-top: 22px;
    margin-top: 24px;
  }
  .detail-section h3 {
    font-size: 14px;
    margin: 0 0 12px;
  }
  .detail-section p {
    font-size: 13px;
    line-height: 1.7;
  }
  .facts {
    font-size: 12px;
    display: grid;
    gap: 12px;
  }
  .facts div {
    display: grid;
    grid-template-columns: 70px 1fr;
    gap: 12px;
  }
  dt {
    color: var(--muted);
  }
  dd {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .inline-form {
    display: flex;
    gap: 8px;
  }
  .inline-form select {
    min-width: 0;
    flex: 1;
  }
  .confirm {
    display: flex;
    align-items: start;
    gap: 9px;
    font-size: 13px;
    margin: 18px 0;
    line-height: 1.5;
  }
  input[type="checkbox"] {
    margin-top: 4px;
  }
  textarea {
    width: 100%;
    resize: vertical;
    margin-bottom: 10px;
  }
  summary {
    cursor: pointer;
    font-size: 14px;
    padding: 4px 0;
  }
  .description {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .history {
    list-style: none;
    padding: 0;
    font-size: 12px;
  }
  .history li {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: space-between;
    border-left: 1px solid var(--border);
    padding: 8px 12px;
  }
  .history time {
    color: var(--muted);
  }
  .history p {
    width: 100%;
    margin: 0;
  }
  .success-message {
    color: var(--success);
    font-size: 13px;
  }
  @media (max-width: 1100px) {
    .detail {
      position: static;
      max-height: none;
    }
  }
</style>
