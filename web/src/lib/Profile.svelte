<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api, type TaskRecord } from "./api";
  import TaskStatus from "./TaskStatus.svelte";

  let { demo = false, onresume }: { demo?: boolean; onresume?: () => void } =
    $props();
  type Document = {
    section: string;
    version: string;
    text: string;
    notice: string;
  };
  type Basics = { version: string; fields: Record<string, string> };
  const basicFields = [
    { key: "name", label: "Full name", type: "text" },
    { key: "email", label: "Email", type: "email" },
    { key: "phone", label: "Phone", type: "tel" },
    { key: "school", label: "School", type: "text" },
    { key: "major", label: "Major", type: "text" },
    { key: "degree", label: "Degree", type: "text" },
    { key: "standing", label: "Year in school", type: "text" },
    { key: "graduation", label: "Graduation date (YYYY-MM)", type: "text" },
    { key: "home_city", label: "City", type: "text" },
    { key: "home_state", label: "State or region", type: "text" },
    { key: "country", label: "Country", type: "text" },
  ];
  let basics = $state<Basics | null>(null);
  let basicsDraft = $state<Record<string, string>>({});
  let configOpen = $state(false);
  let answersOpen = $state(false);
  let basicsDirty = $derived(
    basics !== null &&
      basicFields.some(({ key }) => basicsDraft[key] !== basics?.fields[key]),
  );
  type ProfileData = {
    sections: string[];
    profile: Record<string, unknown>;
    tracks: {
      name: string;
      weekly_target: number;
      default_resume: string | null;
    }[];
    questions: { id: number; text: string; times_seen: number }[];
    answers_count: number;
    notifier: string | null;
    triage_cap: number;
  };
  type ImportRow = {
    line: number;
    company: string;
    title: string;
    status: string;
    applied_at: string | null;
    track: string | null;
    resume: string | null;
    location: string | null;
    url: string;
    notes: string | null;
    action: string;
    problem: string | null;
  };
  type Preview = {
    version: string;
    rows: ImportRow[];
    mapping: Record<string, string>;
    unmapped_columns: string[];
    counts: Record<string, number>;
  };
  type Check = {
    name: string;
    ok: boolean;
    warning: boolean;
    detail: string;
    fix: string | null;
  };

  let data = $state<ProfileData | null>(null);
  let document = $state<Document | null>(null);
  let section = $state("profile");
  let editor = $state("");
  let yamlDirty = $derived(document !== null && editor !== document.text);
  let busy = $state(false);
  let error = $state("");
  let notice = $state("");
  let question = $state("");
  let answer = $state("");
  let answerVersion = $state("");
  let csvText = $state("");
  let mappingText = $state("{}");
  let preview = $state<Preview | null>(null);
  let previewPayload: { csv: string; mapping: Record<string, string> } | null =
    null;
  let checks = $state<Check[] | null>(null);
  let scheduleFormat = $state("systemd");
  let scheduleTimes = $state("07:00, 17:00");
  let withTriage = $state(false);
  let scheduleOutput = $state("");
  let notifyTask = $state<TaskRecord | null>(null);
  let confirmDialog: HTMLDialogElement;
  let cancelButton: HTMLButtonElement;
  let editorField = $state<HTMLTextAreaElement>();
  let answerField = $state<HTMLTextAreaElement>();
  let confirmation = $state("");
  let pending: (() => Promise<void>) | null = null;
  let loadSequence = 0;

  async function run(work: () => Promise<void>) {
    if (busy) return;
    busy = true;
    error = "";
    notice = "";
    try {
      await work();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : "The operation failed.";
    } finally {
      busy = false;
    }
  }

  async function refreshData() {
    const [profile, answers] = await Promise.all([
      api<ProfileData>("/profile"),
      api<Document>("/profile/config?section=answers"),
    ]);
    data = profile;
    answerVersion = answers.version;
  }

  async function loadBasics() {
    const loaded = await api<Basics>("/profile/basics");
    basics = loaded;
    basicsDraft = { ...loaded.fields };
  }

  function saveBasics() {
    if (!basics || (section === "profile" && yamlDirty)) return;
    const payload = {
      fields: { ...basicsDraft },
      version: basics.version,
      confirm: true,
    };
    ask(
      "Save these details for future application forms? Confirm only facts you have verified. Blank fields stay blank; eligibility and other settings will not change.",
      async () => {
        basics = await api<Basics>("/profile/basics", payload);
        basicsDraft = { ...basics.fields };
        if (section === "profile" && !yamlDirty) await loadDocument("profile");
        await refreshData();
        notice =
          "Your details are saved. Next, upload a resume PDF in My resumes.";
      },
    );
  }

  export function hasUnsavedChanges(): boolean {
    return basicsDirty || yamlDirty || Boolean(question || answer);
  }

  function openResumes() {
    onresume?.();
  }

  function beforeUnload(event: BeforeUnloadEvent) {
    if (hasUnsavedChanges()) {
      event.preventDefault();
      event.returnValue = "";
    }
  }

  async function loadDocument(name: string) {
    const sequence = ++loadSequence;
    const loaded = await api<Document>(
      `/profile/config?section=${encodeURIComponent(name)}`,
    );
    if (sequence !== loadSequence) return;
    section = name;
    document = loaded;
    editor = loaded.text;
  }

  function ask(message: string, action: () => Promise<void>) {
    confirmation = message;
    pending = action;
    confirmDialog.showModal();
    cancelButton?.focus({ preventScroll: true });
    confirmDialog.scrollTop = 0;
  }

  function cancel() {
    pending = null;
    confirmDialog.close();
  }
  function confirm() {
    const action = pending;
    cancel();
    if (action) void run(action);
  }

  function refresh() {
    const action = async () => {
      await refreshData();
      await loadDocument(section);
      await loadBasics();
    };
    if (basicsDirty || yamlDirty)
      ask(
        "Discard unsaved profile details and configuration edits, then reload? Your unsaved answer text will be kept.",
        action,
      );
    else void run(action);
  }

  function selectSection(event: Event) {
    const selected = (event.currentTarget as HTMLSelectElement).value;
    (event.currentTarget as HTMLSelectElement).value = section;
    if (selected === section) return;
    const action = () => loadDocument(selected);
    if (document && editor !== document.text)
      ask(
        "Discard unsaved edits and open the selected configuration section?",
        action,
      );
    else void run(action);
  }

  function saveConfig() {
    if (!document || (section === "profile" && basicsDirty)) return;
    const payload = {
      section,
      text: editor,
      version: document.version,
      confirm: true,
    };
    ask(
      "Save this configuration? Only use facts and answers you have personally verified. This affects future discovery and form filling; it never submits an application.",
      async () => {
        document = await api<Document>("/profile/config", payload);
        editor = document.text;
        await refreshData();
        if (section === "profile" && !basicsDirty) await loadBasics();
        notice =
          "Configuration saved and reloaded. Existing chosen resumes and application history are unchanged.";
      },
    );
  }

  function saveAnswer() {
    const payload = { question, answer, version: answerVersion, confirm: true };
    ask(
      "Save exactly your written answer for use on matching application questions? No answer is generated or inferred. Review company/track scope in the answers editor when needed.",
      async () => {
        const saved = await api<Document>("/profile/answer", payload);
        answerVersion = saved.version;
        answer = "";
        question = "";
        if (section === "answers" && document && editor === document.text) {
          document = saved;
          editor = saved.text;
        }
        await refreshData();
        notice =
          "Your answer was saved. Unknown questions still remain unanswered.";
      },
    );
  }

  async function readCsv(event: Event) {
    const file = (event.currentTarget as HTMLInputElement).files?.[0];
    if (!file) return;
    await run(async () => {
      if (file.size > 45000)
        throw new Error(
          "Choose a UTF-8 CSV no larger than 45 KB (at most 1000 rows). Split larger spreadsheets before importing.",
        );
      csvText = await file.text();
      preview = null;
      previewPayload = null;
    });
  }

  function previewImport() {
    void run(async () => {
      const mapping = JSON.parse(mappingText);
      const payload = { csv: csvText, mapping };
      preview = await api<Preview>("/profile/import/preview", payload);
      previewPayload = payload;
    });
  }

  function confirmImport() {
    if (!preview || !previewPayload) return;
    const payload = {
      ...previewPayload,
      version: preview.version,
      confirm: true,
    };
    ask(
      "Import these historical applications exactly as previewed? This records confirmed historical application/status events; it does not apply to jobs. Invalid rows and already-present applications are skipped.",
      async () => {
        const result = await api<{
          stats: Record<string, number>;
          problems_skipped: number;
        }>("/profile/import/confirm", payload);
        notice = `Imported ${result.stats.applications_created} applications, created ${result.stats.jobs_created} jobs and ${result.stats.events} history events; ${result.stats.skipped} rows skipped (${result.problems_skipped} invalid).`;
        preview = null;
        previewPayload = null;
      },
    );
  }

  function printSchedule() {
    const payload = {
      format: scheduleFormat,
      times: scheduleTimes.split(",").map((t) => t.trim()),
      with_triage: withTriage,
      confirm_paid: withTriage,
    };
    const action = async () => {
      const result = await api<{ text: string }>("/profile/schedule", payload);
      scheduleOutput = result.text;
      notice = "Schedule generated only. Nothing has been installed or run.";
    };
    if (withTriage)
      ask(
        `This output enables unattended paid triage when you install it, currently capped at $${data?.triage_cap ?? 0} per run, not per day. Repeated runs can incur repeated charges. Generate this paid schedule?`,
        action,
      );
    else void run(action);
  }

  function sendNotification() {
    ask(
      "Send today's job-search summary to your locally configured notification provider? Only the digest summary is sent, not your resumes or screening answers.",
      async () => {
        const result = await api<{ task: TaskRecord }>("/profile/notify", {
          confirm: true,
        });
        notifyTask = result.task;
      },
    );
  }

  async function handleKey(event: KeyboardEvent) {
    const target = event.target as HTMLElement | null;
    if (
      event.defaultPrevented ||
      event.ctrlKey ||
      event.metaKey ||
      event.altKey ||
      event.shiftKey ||
      event.repeat ||
      busy ||
      confirmDialog?.open ||
      target?.closest(
        "input,textarea,select,button,a,summary,[contenteditable=true],[role=dialog]",
      )
    )
      return;
    if (event.key === "r") {
      event.preventDefault();
      refresh();
    }
    if (event.key === "e") {
      event.preventDefault();
      configOpen = true;
      await tick();
      editorField?.focus();
    }
    if (event.key === "w") {
      event.preventDefault();
      answersOpen = true;
      await tick();
      answerField?.focus();
    }
    if (event.key === "n" && onresume) {
      event.preventDefault();
      openResumes();
    }
  }

  onMount(() => {
    void run(async () => {
      await refreshData();
      await loadDocument(section);
      await loadBasics();
    });
  });
</script>

<svelte:window onkeydown={handleKey} onbeforeunload={beforeUnload} />

<section
  class="panel profile-page"
  aria-labelledby="profile-title"
  aria-busy={busy}
>
  <header class="panel-heading">
    <div>
      <p class="eyebrow">Setup / Step 1</p>
      <h2 id="profile-title">My profile</h2>
      <p class="muted">
        Add the details you want copied into application forms. Leave anything
        you do not know blank. You always review and submit applications
        yourself.
      </p>
    </div>
    <button class="button" onclick={refresh} disabled={busy}
      >Refresh <kbd>r</kbd></button
    >
  </header>
  {#if demo}<p class="muted">
      Demo edits stay in the temporary local workspace. External notifications
      are disabled.
    </p>{/if}
  {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
  {#if notice}<p class="success-note" role="status">{notice}</p>{/if}
  {#if busy}<p class="muted" role="status">Working…</p>{/if}

  {#if data}
    <div class="profile-grid setup-layout">
      <section class="subpanel" aria-labelledby="facts-title">
        <h3 id="facts-title">Your basic details</h3>
        <p class="muted">
          Use your own verified information. Nothing here determines your
          eligibility for a job.
        </p>
        <form
          onsubmit={(event) => {
            event.preventDefault();
            saveBasics();
          }}
        >
          <div class="profile-grid">
            {#each basicFields as field}
              <label for={`profile-${field.key}`}
                >{field.label}
                <input
                  id={`profile-${field.key}`}
                  type={field.type}
                  bind:value={basicsDraft[field.key]}
                  maxlength="5000"
                  disabled={busy ||
                    !basics ||
                    (section === "profile" && yamlDirty)}
                />
              </label>
            {/each}
          </div>
          {#if section === "profile" && yamlDirty}<p class="muted">
              You also have unsaved advanced profile edits. Save those first, or
              use Refresh to discard edits before saving basic details.
            </p>{/if}
          <div class="actions">
            <button
              class="button primary"
              type="submit"
              disabled={busy ||
                !basicsDirty ||
                (section === "profile" && yamlDirty)}
              >Review & save my details</button
            >
            <span class="muted"
              >{basicsDirty ? "Unsaved details" : "Details up to date"}</span
            >
          </div>
        </form>
      </section>
      <section class="subpanel" aria-labelledby="preferences-title">
        <h3 id="preferences-title">Next: upload your resume</h3>
        <p class="muted">
          Add a PDF in My resumes, then choose it when preparing an application.
          Uploading does not change a resume already selected for a job.
        </p>
        {#if onresume}<button class="button primary" onclick={openResumes}
            >Upload resume PDF <kbd>n</kbd></button
          >{/if}
        <details>
          <summary>Advanced: job categories & resume defaults</summary>
          <p class="muted">
            Job categories (called “tracks” in configuration) group your
            searches. Each can have a weekly target and preferred resume.
          </p>
          <ul class="track-list">
            {#each data.tracks as track}<li>
                <strong>{track.name}</strong><span
                  >{track.weekly_target} / week</span
                ><span>Configured resume: {track.default_resume || "none"}</span
                >
              </li>{/each}
          </ul>
          <p class="muted">
            Edit targets, search terms, locations, eligibility and resume labels
            in the advanced tracks configuration. Approved resume versions take
            precedence; manage those in My resumes.
          </p>
        </details>
      </section>
    </div>

    <details class="subpanel" bind:open={configOpen}>
      <summary>Advanced: configuration editor <kbd>e</kbd></summary>
      <div class="section-heading">
        <h3 id="config-title">Edit configuration (YAML)</h3>
        <label
          >Section <select
            value={section}
            onchange={selectSection}
            disabled={busy}
            >{#each data.sections as name}<option value={name}
                >{name.replaceAll("_", " ")}</option
              >{/each}</select
          ></label
        >
      </div>
      <p class="muted">
        Named configuration only; no arbitrary files, passwords, tokens or
        authentication settings. Source options are restricted to public
        settings. Changes are validated and saved atomically; stale edits are
        rejected.
      </p>
      {#if document}<p class="muted">{document.notice}</p>{/if}
      <form
        onsubmit={(event) => {
          event.preventDefault();
          saveConfig();
        }}
      >
        <label for="profile-yaml">{section}.yaml</label>
        <textarea
          id="profile-yaml"
          class="code-editor"
          bind:this={editorField}
          bind:value={editor}
          rows="18"
          spellcheck="false"
          readonly={section === "profile" && basicsDirty}
          disabled={!document || busy}
        ></textarea>
        <div class="actions">
          <button
            class="button primary"
            type="submit"
            disabled={busy ||
              !document ||
              editor === document.text ||
              (section === "profile" && basicsDirty)}>Review & save</button
          ><span class="muted"
            >{document && editor !== document.text
              ? "Unsaved changes"
              : "No unsaved changes"}</span
          >
        </div>
        {#if section === "profile" && basicsDirty}<p class="muted">
            Save your basic details before editing the same profile in YAML.
            Your basic details remain intact when opening or closing this
            section.
          </p>{/if}
      </form>
    </details>

    <details class="subpanel" bind:open={answersOpen}>
      <summary
        >Your application answers <kbd>w</kbd> · {data.answers_count} saved</summary
      >
      <h3 id="answers-title">Save answers in your own words</h3>
      <p class="muted">
        Save answers to questions applications ask repeatedly. Unknown questions
        stay unanswered. For matching rules and company-specific answers, open
        “answers” in the advanced configuration editor.
      </p>
      {#if data.questions.length}<label
          >Observed unanswered question<select
            value=""
            onchange={(event) => {
              question = event.currentTarget.value;
            }}
            ><option value="">Choose a question or write one below</option
            >{#each data.questions as item}<option value={item.text}
                >{item.text} ({item.times_seen} observations)</option
              >{/each}</select
          ></label
        >{/if}
      <form
        onsubmit={(event) => {
          event.preventDefault();
          saveAnswer();
        }}
      >
        <label
          >Question<input
            bind:value={question}
            required
            maxlength="5000"
            disabled={busy}
          /></label
        >
        <label
          >Your exact answer<textarea
            bind:this={answerField}
            bind:value={answer}
            required
            maxlength="10000"
            rows="3"
            disabled={busy}
          ></textarea></label
        >
        <button
          class="button primary"
          type="submit"
          disabled={busy || !answer.trim() || !question.trim()}
          >Review & save my answer</button
        >
      </form>
    </details>

    <details class="subpanel">
      <summary>Advanced: check setup & diagnose problems</summary>
      <div class="section-heading">
        <h3 id="doctor-title">Local setup checks</h3>
        <button
          class="button"
          disabled={busy}
          onclick={() =>
            run(async () => {
              checks = (await api<{ checks: Check[] }>("/profile/doctor"))
                .checks;
            })}>Run local checks</button
        >
      </div>
      <p class="muted">
        Checks config, schema, resume files, track defaults and recorded source
        health. No network requests; credentials are presence/missing only.
      </p>
      {#if checks}<ul class="checks">
          {#each checks as check}<li>
              <span class:failed={!check.ok} class="badge"
                >{!check.ok ? "Failed" : check.warning ? "Warning" : "OK"}</span
              >
              <div>
                <strong>{check.name}</strong>
                <p>{check.detail}</p>
                {#if check.fix}<p class="muted">{check.fix}</p>{/if}
              </div>
            </li>{/each}
        </ul>{/if}
    </details>

    <details class="subpanel">
      <summary>Advanced: import past applications from CSV</summary>
      <h3 id="import-title">Import historical applications</h3>
      <p class="muted">
        Preview a UTF-8 CSV before recording anything. Standard columns:
        company, title, url, location, status, applied_at, track, resume, notes.
        Missing status means applied; missing application date means import
        time. Existing applications are not overwritten. Unknown resume labels
        import without an attached resume.
      </p>
      <label
        >CSV file (45 KB maximum)<input
          type="file"
          accept=".csv,text/csv"
          onchange={readCsv}
          disabled={busy}
        /></label
      >
      <label
        >CSV contents<textarea
          class="code-editor"
          bind:value={csvText}
          oninput={() => {
            preview = null;
            previewPayload = null;
          }}
          rows="5"
          disabled={busy}
        ></textarea></label
      >
      <details>
        <summary>Custom column mapping</summary>
        <p class="muted">
          JSON object mapping a standard field to your exact column heading, for
          example {`{"company":"Employer"}`}. Unmapped fields use recognized
          aliases.
        </p>
        <label
          >Column mapping JSON<textarea
            class="code-editor"
            bind:value={mappingText}
            oninput={() => {
              preview = null;
              previewPayload = null;
            }}
            rows="3"
            disabled={busy}
          ></textarea></label
        >
      </details>
      <button
        class="button"
        onclick={previewImport}
        disabled={busy || !csvText.trim()}>Preview import</button
      >
      {#if preview}
        <p>
          {preview.counts.create} new jobs · {preview.counts.update} existing jobs
          gaining applications · {preview.counts.skip} already present · {preview
            .counts.problems} invalid rows skipped
        </p>
        <p class="muted">
          Mapping: {Object.entries(preview.mapping)
            .map(([field, column]) => `${field} ← ${column}`)
            .join(", ")}. Unmapped columns: {preview.unmapped_columns.join(
            ", ",
          ) || "none"}.
        </p>
        <div class="table-scroll">
          <table>
            <thead
              ><tr
                ><th>Line</th><th>Company / role</th><th>History</th><th
                  >Details</th
                ><th>Planned action</th></tr
              ></thead
            ><tbody
              >{#each preview.rows as row}<tr
                  ><td>{row.line}</td><td
                    ><strong>{row.company}</strong><br />{row.title}</td
                  ><td>{row.status}<br />{row.applied_at || "Import time"}</td
                  ><td
                    >{row.location || "No location"}<br />Track: {row.track ||
                      "none"}; resume: {row.resume || "none"}<br /><span
                      class="muted"
                      >{row.url}{row.notes ? ` — ${row.notes}` : ""}</span
                    ></td
                  ><td>{row.problem || row.action}</td></tr
                >{/each}</tbody
            >
          </table>
        </div>
        <button
          class="button primary"
          onclick={confirmImport}
          disabled={busy || !(preview.counts.create + preview.counts.update)}
          >Review & confirm import</button
        >
      {/if}
    </details>

    <details class="subpanel">
      <summary>Advanced: schedule job searches</summary>
      <h3 id="schedule-title">Discovery schedule</h3>
      <p class="muted">
        Generate the same timer output as the CLI. Nothing is installed or
        executed here. Every scheduled run also sends today's digest if your
        local notifier is configured.
      </p>
      <form
        onsubmit={(event) => {
          event.preventDefault();
          printSchedule();
        }}
      >
        <div class="profile-grid">
          <label
            >Format<select bind:value={scheduleFormat}
              ><option value="systemd">systemd user units</option><option
                value="home-manager">Home Manager</option
              ><option value="crontab">crontab</option></select
            ></label
          ><label
            >Local times (comma-separated HH:MM)<input
              bind:value={scheduleTimes}
              required
            /></label
          >
        </div>
        <label class="checkbox"
          ><input type="checkbox" bind:checked={withTriage} /> Include
          unattended paid triage (current cap ${data.triage_cap} per run)</label
        >
        <button class="button" type="submit" disabled={busy}
          >Generate schedule output</button
        >
      </form>
      {#if scheduleOutput}<label
          >Generated schedule — review before installing<textarea
            class="code-editor"
            readonly
            value={scheduleOutput}
            rows="16"
          ></textarea></label
        >{/if}
    </details>

    <details class="subpanel">
      <summary>Advanced: send a daily notification</summary>
      <h3 id="notify-title">Daily digest notification</h3>
      <p class="muted">
        Provider: {data.notifier || "not configured"}. Secrets stay in your
        local environment and are never shown here.
      </p>
      <button
        class="button"
        onclick={sendNotification}
        disabled={busy ||
          demo ||
          !data.notifier ||
          notifyTask?.status === "running"}>Review & send today's digest</button
      >
      <TaskStatus
        task={notifyTask}
        oncomplete={(task) => {
          notifyTask = task;
        }}
      />
    </details>
  {/if}
</section>

<dialog
  bind:this={confirmDialog}
  oncancel={() => {
    pending = null;
  }}
  aria-labelledby="profile-confirm-title"
>
  <h2 id="profile-confirm-title">Review this change</h2>
  <p>{confirmation}</p>
  <div class="actions">
    <button class="button" bind:this={cancelButton} onclick={cancel}
      >Cancel</button
    ><button class="button primary" onclick={confirm}>Confirm</button>
  </div>
</dialog>

<style>
  .profile-page {
    padding-bottom: 1rem;
  }
  .profile-page > p,
  .profile-grid,
  .subpanel {
    margin: 1rem;
  }
  .profile-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1rem;
    align-items: start;
  }
  .setup-layout {
    grid-template-columns: minmax(0, 2fr) minmax(240px, 1fr);
  }
  .profile-grid > .subpanel,
  .subpanel .profile-grid {
    margin: 0;
  }
  .subpanel {
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1.15rem;
    min-width: 0;
    background: var(--surface);
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
  h3 {
    margin: 0 0 0.8rem;
  }
  p {
    line-height: 1.6;
    overflow-wrap: anywhere;
  }
  .section-heading,
  .actions {
    display: flex;
    gap: 0.8rem;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    margin: 0.85rem 0;
    font-size: 0.9rem;
  }
  input,
  select,
  textarea {
    width: 100%;
    box-sizing: border-box;
    padding: 0.65rem;
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--ink);
    background: var(--surface-raised);
    font: inherit;
  }
  textarea {
    resize: vertical;
  }
  .code-editor {
    font-family: ui-monospace, monospace;
    font-size: 0.83rem;
    line-height: 1.55;
  }
  .checkbox {
    flex-direction: row;
    align-items: center;
  }
  .checkbox input {
    width: auto;
  }
  .track-list,
  .checks {
    list-style: none;
    padding: 0;
  }
  .track-list li {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    padding: 0.7rem 0;
    border-bottom: 1px solid var(--border);
  }
  .checks li {
    display: flex;
    align-items: flex-start;
    gap: 1rem;
    padding: 0.75rem 0;
    border-bottom: 1px solid var(--border);
  }
  .checks p {
    margin: 0.3rem 0;
  }
  .failed {
    color: var(--error);
  }
  .success-note {
    color: var(--success);
  }
  kbd {
    font-family: ui-monospace, monospace;
    font-size: 0.75rem;
    color: inherit;
  }
  .table-scroll {
    overflow-x: auto;
    margin: 1rem 0;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.85rem;
  }
  th,
  td {
    text-align: left;
    padding: 0.65rem;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
    overflow-wrap: anywhere;
    max-width: 24rem;
  }
  dialog {
    max-width: min(32rem, 90vw);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 1.5rem;
    background: var(--surface);
    color: var(--ink);
  }
  dialog::backdrop {
    background: #0008;
  }
  @media (max-width: 1050px) {
    .setup-layout {
      grid-template-columns: 1fr;
    }
  }
  @media (max-width: 700px) {
    .profile-grid {
      grid-template-columns: 1fr;
    }
    .subpanel {
      padding: 0.85rem;
    }
  }
</style>
