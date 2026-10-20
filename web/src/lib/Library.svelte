<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api, type TaskRecord } from "./api";
  import TaskStatus from "./TaskStatus.svelte";
  import ResumeReview, { type ResumeAudit } from "./ResumeReview.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  const pane = useWorkspaceContext();
  let {
    demo = false,
    onopenjob,
  }: { demo?: boolean; onopenjob?: (id: number) => void } = $props();
  type Registered = {
    id: number;
    label: string;
    description: string | null;
    available: boolean;
    default_tracks: string[];
  };
  type Version = {
    id: string;
    track: string;
    status: string;
    created_at: string;
  };
  type Term = {
    term: string;
    postings: number;
    share: number;
    resume_line: string | null;
    example_snippet: string | null;
    example_job_id: number | null;
    example_company: string | null;
    example_title: string | null;
  };
  type Report = {
    track: string;
    resume_label: string | null;
    postings: number;
    error: string | null;
    missing: Term[];
    covered: Term[];
  };
  let resumes = $state<Registered[]>([]),
    versions = $state<Version[]>([]),
    tracks = $state<string[]>([]);
  let defaults = $state<Record<string, string | null>>({});
  let track = $state(""),
    selected = $state<ResumeAudit | null>(null),
    reports = $state<Report[]>([]);
  let loading = $state(false),
    busy = $state(false),
    error = $state(""),
    notice = $state("");
  let label = $state(""),
    description = $state(""),
    localPath = $state(""),
    source = $state("");
  let files = $state<FileList | undefined>(),
    mode = $state("upload"),
    minimum = $state(5);
  let sourceOpen = $state(false);
  let coachOpen = $state(false);
  let versionsOpen = $state(false);
  let task = $state<TaskRecord | null>(null);
  type Decision = "register" | "generate" | "promote" | "reject" | "rollback";
  let pending = $state<Decision | null>(null),
    dialog = $state<HTMLDialogElement>(),
    cancel = $state<HTMLButtonElement>();
  let versionSelect = $state<HTMLSelectElement>(),
    reviewId = $state("");
  let pendingVersionId = $state(""),
    pendingTrack = $state("");
  const fail = (cause: unknown) => {
    error =
      cause instanceof Error ? cause.message : "Local resume operation failed";
  };
  async function load() {
    loading = true;
    try {
      const data = await api<{
        resumes: Registered[];
        versions: Version[];
        tracks: string[];
        defaults: Record<string, string | null>;
      }>("/resumes/workflow");
      resumes = data.resumes;
      versions = data.versions;
      tracks = data.tracks;
      defaults = data.defaults;
      if (!tracks.includes(track)) track = tracks[0] || "";
    } catch (cause) {
      fail(cause);
    } finally {
      loading = false;
    }
  }
  async function coach() {
    error = "";
    busy = true;
    try {
      reports = (
        await api<{ reports: Report[] }>(
          "/resumes/coach" +
            (track ? "?track=" + encodeURIComponent(track) : ""),
        )
      ).reports;
    } catch (cause) {
      fail(cause);
    } finally {
      busy = false;
    }
  }
  async function review(id: string) {
    versionsOpen = true;
    if (!id) return;
    error = "";
    busy = true;
    try {
      selected = (
        await api<{ version: ResumeAudit }>(
          "/resumes/versions/" + encodeURIComponent(id),
        )
      ).version;
      reviewId = id;
    } catch (cause) {
      selected = null;
      fail(cause);
    } finally {
      busy = false;
    }
  }
  async function ask(action: Decision) {
    if (!pane.active || busy) return;
    error = "";
    if (action === "generate" || action === "rollback") coachOpen = true;
    if (action === "promote" || action === "reject") versionsOpen = true;
    pendingVersionId = selected?.id || "";
    pendingTrack = track;
    pending = action;
    await tick();
    if (!pane.active || pending !== action) return;
    dialog?.showModal();
    cancel?.focus({ preventScroll: true });
    if (dialog) dialog.scrollTop = 0;
  }
  function dismiss() {
    dialog?.close();
    pending = null;
  }
  async function encode(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("Unable to read selected PDF"));
      reader.readAsDataURL(file);
    });
  }
  async function confirm() {
    if (!pane.active) return;
    const action = pending,
      versionId = pendingVersionId,
      approvedTrack = pendingTrack;
    dismiss();
    if (!action) return;
    busy = true;
    error = "";
    notice = "";
    try {
      if (action === "register") {
        const body: Record<string, unknown> = {
          label,
          description,
          confirm: true,
        };
        if (mode === "path") body.path = localPath;
        else {
          const file = files?.[0];
          if (!file) throw new Error("Choose a PDF first");
          if (file.size > 8 * 1024 * 1024)
            throw new Error("PDF upload must be at most 8 MiB");
          body.upload = await encode(file);
        }
        await api("/resumes/register", body);
        notice =
          "Resume saved in My resumes. Next, find or save a job and choose this PDF in Prepare applications. Existing resume defaults are unchanged.";
      } else if (action === "generate") {
        task = (
          await api<{ task: TaskRecord }>("/resumes/generate", {
            track: approvedTrack,
            source,
            min_postings: minimum,
            confirm: true,
          })
        ).task;
        if (task.status !== "running") await complete(task);
      } else if (action === "rollback") {
        await api("/resumes/rollback", { track: approvedTrack, confirm: true });
        notice =
          "Previous track default restored. Existing job selections are unchanged.";
      } else if (versionId) {
        selected = (
          await api<{ version: ResumeAudit }>(
            "/resumes/versions/" + versionId + "/" + action,
            { confirm: true },
          )
        ).version;
        reviewId = versionId;
        notice =
          action === "promote"
            ? "Candidate promoted for future unchosen jobs."
            : "Candidate rejected; audit artifacts retained.";
      }
      await load();
    } catch (cause) {
      fail(cause);
    } finally {
      busy = false;
    }
  }
  async function complete(result: TaskRecord) {
    task = result;
    await load();
    if (result.status === "completed") {
      const value = result.result as { version_id?: string } | null;
      if (value?.version_id) await review(value.version_id);
    }
  }
  async function keydown(event: KeyboardEvent) {
    const target = event.target as HTMLElement | null;
    if (
      !pane.active ||
      event.defaultPrevented ||
      event.ctrlKey ||
      event.metaKey ||
      event.altKey ||
      dialog?.open ||
      target?.closest(
        'input,textarea,select,button,a,summary,[contenteditable="true"],[role="dialog"]',
      )
    )
      return;
    if (event.key === "t" || event.key === "T") {
      event.preventDefault();
      coachOpen = true;
      if (tracks.length)
        track =
          tracks[
            (tracks.indexOf(track) +
              (event.key === "T" ? -1 : 1) +
              tracks.length) %
              tracks.length
          ];
    } else if (event.key === "v") {
      event.preventDefault();
      versionsOpen = true;
      await tick();
      if (pane.active) versionSelect?.focus();
    } else if (event.key === "g" && track) {
      event.preventDefault();
      void ask("generate");
    } else if (event.key === "b" && track) {
      event.preventDefault();
      void ask("rollback");
    } else if (
      event.key === "p" &&
      selected?.status === "candidate" &&
      selected.validation.valid &&
      selected.patches.length
    ) {
      event.preventDefault();
      void ask("promote");
    } else if (event.key === "x" && selected?.status === "candidate") {
      event.preventDefault();
      void ask("reject");
    } else if (event.key === "o" || event.key === "Enter") {
      const url = selected
        ? "/api/resumes/versions/" + selected.id + "/pdf"
        : resumes.find((item) => item.available)
          ? "/api/resumes/" +
            resumes.find((item) => item.available)!.id +
            "/pdf"
          : null;
      if (url) {
        event.preventDefault();
        window.open(url, "_blank", "noopener,noreferrer");
      }
    }
  }
  $effect(() => {
    if (!pane.active) dismiss();
  });
  onMount(() => {
    void load();
  });
</script>

<svelte:window onkeydown={keydown} />
<section
  class="panel library"
  aria-labelledby={`${pane.id}-library-title`}
  aria-busy={loading}
>
  <header class="panel-heading">
    <div>
      <p class="eyebrow">Setup / Step 2</p>
      <h2 id={`${pane.id}-library-title`}>My resumes</h2>
      <p class="muted">
        Upload a PDF you have checked, then choose it for a job in Prepare
        applications. Your resume stays local; uploading never submits an
        application.
      </p>
    </div>
    <button class="button" disabled={loading || busy} onclick={load}
      >Refresh</button
    >
  </header>
  {#if demo}<p class="muted inset">
      Synthetic workspace. Registration and resume decisions affect only this
      local demo.
    </p>{/if}
  {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
  {#if notice}<p class="inset" role="status">{notice}</p>{/if}
  <section class="inset">
    <h3>Upload resume PDF</h3>
    <p class="muted">
      Start with your current resume. Give it a label you will recognize when
      choosing a resume for a job.
    </p>
    <form
      onsubmit={(event) => {
        event.preventDefault();
        void ask("register");
      }}
    >
      <div class="grid">
        <label
          >Resume label (your own name for this PDF)<input
            bind:value={label}
            required
            maxlength="200"
            placeholder="My general resume"
          /></label
        ><label
          >Description (optional)<input
            bind:value={description}
            maxlength="4096"
          /></label
        >
      </div>
      <details bind:open={sourceOpen}>
        <summary>Advanced: use a PDF already on this computer</summary>
        <p class="muted">
          Usually, choose a PDF below. If the website runs on another computer,
          a local path must refer to that computer.
        </p>
        <label
          >PDF source<select bind:value={mode}>
            <option value="upload">Upload from this device</option>
            <option value="path">Register a local file path instead</option>
          </select></label
        >
      </details>
      {#if mode === "path" && !sourceOpen}<p class="muted">
          Using a local file path. Open the advanced source option to switch
          back to upload.
        </p>{/if}
      {#if mode === "upload"}<label
          >Choose resume PDF (maximum 8 MiB)<input
            type="file"
            accept=".pdf,application/pdf"
            bind:files
            required
          /></label
        >{:else}<label
          >Full path to your PDF<input
            bind:value={localPath}
            required
            placeholder="/path/to/resume.pdf"
          /></label
        >{/if}
      <p class="muted">
        A copy is saved in your local resume library. To replace a resume that
        has already been chosen or reviewed, use a new label unless the PDF is
        identical. Uploading does not change job-category defaults.
      </p>
      <button class="button primary" disabled={busy}
        >{mode === "upload"
          ? "Review & upload PDF"
          : "Review & register local PDF"}</button
      >
    </form>
  </section>
  <section class="inset">
    <h3>Your saved resumes ({resumes.length})</h3>
    {#if !resumes.length}<p class="empty-state">
        No resumes yet. Upload your first PDF above.
      </p>{/if}
    <ul class="cards">
      {#each resumes as resume}<li>
          <h4>{resume.label}</h4>
          {#if resume.description}<p>{resume.description}</p>{/if}
          <p class="muted">
            {resume.default_tracks.length
              ? "Preferred for: " + resume.default_tracks.join(", ")
              : "Ready to choose for individual jobs"}
          </p>
          {#if resume.available}<a
              class="button"
              href="/api/resumes/{resume.id}/pdf"
              target="_blank"
              rel="noopener noreferrer">Open resume PDF</a
            >{:else}<span class="badge">PDF missing or identity changed</span
            >{/if}
        </li>{/each}
    </ul>
  </section>
  <details class="inset" bind:open={coachOpen}>
    <summary>Advanced: improve a resume for a job category</summary>
    <h3>Compare wording & create a version to review</h3>
    <p class="muted">
      Job categories (called “tracks” in configuration) group similar jobs.
      Compare your resume with those job descriptions, then optionally create a
      proposed version. It does not replace your current resume without your
      approval.
    </p>
    <div class="grid">
      <label
        >Job category (t / T)<select bind:value={track}
          ><option value="" disabled>Select a job category</option
          >{#each tracks as name}<option value={name}>{name}</option
            >{/each}</select
        ></label
      >
      <p class="muted">Current default: {defaults[track] || "None"}</p>
    </div>
    <button class="button" disabled={busy || !track} onclick={coach}
      >Compare resume wording with jobs</button
    >
    {#each reports as report}
      <article>
        <h4>
          {report.track} · {report.postings} postings · {report.resume_label ||
            "No default"}
        </h4>
        {#if report.error}<p class="error-banner">{report.error}</p>{/if}
        {#each ["missing", "covered"] as group}
          <h4>
            {group === "missing"
              ? "Demand not found in resume — not proof of experience"
              : "Covered vocabulary"}
          </h4>
          <ul>
            {#each group === "missing" ? report.missing : report.covered as term}
              <li>
                <strong>{term.term}</strong> · {term.postings} postings ({Math.round(
                  term.share * 100,
                )}%)
                {#if term.resume_line}<blockquote>
                    Resume: {term.resume_line}
                  </blockquote>{/if}
                {#if term.example_snippet}
                  <blockquote>
                    Posting #{term.example_job_id}: {term.example_snippet}
                    <footer>
                      {term.example_company} · {term.example_title}
                    </footer>
                    {#if term.example_job_id}
                      <a
                        href="#jobs"
                        onclick={(event) => {
                          if (onopenjob && term.example_job_id) {
                            event.preventDefault();
                            onopenjob(term.example_job_id);
                          }
                        }}>Inspect example posting #{term.example_job_id}</a
                      >
                    {/if}
                  </blockquote>
                {/if}
              </li>
            {/each}
          </ul>
        {/each}
      </article>
    {/each}
    <form
      onsubmit={(event) => {
        event.preventDefault();
        void ask("generate");
      }}
    >
      <label
        >Editable resume source: local HTML path (optional)<input
          bind:value={source}
          placeholder="Leave empty to infer the current default's source"
        /></label
      ><label
        >Minimum job descriptions to compare<input
          type="number"
          min="1"
          max="1000"
          step="1"
          bind:value={minimum}
          required
        /></label
      >
      <p class="muted">
        Creating a version needs an editable HTML source, not just a PDF. Uses
        local rules to propose exact wording changes with supporting facts and
        job descriptions. Original files and current defaults stay unchanged;
        missing facts are never inserted.
      </p>
      <div class="actions">
        <button
          class="button primary"
          disabled={busy || !track || task?.status === "running"}
          >Create proposed resume version (g)</button
        ><button
          type="button"
          class="button"
          disabled={busy || !track}
          onclick={() => ask("rollback")}
          >Restore previous category default (b)</button
        >
      </div>
    </form>
    <TaskStatus {task} oncomplete={complete} />
  </details>
  <details class="inset" bind:open={versionsOpen}>
    <summary>Advanced: review versions & approve changes</summary>
    <h3>Version history and review</h3>
    <p class="muted">
      Review the PDF, changes and supporting facts before approving a version.
      Approval affects only future jobs without a chosen resume; existing
      choices and history stay fixed.
    </p>
    <label
      >Select a version (v)<select
        bind:this={versionSelect}
        bind:value={reviewId}
        onchange={() => review(reviewId)}
        ><option value="">Choose a version for full audited review</option
        >{#each versions as version}<option value={version.id}
            >{version.track} · {version.status} · {new Date(
              version.created_at,
            ).toLocaleString()} · {version.id}</option
          >{/each}</select
      ></label
    >{#if !versions.length}<p class="empty-state">
        No proposed versions yet. Open “improve a resume” above to create one.
      </p>{/if}
    {#if selected}<ResumeReview
        version={selected}
        {busy}
        ondecision={ask}
      />{/if}
  </details>
  <details class="inset muted">
    <summary>Keyboard shortcuts</summary>
    <p>
      t/T job category · g create version · v versions · o/Enter open PDF · p
      approve · x reject · b restore previous default. Decisions always open a
      confirmation with Cancel selected first. No application is submitted.
    </p>
  </details>
</section>
<dialog
  bind:this={dialog}
  oncancel={dismiss}
  onclose={() => {
    pending = null;
  }}
  aria-labelledby={`${pane.id}-resume-confirm-title`}
>
  <h3 id={`${pane.id}-resume-confirm-title`}>
    {pending === "register"
      ? "Save this resume PDF?"
      : pending === "generate"
        ? "Create a proposed version?"
        : pending === "promote"
          ? "Approve this version?"
          : pending === "rollback"
            ? "Restore the previous default?"
            : "Reject this version?"}
  </h3>
  {#if pending === "promote" || pending === "reject"}<p class="decision-id">
      Version: {pendingVersionId}
    </p>{/if}
  <p>
    {pending === "promote"
      ? "I reviewed the exact diff, fact anchors, warnings and PDF. Make this candidate the default only for future unchosen jobs?"
      : pending === "reject"
        ? "Reject this pending candidate? Its immutable artifacts will be retained."
        : pending === "rollback"
          ? "Restore the prior promoted default for " +
            pendingTrack +
            "? Existing selections and historical applications remain fixed."
          : pending === "register"
            ? "Copy your chosen PDF into My resumes? This does not change any job's chosen resume or submit an application."
            : "Generate an audited candidate for " +
              pendingTrack +
              " using the selected local source? This does not promote it."}
  </p>
  <div class="actions">
    <button bind:this={cancel} class="button" onclick={dismiss}>Cancel</button
    ><button class="button primary" onclick={confirm}
      >{pending === "register"
        ? "Save resume PDF"
        : pending === "generate"
          ? "Create version"
          : pending === "promote"
            ? "Approve version"
            : pending === "rollback"
              ? "Restore default"
              : "Reject version"}</button
    >
  </div>
</dialog>

<style>
  .decision-id {
    overflow-wrap: anywhere;
    font: 12px monospace;
    color: var(--muted);
  }
  .library {
    min-width: 0;
  }
  .panel-heading,
  .actions {
    display: flex;
    gap: 0.8rem;
    justify-content: space-between;
    flex-wrap: wrap;
  }
  .actions {
    justify-content: flex-start;
  }
  .inset {
    padding: 1.2rem;
    border-top: 1px solid var(--border);
  }
  h2,
  h3,
  h4 {
    margin: 0.4rem 0;
  }
  h3 {
    font-size: 1.1rem;
  }
  p {
    line-height: 1.5;
  }
  summary {
    cursor: pointer;
    color: var(--ink);
    font-weight: 600;
    line-height: 1.5;
  }
  details[open] > summary {
    margin-bottom: 1rem;
  }
  form {
    display: grid;
    gap: 0.8rem;
    margin-top: 1rem;
  }
  label {
    display: grid;
    gap: 0.4rem;
    margin: 0.5rem 0;
  }
  input,
  select {
    width: 100%;
    min-width: 0;
    padding: 0.65rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--ink);
    font: inherit;
  }
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
    align-items: center;
  }
  .cards {
    list-style: none;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr));
    gap: 1rem;
  }
  .cards li,
  article {
    padding: 1rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow-wrap: anywhere;
  }
  blockquote {
    margin: 0.5rem 0;
    padding: 0.5rem;
    border-left: 2px solid var(--accent);
  }
  footer {
    font-size: 0.85rem;
    color: var(--muted);
  }
  dialog {
    max-width: min(36rem, calc(100% - 2rem));
    background: var(--surface-raised);
    color: var(--ink);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.5rem;
  }
  dialog::backdrop {
    background: #0009;
  }
  @media (max-width: 700px) {
    .grid {
      grid-template-columns: 1fr;
    }
  }
</style>
