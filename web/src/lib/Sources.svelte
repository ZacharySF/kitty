<script lang="ts">
  import { onMount } from "svelte";
  import {
    api,
    type Source,
    type Discovery,
    type TaskRecord,
    type Bootstrap,
  } from "./api";
  import TaskStatus from "./TaskStatus.svelte";

  let { demo = false }: { demo?: boolean } = $props();
  type SourceDetails = Source & {
    kind: string;
    fragile: boolean;
    last_success: string | null;
    configured: boolean;
    runnable: boolean;
  };
  let sources = $state<SourceDetails[]>([]);
  let discovery = $state<Discovery | null>(null);
  let loading = $state(true);
  let busy = $state(false);
  let error = $state("");
  let notice = $state("");
  let task = $state<TaskRecord | null>(null);
  let selected = $state(0);
  let track = $state("");
  let tracks = $state<NonNullable<Bootstrap["tracks"]>>([]);
  let limit = $state(10);
  let ids = $state("");
  let preview = $state<
    { id: number; company: string; title: string; freshness: string }[] | null
  >(null);
  let confirmation = $state<{
    title: string;
    description: string;
    action: () => Promise<void>;
  } | null>(null);
  const running = $derived(
    busy || task?.status === "running" || discovery?.running,
  );
  const enabledCount = $derived(
    sources.filter((source) => source.enabled).length,
  );

  async function refresh() {
    loading = true;
    error = "";
    try {
      const data = await api<{
        sources: SourceDetails[];
        discovery: Discovery;
      }>("/sources");
      sources = data.sources;
      discovery = data.discovery;
      selected = Math.min(selected, Math.max(0, sources.length - 1));
    } catch (cause) {
      error =
        cause instanceof Error
          ? cause.message
          : "Unable to load source health.";
    } finally {
      loading = false;
    }
  }
  async function request(path: string, body: Record<string, unknown>) {
    busy = true;
    error = "";
    notice = "";
    try {
      const result = await api<{ task?: TaskRecord }>(path, body);
      if (result.task) {
        task = result.task;
        if (task.status !== "running") await complete(task);
      } else {
        notice = "Source configuration saved.";
        await refresh();
      }
    } catch (cause) {
      error =
        cause instanceof Error ? cause.message : "Unable to complete action.";
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
  function discover(source?: SourceDetails) {
    if (source && (!source.runnable || !source.enabled)) return;
    confirm(
      source ? `Run ${source.name}?` : "Discover jobs?",
      "Fetch explicitly enabled free public sources, respecting source restrictions and rate limits. Paid search is excluded. Partial results remain labeled partial; no applications are submitted.",
      () =>
        request(
          source
            ? `/sources/${encodeURIComponent(source.name)}/run`
            : "/sources/run",
          { confirm: true },
        ),
    );
  }
  function toggle(source: SourceDetails) {
    if (!source.configured) return;
    confirm(
      `${source.enabled ? "Disable" : "Enable"} ${source.name}?`,
      "Save this source's enabled state in local configuration. This does not start discovery or change other source options.",
      () =>
        request(`/sources/${encodeURIComponent(source.name)}/toggle`, {
          confirm: true,
          enabled: !source.enabled,
        }),
    );
  }
  function selection() {
    const values = ids.trim() ? ids.trim().split(/[\s,]+/) : [];
    if (
      values.some(
        (value) =>
          !/^[1-9][0-9]*$/.test(value) || !Number.isSafeInteger(Number(value)),
      )
    )
      throw new Error("Enter positive job IDs separated by commas or spaces.");
    return {
      limit,
      track: track || null,
      ...(values.length ? { job_ids: [...new Set(values.map(Number))] } : {}),
    };
  }
  async function previewPostings() {
    busy = true;
    error = "";
    preview = null;
    try {
      preview = (
        await api<{
          jobs: {
            id: number;
            company: string;
            title: string;
            freshness: string;
          }[];
        }>("/postings/refresh", { ...selection(), dry_run: true })
      ).jobs;
    } catch (cause) {
      error =
        cause instanceof Error ? cause.message : "Unable to preview selection.";
    } finally {
      busy = false;
    }
  }
  function refreshPostings() {
    if (!preview?.length) return;
    const jobIds = preview.map((job) => job.id);
    confirm(
      "Refresh selected public postings?",
      `Make bounded public requests for these ${jobIds.length} stored postings. No whole-board discovery, paid calls, login or submission. Blocked or incomplete checks do not prove a job is closed.`,
      () =>
        request("/postings/refresh", {
          confirm: true,
          limit: jobIds.length,
          job_ids: jobIds,
        }),
    );
  }
  async function complete(completed: TaskRecord) {
    task = completed;
    await refresh();
  }
  function dateLabel(value: string) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }
  function keydown(event: KeyboardEvent) {
    const target = event.target as HTMLElement;
    if (
      event.defaultPrevented ||
      event.isComposing ||
      confirmation ||
      event.ctrlKey ||
      event.metaKey ||
      event.altKey ||
      target.closest(
        "input,textarea,select,button,a,summary,dialog,[role=dialog],[contenteditable]:not([contenteditable=false])",
      )
    )
      return;
    if (running || loading) return;
    if (event.key === "j" || event.key === "ArrowDown") {
      event.preventDefault();
      selected = Math.min(sources.length - 1, selected + 1);
    }
    if (event.key === "k" || event.key === "ArrowUp") {
      event.preventDefault();
      selected = Math.max(0, selected - 1);
    }
    if (event.key === "r") {
      event.preventDefault();
      void refresh();
    }
    if (event.key === "d" && !demo) {
      event.preventDefault();
      discover();
    }
    const source = sources[selected];
    if (event.key === "e" && source) {
      event.preventDefault();
      toggle(source);
    }
    if (
      (event.key === "s" || event.key === "Enter") &&
      source?.enabled &&
      !demo
    ) {
      event.preventDefault();
      discover(source);
    }
  }
  onMount(() => {
    void refresh();
    void api<Bootstrap>("/bootstrap")
      .then((data) => (tracks = data.tracks ?? []))
      .catch(
        (cause) =>
          (error =
            cause instanceof Error ? cause.message : "Unable to load tracks."),
      );
  });
</script>

<svelte:window onkeydown={keydown} />
<section class="panel sources" aria-labelledby="sources-title">
  <header class="panel-heading">
    <div>
      <p class="eyebrow">Discovery / source health</p>
      <h2 id="sources-title">Sources</h2>
      <p class="muted">
        Inspect evidence and choose when to look for opportunities.
      </p>
    </div>
    <div class="controls">
      <button class="button" onclick={refresh} disabled={loading || busy}
        >{loading ? "Refreshing…" : "Refresh status"}</button
      ><button
        class="button primary"
        onclick={() => discover()}
        disabled={demo || running || loading}>Discover jobs</button
      >
    </div>
  </header>
  <div class="body">
    <p class="muted">
      {demo
        ? "External requests are disabled in demo mode; local source configuration changes are permitted."
        : "Only explicitly requested, enabled free sources run. Paid web search is excluded. No applications are submitted."}
    </p>
    {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
    {#if notice}<p role="status">{notice}</p>{/if}
    <TaskStatus {task} oncomplete={complete} />
    {#if discovery}<div class="state" aria-live="polite">
        <h3>
          {discovery.running
            ? "Discovery in progress"
            : discovery.error
              ? "Discovery needs attention"
              : "Source health"}
        </h3>
        <span class="badge">{enabledCount} / {sources.length} enabled</span
        >{#if discovery.error}<p class="error-banner">
            {discovery.error}
          </p>{/if}{#if discovery.result}<p>{discovery.result}</p>{/if}
      </div>{/if}
    <p class="muted">
      Keyboard: j/k select · e enable/disable · s/Enter run selected · d
      discover · r refresh status. All mutations require confirmation.
    </p>
    {#if !loading && sources.length === 0}<div class="empty-state">
        <h3>No configured sources</h3>
        <p>
          Add source configuration in Profile. An empty list is not evidence of
          a successful discovery run.
        </p>
      </div>{/if}
    <ul class="source-list">
      {#each sources as source, index (source.name)}
        <li
          class="source-card"
          class:selected={index === selected}
          class:source-disabled={!source.enabled}
        >
          <div class="source-summary">
            <div>
              <button
                class="source-name"
                aria-pressed={index === selected}
                onclick={() => (selected = index)}>{source.name}</button
              >
              <div class="badges">
                <span class="badge"
                  >{source.enabled ? "Enabled" : "Disabled"}</span
                ><span class="badge">{source.status}</span>
              </div>
            </div>
            <dl>
              <div>
                <dt>Last run</dt>
                <dd>
                  {#if source.last_run}<time datetime={source.last_run}
                      >{dateLabel(source.last_run)}</time
                    >{:else}Not recorded{/if}
                </dd>
              </div>
              <div>
                <dt>Jobs seen</dt>
                <dd>{source.jobs_seen ?? "Not reported"}</dd>
              </div>
            </dl>
          </div>
          <p class="muted">
            Kind: {source.kind} · {source.fragile
              ? "Fragile adapter"
              : "Standard adapter"} · Last successful run: {source.last_success
              ? dateLabel(source.last_success)
              : "Not recorded"}
          </p>
          {#if source.error}<p class="error-banner">
              <strong>Source reported:</strong>
              {source.error}
            </p>{/if}
          <div class="controls">
            <button
              class="button"
              disabled={running || !source.configured}
              onclick={() => toggle(source)}
              >{source.enabled ? "Disable" : "Enable"}</button
            ><button
              class="button primary"
              disabled={demo || running || !source.enabled || !source.runnable}
              onclick={() => discover(source)}>Run only this source</button
            >{#if !source.runnable}<span class="muted"
                >No permitted free web adapter; manual records remain available.</span
              >{/if}
          </div>
        </li>
      {/each}
    </ul>
    <section class="posting-refresh" aria-labelledby="refresh-title">
      <h3 id="refresh-title">Refresh stored public postings</h3>
      <p class="muted">
        Preview is local-only. Favorites and stale evidence rank first; explicit
        IDs also allow checking closed jobs. Fetch only these postings, not
        whole boards.
      </p>
      <form
        class="controls"
        onsubmit={(event) => {
          event.preventDefault();
          void previewPostings();
        }}
      >
        <label
          >Maximum postings<input
            type="number"
            min="1"
            max="100"
            required
            bind:value={limit}
            oninput={() => (preview = null)}
          /></label
        >
        <label
          >Track<select bind:value={track} onchange={() => (preview = null)}
            ><option value="">All tracks</option>{#each tracks as item}<option
                value={item.name}>{item.label}</option
              >{/each}</select
          ></label
        >
        <label
          >Exact job IDs (optional)<input
            placeholder="12, 34"
            bind:value={ids}
            oninput={() => (preview = null)}
          /></label
        >
        <button class="button" disabled={running}
          >Preview selection (dry run)</button
        >
      </form>
      {#if preview !== null}<div aria-live="polite">
          {#if preview.length === 0}<p class="empty-state">
              No stored postings match this selection.
            </p>{:else}<ul>
              {#each preview as job}<li>
                  #{job.id} · {job.company} — {job.title}
                  <span class="badge">{job.freshness}</span>
                </li>{/each}
            </ul>
            <button
              class="button primary"
              disabled={demo || running}
              onclick={refreshPostings}
              >Review & refresh these {preview.length} postings</button
            >{/if}
        </div>{/if}
    </section>
    <p class="muted">
      Errors, blocked requests and capped searches are not healthy empty
      results. Edit source options in Profile. Respect robots restrictions and
      cooldowns; do not bypass blocked sources.
    </p>
  </div>
</section>

{#if confirmation}
  <div class="modal-backdrop" role="presentation">
    <div
      class="confirm-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="source-confirm-title"
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
      <h2 id="source-confirm-title">{confirmation.title}</h2>
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
  .sources {
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
  .panel-heading,
  .controls,
  .badges,
  .source-summary,
  .state {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.8rem;
  }
  .panel-heading,
  .source-summary {
    justify-content: space-between;
  }
  .panel-heading p {
    margin-top: 0.4rem;
  }
  .body {
    padding: 1.25rem;
    display: grid;
    gap: 1.2rem;
  }
  .source-list {
    display: grid;
    gap: 0.9rem;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .source-card {
    display: grid;
    gap: 0.9rem;
    padding: 1rem;
    border: 1px solid var(--border);
    border-radius: 0.7rem;
  }
  .source-card.selected {
    border-color: var(--accent);
  }
  .source-disabled .source-name {
    color: var(--muted);
  }
  .source-name {
    background: none;
    border: none;
    padding: 0.25rem 0;
    font: inherit;
    font-weight: 650;
    color: var(--ink);
    cursor: pointer;
    text-align: left;
    overflow-wrap: anywhere;
  }
  .badges {
    margin-top: 0.4rem;
  }
  dl {
    display: flex;
    gap: 1.5rem;
    flex-wrap: wrap;
    margin: 0;
    font-size: 0.85rem;
  }
  dt {
    color: var(--muted);
  }
  dd {
    margin: 0.25rem 0 0;
  }
  .posting-refresh {
    display: grid;
    gap: 1rem;
    border-top: 1px solid var(--border);
    padding-top: 1.2rem;
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
    padding: 0.6rem;
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.45rem;
    max-width: 100%;
  }
  p,
  li {
    overflow-wrap: anywhere;
  }
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgb(0 0 0 / 0.5);
    display: grid;
    place-items: center;
    padding: 1rem;
    z-index: 100;
  }
  .confirm-dialog {
    width: min(34rem, 100%);
    max-height: 90vh;
    overflow: auto;
    background: var(--surface);
    color: var(--ink);
    padding: 1.5rem;
    border-radius: 1rem;
    display: grid;
    gap: 1rem;
  }
  @media (max-width: 600px) {
    .body {
      padding: 1rem;
    }
    .controls > label {
      width: 100%;
    }
  }
</style>
