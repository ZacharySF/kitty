<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api, dateLabel, safeUrl, type JobDetail, type Resume } from "./api";
  import Icon from "./Icon.svelte";
  import Automation from "./Automation.svelte";
  import JobTools from "./JobTools.svelte";
  import PostingAvailability from "./PostingAvailability.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  import Loader from "./Loader.svelte";
  import { useFocus } from "./rika/focus.svelte";
  import { fitLabel, type JobFit } from "./jobs/table";
  import { tailorForJob } from "./workshopHandoff";
  import type { View } from "./workspace";

  let {
    id,
    onclose,
    onchange,
    onview,
    initialCommand = "",
    demo = false,
    focus = "job",
  }: {
    id: number;
    onclose: () => void;
    onchange: () => Promise<void>;
    /** Opens another page; with it, Tailor resume opens the Resume Workshop for this job. */
    onview?: (view: View) => void;
    initialCommand?: string;
    demo?: boolean;
    /** What Rika is told is open: a job (Jobs) or an application (Track). */
    focus?: "job" | "application";
  } = $props();
  type Tab = "fit" | "posting" | "application";
  const TABS: [Tab, string][] = [["fit", "Fit"], ["posting", "Posting"], ["application", "Application"]];
  /** Keys whose control or preparation lives on the Application tab. */
  const APPLICATION_KEYS = ["t", "k", "s", "n", "d", "R", "e", "a", "F", "w", "q"];
  /** An application (Track) opens on its own tab; a job opens on whether it fits. */
  const openingTab = (): Tab => (focus === "application" || APPLICATION_KEYS.includes(initialCommand) ? "application" : "fit");
  let tab = $state<Tab>(openingTab());
  let fit = $state<JobFit | null>(null);
  let fitError = $state("");
  const pane = useWorkspaceContext();
  const fieldId = (name: string) => `${pane.id}-job-${id}-${name}`;
  let job = $state<JobDetail | null>(null);
  useFocus(() => job ? { kind: focus, id: job.id, title: `${job.title} at ${job.company}`,
    detail: [job.location || (job.is_remote ? "Remote" : ""), job.tier ? `rank ${job.tier}` : "",
      job.application?.status || job.status || job.queue_state].filter(Boolean).join(" · ") } : null);
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
  async function loadFit() {
    try {
      fit = (await api<{ jobs: JobFit[] }>(`/jobs/fit?ids=${id}`)).jobs[0] ?? null;
      if (!fit) fitError = "This job no longer exists.";
    } catch (cause) {
      fitError = (cause as Error).message;
    }
  }
  function tailor() {
    if (!job || !onview) return;
    tailorForJob({ id: job.id, company: job.company, title: job.title });
    onview("workshop");
  }
  /** Arrow keys, Home and End move between the tabs (the ARIA tabs pattern). */
  async function tabKey(event: KeyboardEvent) {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    const at = TABS.findIndex(([key]) => key === tab);
    const next = event.key === "Home" ? 0 : event.key === "End" ? TABS.length - 1
      : (at + (event.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
    tab = TABS[next][0];
    await tick();
    document.getElementById(fieldId(`tab-${tab}`))?.focus();
  }

  export async function handleKey(event: KeyboardEvent): Promise<boolean> {
    if (!pane.active || event.ctrlKey || event.metaKey || event.altKey || busy) return false;
    if (["F", "w", "q"].includes(event.key) && job && !job.is_active) {
      event.preventDefault();
      message = "Posting unavailable. Recheck availability before preparing it.";
      return true;
    }
    if (["F", "w", "q"].includes(event.key)) {
      // The preparation panel (and the plan it opens) lives on the Application tab.
      tab = "application";
      await tick();
      return automation?.handleKey(event) ?? false;
    }
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
      // Every control a key reaches sits on the Application tab.
      tab = "application";
      await tick();
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
    void loadFit();
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
      const done: Record<string, string> = {
        applied: "Submission recorded. No application was sent.",
        queue: "Added to your preparation queue.",
        skip: "Skipped this job.",
        restore: "Back to not reviewed.",
      };
      message = done[action] ?? "Saved to your workspace.";
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
    {@const queued = job.queue_state === "queued"}
    {@const skipped = job.queue_state === "skipped"}
    <!-- Q8 B: what the job is and the decisions on it first, then Fit / Posting / Application. -->
    <header class="hero">
      <div class="who">
        <span class="company-mark">{job.company.slice(0, 2).toUpperCase()}</span><span>{job.company}</span>
        {#if fit?.total}<span class="fit-chip">{fitLabel(fit)}</span>{/if}
      </div>
      <h2 bind:this={heading} tabindex="-1">{job.title}</h2>
      <p class="meta-line">
        <span><Icon name="location" size={14} />{job.location || "Location not provided"}{job.is_remote ? " · Remote" : ""}</span>
        {#if fit?.pay}<span class="pay">{fit.pay.text}</span>{/if}
        <span><Icon name="calendar" size={14} />{job.deadline_at ? `Due ${dateLabel(job.deadline_at)}` : "No deadline"}</span>
      </p>
      <div class="tag-row">
        <span class="badge">{label(job.status || job.queue_state)}</span>
        {#if !job.is_active}<span class="badge">Posting unavailable</span>{/if}
      </div>
      <div class="decide" role="group" aria-label="Decide on this job">
        {#if !job.application}
          <button class="button primary" disabled={busy} aria-pressed={queued} title={queued ? "Take it out of your preparation queue" : "Add it to your preparation queue"}
            onclick={() => mutate(queued ? "restore" : "queue")}><Icon name="queue" size={15} />{queued ? "Queued" : "Queue"}</button>
          <button class="button" disabled={busy} aria-pressed={skipped} title={skipped ? "Bring it back as not reviewed" : "Skip this job"}
            onclick={() => mutate(skipped ? "restore" : "skip")}>{skipped ? "Skipped" : "Skip"}</button>
        {/if}
        <button class="button" disabled={busy} aria-pressed={job.favorite} onclick={() => mutate("favorite", !job?.favorite)}
          ><Icon name="favorites" size={15} />{job.favorite ? "Saved" : "Save"}</button>
        {#if onview}<button class="button" title="Open the Resume Workshop with this job linked" onclick={tailor}
            ><Icon name="resumes" size={15} />Tailor resume</button>{/if}
      </div>
      {#if message}<p class="success-message" role="status">{message}</p>{/if}
    </header>
    <div class="tabs" role="tablist" aria-label="Job details" tabindex="-1" onkeydown={tabKey}>
      {#each TABS as [key, name] (key)}
        <button
          type="button"
          role="tab"
          id={fieldId(`tab-${key}`)}
          aria-selected={tab === key}
          aria-controls={fieldId(`panel-${key}`)}
          tabindex={tab === key ? 0 : -1}
          onclick={() => (tab = key)}>{name}</button>
      {/each}
    </div>
    <!-- Every panel stays mounted (hidden when not chosen), so a key's control and the
         preparation in progress are there whichever tab is showing. -->
    <div class="tab-body" role="tabpanel" id={fieldId("panel-fit")} aria-labelledby={fieldId("tab-fit")} hidden={tab !== "fit"}>
      {#if fitError}<p class="muted">{fitError}</p>
      {:else if !fit}<Loader label="Comparing with your resume…" />
      {:else if fit.error}<p class="muted">{fit.error}</p>
      {:else if !fit.total}<p class="muted">This posting names none of the skills rika! compares (config/resume_terms.yaml).</p>
      {:else}
        <p class="tally"><strong>{fit.covered} of {fit.total}</strong> skills this posting asks for are covered</p>
        <ul class="checklist" aria-label="Posting skills">
          {#each fit.skills as skill (skill.term)}
            <li class:on={skill.covered}>
              <span class="tick" aria-hidden="true">{#if skill.covered}<Icon name="check" size={13} />{/if}</span>
              <span class="name">{skill.term}</span>
              {#if skill.covered === "resume"}<span class="src">On your resume</span>
              {:else if skill.covered === "fact"}<span class="src fact"><b class="diamond" aria-hidden="true"></b>Confirmed fact</span>
              {:else}<span class="src gap">Not covered</span>{/if}
            </li>
          {/each}
        </ul>
        <p class="legend">
          {fit.document
            ? `Compared with “${fit.document.title}” (its ${fit.compared_with}).`
            : "No Resume Workshop resume yet, so only your confirmed facts count."}
          {#if fit.skills.some((skill) => skill.covered === "fact")} A confirmed fact is something you told rika! that this resume does not say yet.{/if}
        </p>
      {/if}
      <h3>Requirements today</h3>
      <p><span class="badge">{label(job.eligibility)}</span></p>
      <p>
        {job.eligibility_reason ||
          job.tier_reason ||
          "No fit assessment recorded. Review the posting requirements before applying."}
      </p>
      {#if job.tier_reason && job.eligibility_reason}<p class="muted">{job.tier ? `Rank ${job.tier}: ` : ""}{job.tier_reason}</p>{/if}
    </div>
    <div class="tab-body" role="tabpanel" id={fieldId("panel-posting")} aria-labelledby={fieldId("tab-posting")} hidden={tab !== "posting"}>
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
      <h3>Description</h3>
      <p class="description">
        {job.description ||
          "No description saved. Open the original posting for details."}
      </p>
      <div class="detail-actions">
        {#if safeUrl(job.url) && job.is_active}<a
            class="button"
            href={safeUrl(job.url)}
            target="_blank"
            rel="noopener noreferrer"
            >Read original posting <Icon name="external" size={16} /></a
          >{:else if !job.is_active}<span class="badge">Posting unavailable</span>{/if}
        <PostingAvailability jobIds={[id]} {demo} oncomplete={() => changed()} />
      </div>
      <p class="muted" title={job.verification_reason || undefined}>
        {job.verification_checked_at ? `Last checked ${dateLabel(job.verification_checked_at)}` : "Availability not yet checked"}
      </p>
    </div>
    <div class="tab-body" role="tabpanel" id={fieldId("panel-application")} aria-labelledby={fieldId("tab-application")} hidden={tab !== "application"}>
      <section class="detail-section">
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
      </section>
      <Automation
        bind:this={automation}
        jobIds={[id]}
        compact
        {demo}
        onchanged={changed}
      />
      <section class="detail-section">
        <h3>{job.application ? "Update the application status" : "Record a submission"}</h3>
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
            Apply on the employer’s website, then record it here. rika! never
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
      </section>
      <section class="detail-section">
        <h3>Your notes</h3>
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
      </section>
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
  .hero {
    padding: 20px 24px 16px;
  }
  .who {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--muted);
    font-size: 13px;
  }
  .who .company-mark {
    width: 34px;
    height: 34px;
    font-size: 11px;
  }
  .fit-chip {
    margin-left: auto;
    padding: 3px 9px;
    border: 1px solid var(--border);
    border-radius: 999px;
    color: var(--ink);
    font: 600 11px/1.3 var(--font-ui);
    white-space: nowrap;
  }
  .meta-line {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 16px;
    margin: 0 0 10px;
    color: var(--muted);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }
  .meta-line span {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .meta-line .pay {
    color: var(--ink);
    font-weight: 550;
  }
  .decide {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 16px;
  }
  .tabs {
    display: flex;
    gap: 4px;
    padding: 0 18px;
    border-block: 1px solid var(--border);
  }
  :global(:root body) .tabs [role="tab"] {
    position: relative;
    height: 40px;
    min-height: 0;
    margin: 0;
    padding: 0 12px;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: var(--muted);
    font: 500 13px/1 var(--font-ui);
  }
  :global(:root body) .tabs [role="tab"]:hover {
    color: var(--ink);
  }
  :global(:root body) .tabs [role="tab"][aria-selected="true"] {
    color: var(--ink);
    font-weight: 600;
  }
  .tabs [role="tab"][aria-selected="true"]::after {
    content: "";
    position: absolute;
    left: 10px;
    right: 10px;
    bottom: -1px;
    height: 2px;
    border-radius: 2px;
    background: var(--accent);
  }
  .tabs [role="tab"]:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
  .tab-body {
    padding: 20px 24px 26px;
  }
  .tab-body[hidden] {
    display: none;
  }
  .tab-body > h3 {
    margin: 22px 0 8px;
    font-size: 13px;
  }
  .tab-body > p {
    font-size: 13px;
    line-height: 1.65;
  }
  .tab-body > .detail-section:first-child {
    margin-top: 0;
    padding-top: 0;
    border-top: 0;
  }
  .tally {
    margin: 0 0 10px;
    color: var(--muted);
  }
  .tally strong {
    color: var(--ink);
  }
  .checklist {
    margin: 0;
    padding: 0;
    overflow: hidden;
    border: 1px solid var(--border);
    border-radius: var(--r-item, 8px);
    list-style: none;
  }
  .checklist li {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    color: var(--muted);
    font-size: 13px;
  }
  .checklist li:last-child {
    border-bottom: 0;
  }
  .checklist li.on {
    color: var(--ink);
  }
  .tick {
    flex: none;
    display: inline-grid;
    place-items: center;
    width: 18px;
    height: 18px;
    box-sizing: border-box;
    border: 1.5px dashed var(--subtle);
    border-radius: 50%;
  }
  li.on .tick {
    border: 0;
    background: color-mix(in srgb, var(--success) 22%, var(--surface));
    color: var(--success);
  }
  .name {
    flex: 1;
    min-width: 0;
  }
  .src {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--subtle);
    font-size: 11px;
  }
  .src.fact {
    color: var(--violet, var(--accent));
  }
  .src.gap {
    font-style: italic;
  }
  .diamond {
    display: inline-block;
    width: 7px;
    height: 7px;
    background: currentColor;
    transform: rotate(45deg);
  }
  .legend {
    margin: 10px 0 0;
    color: var(--subtle);
    font-size: 12px !important;
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
  h2 {
    font-size: 23px;
    line-height: 1.25;
    margin: 14px 0 8px;
    letter-spacing: -0.025em;
  }
  .detail-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 20px;
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
  .description {
    margin-top: 0;
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
