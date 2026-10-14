<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api, dateLabel, safeUrl, type Bootstrap, type Job } from "./api";
  import Icon from "./Icon.svelte";
  import JobDetail from "./JobDetail.svelte";
  import Automation from "./Automation.svelte";
  import type { View } from "./workspace";
  let {
    view,
    workspace,
    onchange,
    onadd,
    onview,
    onopenjob,
    initialJobId = null,
  }: {
    view: "today" | "inbox" | "jobs" | "queue" | "applications" | "favorites";
    workspace: Bootstrap | null;
    onchange: () => Promise<void>;
    onadd: () => void;
    onview: (view: View) => void;
    onopenjob?: (id: number) => void;
    initialJobId?: number | null;
  } = $props();
  let jobs = $state<Job[]>([]);
  let total = $state(0);
  let loading = $state(true);
  let busy = $state(false);
  let error = $state("");
  let message = $state("");
  let search = $state("");
  let companyOnly = $state(false);
  let track = $state("");
  let tier = $state("");
  let saved = $state("");
  let source = $state("");
  let eligibility = $state("");
  let status = $state("");
  let queueState = $state("");
  let order = $state("best");
  let includeClosed = $state(false);
  let allDates = $state(false);
  let onlyFavorites = $state(false);
  let options = $state<{
    views: { key: number; title: string }[];
    sources: string[];
  }>({ views: [], sources: [] });
  let offset = $state(0);
  let selected = $state<number | null>(null);
  let pendingCommand = $state("");
  let marked = $state<number[]>([]);
  const markedSet = $derived(new Set(marked));
  let cursor = $state(0);
  let anchor = -1;
  let request = 0;
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  let detail:
    { handleKey: (event: KeyboardEvent) => Promise<boolean> } | undefined =
    $state();
  let automation:
    | {
        handleKey: (event: KeyboardEvent) => boolean;
        reviewSelected: () => Promise<void>;
      }
    | undefined = $state();
  let preparationOpen = $state(false);
  let bulkOptionsOpen = $state(false);
  const labels: Record<string, string> = {
    eligible: "Meets known requirements",
    boosted: "Especially suitable",
    ambiguous: "Requirements need review",
    unknown: "Not checked",
    ineligible: "Does not meet requirements",
    new: "Not reviewed",
    queued: "Added to preparation queue",
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
  async function prepareSelected() {
    preparationOpen = true;
    await tick();
    await automation?.reviewSelected();
  }
  let searchInput: HTMLInputElement | undefined = $state();
  let bulkAction = $state("favorite");
  let bulkValue = $state("");
  const limit = 20;
  const heading = $derived(
    view === "today"
      ? "Jobs to explore"
      : view === "inbox"
        ? "New jobs"
        : view === "queue"
          ? "Prepare applications"
          : view === "applications"
            ? "Application list"
            : view === "favorites"
              ? "Saved jobs"
              : "Find jobs",
  );

  function query() {
    const params = new URLSearchParams({
      view: view === "today" ? "jobs" : view === "queue" ? "apply" : view,
      search,
      company: companyOnly ? "1" : "0",
      track,
      tier,
      saved,
      source,
      eligibility,
      status,
      state: queueState,
      order,
      closed: includeClosed ? "1" : "",
      favorites: onlyFavorites ? "1" : "",
      offset: String(offset),
      limit: String(view === "today" ? 5 : limit),
    });
    if (view === "inbox") params.set("age", allDates ? "" : "14");
    return params;
  }
  async function load() {
    const version = ++request;
    loading = true;
    error = "";
    try {
      const result = await api<{ jobs: Job[]; total: number }>(
        `/jobs?${query()}`,
      );
      if (version !== request) return;
      jobs = result.jobs;
      total = result.total;
      cursor = Math.min(cursor, Math.max(0, jobs.length - 1));
    } catch (cause) {
      if (version === request) error = (cause as Error).message;
    } finally {
      if (version === request) loading = false;
    }
  }
  async function refresh() {
    await load();
    await onchange();
  }
  async function filter() {
    clearTimeout(searchTimer);
    offset = 0;
    marked = [];
    anchor = -1;
    selected = null;
    await load();
  }
  function searchChanged() {
    clearTimeout(searchTimer);
    request += 1;
    loading = true;
    marked = [];
    selected = null;
    searchTimer = setTimeout(() => void filter(), 180);
  }
  onMount(() => {
    selected = initialJobId;
    void load();
    void api<typeof options>("/job-options")
      .then((result) => (options = result))
      .catch((cause) => (error = cause.message));
    return () => {
      clearTimeout(searchTimer);
      request += 1;
    };
  });

  function mark(index: number, checked: boolean, range = false) {
    const next = new Set(marked);
    const start = range && anchor >= 0 ? Math.min(anchor, index) : index;
    const end = range && anchor >= 0 ? Math.max(anchor, index) : index;
    for (let i = start; i <= end; i++) {
      if (checked) next.add(jobs[i].id);
      else next.delete(jobs[i].id);
    }
    marked = [...next];
    anchor = index;
    cursor = index;
  }
  async function selectAll() {
    const version = request;
    const parameters = query().toString();
    busy = true;
    error = "";
    try {
      const result = await api<{ ids: number[]; total: number }>(
        `/jobs/selection?${parameters}`,
      );
      if (version !== request || parameters !== query().toString()) return;
      marked = result.ids;
      message = `Selected all ${result.total} matching jobs across every page.`;
    } catch (cause) {
      if (version === request) error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
  async function bulk(
    action: string,
    value: unknown,
    ids = marked.length ? marked : jobs[cursor] ? [jobs[cursor].id] : [],
  ) {
    if (!ids.length) {
      message = "Select an opportunity first.";
      return;
    }
    busy = true;
    error = "";
    try {
      const result = await api<{ favorite?: boolean }>("/jobs/bulk", {
        ids,
        action,
        value,
      });
      message = `${action === "favorite" ? (result.favorite ? "Saved" : "Removed from saved jobs:") : "Updated"} ${ids.length} job${ids.length === 1 ? "" : "s"}.`;
      marked = [];
      await refresh();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
  function openAction(command: string) {
    const job = jobs[cursor];
    if (!job) return;
    pendingCommand = command;
    selected = job.id;
  }
  export async function handleKey(event: KeyboardEvent): Promise<boolean> {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
      event.preventDefault();
      const next = view === "inbox";
      if (companyOnly !== next && search) searchChanged();
      companyOnly = next;
      searchInput?.focus();
      searchInput?.select();
      return true;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return false;
    if (selected !== null) {
      if (event.key === "Escape") {
        const id = selected;
        selected = null;
        await tick();
        document
          .querySelector<HTMLButtonElement>(`[data-job-id="${id}"]`)
          ?.focus();
        return true;
      }
      return (await detail?.handleKey(event)) ?? false;
    }
    if (event.key === "Escape" && search) {
      search = "";
      await filter();
      return true;
    }
    if (event.key === "/") {
      event.preventDefault();
      companyOnly = false;
      searchInput?.focus();
      return true;
    }
    if (
      event.key === "ArrowDown" ||
      event.key === "j" ||
      (view === "queue" && event.key === "n")
    ) {
      event.preventDefault();
      cursor = Math.min(jobs.length - 1, cursor + 1);
      document
        .querySelector<HTMLButtonElement>(`[data-job-id="${jobs[cursor]?.id}"]`)
        ?.focus();
      return true;
    }
    if (event.key === "ArrowUp" || (view === "queue" && event.key === "p")) {
      event.preventDefault();
      cursor = Math.max(0, cursor - 1);
      document
        .querySelector<HTMLButtonElement>(`[data-job-id="${jobs[cursor]?.id}"]`)
        ?.focus();
      return true;
    }
    if (event.key === " " && jobs[cursor]) {
      event.preventDefault();
      mark(cursor, !markedSet.has(jobs[cursor].id));
      return true;
    }
    if (event.key === "Enter" && jobs[cursor]) {
      event.preventDefault();
      openAction("");
      return true;
    }
    if (event.key === "*") {
      event.preventDefault();
      await bulk("favorite", "toggle");
      return true;
    }
    if (event.key === "x") {
      event.preventDefault();
      await bulk(
        view === "queue" ? "not_eligible" : "state",
        view === "queue" ? undefined : "skipped",
      );
      return true;
    }
    if (view === "queue" && ["s", "l"].includes(event.key)) {
      event.preventDefault();
      await bulk("state", event.key === "s" ? "skipped" : "later");
      return true;
    }
    if (event.key === "a") {
      event.preventDefault();
      if (view === "queue") openAction("a");
      else onadd();
      return true;
    }
    if (event.key === "r") {
      event.preventDefault();
      await refresh();
      return true;
    }
    if (event.key === "v") {
      event.preventDefault();
      onlyFavorites = !onlyFavorites;
      if (view !== "inbox") {
        saved = "";
        includeClosed = onlyFavorites;
      }
      await filter();
      return true;
    }
    if (event.key === "c") {
      event.preventDefault();
      document.getElementById("track-filter")?.focus();
      return true;
    }
    if (event.key === "g") {
      event.preventDefault();
      if (!marked.length && jobs[cursor]) marked = [jobs[cursor].id];
      bulkAction = "add_track";
      bulkOptionsOpen = true;
      await tick();
      document.getElementById("bulk-value")?.focus();
      return true;
    }
    if (event.key === "t" && marked.length) {
      event.preventDefault();
      bulkAction = "tier";
      bulkOptionsOpen = true;
      await tick();
      document.getElementById("bulk-value")?.focus();
      return true;
    }
    if (event.key === "w" && view === "inbox") {
      event.preventDefault();
      allDates = !allDates;
      await filter();
      return true;
    }
    if (["f", "F", "w"].includes(event.key)) {
      if (event.key === "f" && !marked.length && jobs[cursor]) {
        event.preventDefault();
        openAction("f");
      } else {
        preparationOpen = true;
        await tick();
        automation?.handleKey(event);
      }
      return true;
    }
    if (event.key === "o" && jobs[cursor]) {
      event.preventDefault();
      const url = safeUrl(jobs[cursor].url);
      if (url) window.open(url, "_blank", "noopener,noreferrer");
      return true;
    }
    if (["t", "k", "s", "u", "d", "n", "R", "e", "q"].includes(event.key)) {
      event.preventDefault();
      openAction(event.key);
      return true;
    }
    if (
      /^\d$/.test(event.key) &&
      options.views.some((item) => String(item.key) === event.key)
    ) {
      event.preventDefault();
      saved = event.key;
      onlyFavorites = false;
      await filter();
      return true;
    }
    return false;
  }
</script>

{#if error}<p class="error-banner" role="alert">{error}</p>{/if}
{#if message}<p class="action-feedback" role="status">{message}</p>{/if}
<div class="content-grid" class:with-detail={selected !== null}>
  <section class="opportunities panel" aria-label={heading}>
    <div class="panel-heading">
      <div>
        <span class="eyebrow"
          >{view === "today" ? "A PLACE TO START" : "YOUR SHORTLIST"}</span
        >
        <h2>{heading}<span class="count-pill">{total}</span></h2>
      </div>
      {#if view === "today"}<button
          class="text-button"
          onclick={() => onview("jobs")}
          >View all <Icon name="arrow" size={16} /></button
        >{:else}<button class="button" onclick={onadd}>Add a job link</button
        >{/if}
    </div>
    {#if view === "queue"}
      <div class="flow-guide">
        <h3>You stay in charge of every application</h3>
        <ol>
          <li>Review jobs and select the ones you want.</li>
          <li>
            Prepare selected: review the exact plan, then fill known answers in
            Firefox.
          </li>
          <li>
            Check each form, answer anything left blank, and submit yourself.
          </li>
        </ol>
        <p>
          This list shows open, unsubmitted jobs ranked A or B. Prefer your own
          shortlist?
        </p>
        <button class="text-button" onclick={() => onview("favorites")}
          >Open my saved jobs <Icon name="arrow" size={15} /></button
        >
      </div>
    {/if}
    {#if view !== "today"}
      <form
        class="filters"
        onsubmit={(event) => {
          event.preventDefault();
          void filter();
        }}
      >
        <label class="search-field"
          ><Icon name="jobs" size={17} /><input
            bind:this={searchInput}
            bind:value={search}
            oninput={searchChanged}
            placeholder={companyOnly
              ? "Search company names…"
              : "Search roles or companies…"}
            aria-label="Search roles or companies"
          /><kbd>Ctrl F</kbd></label
        >
        <label class="sr-only" for="track-filter">Filter by category</label
        ><select id="track-filter" bind:value={track} onchange={filter}
          ><option value="">All categories</option
          >{#each workspace?.tracks || [] as item}<option value={item.name}
              >{item.label}</option
            >{/each}</select
        >
        <button class="button" type="submit">Search</button>
      </form>
      <details class="advanced-filters">
        <summary>More filters</summary>
        <div class="filter-grid">
          <label
            >Rank<select id="tier-filter" bind:value={tier} onchange={filter}>
              <option value="">All ranks</option>
              <option value="A">A — Highest priority</option>
              <option value="B">B — Worth considering</option>
              <option value="C">C — Lower priority / filtered out</option>
            </select></label
          >
          <label
            >Saved view<select
              bind:value={saved}
              onchange={() => {
                onlyFavorites = false;
                void filter();
              }}
              ><option value="">No saved view</option
              >{#each options.views as item}<option value={String(item.key)}
                  >{item.key} · {item.title}</option
                >{/each}</select
            ></label
          >
          <label
            >Source<select bind:value={source} onchange={filter}
              ><option value="">All sources</option
              >{#each options.sources as item}<option>{item}</option
                >{/each}</select
            ></label
          >
          <label
            >Eligibility<select bind:value={eligibility} onchange={filter}
              ><option value="">All eligibility</option
              >{#each ["eligible", "boosted", "ambiguous", "unknown", "ineligible"] as item}<option
                  value={item}>{label(item)}</option
                >{/each}</select
            ></label
          >
          <label
            >Application status<select bind:value={status} onchange={filter}
              ><option value="">All statuses</option
              >{#each ["applied", "oa", "interview", "offer", "rejected", "ghosted", "withdrawn"] as item}<option
                  value={item}>{label(item)}</option
                >{/each}</select
            ></label
          >
          <label
            >Preparation progress<select
              bind:value={queueState}
              onchange={filter}
              ><option value="">All states</option
              >{#each ["new", "queued", "later", "skipped", "not_eligible", "applied"] as item}<option
                  value={item}>{label(item)}</option
                >{/each}</select
            ></label
          >
          <label
            >Order<select bind:value={order} onchange={filter}
              ><option value="best">Best fit</option><option value="newest"
                >Newest</option
              ><option value="company">Company</option><option value="deadline"
                >Deadline</option
              ></select
            ></label
          >
          <label class="inline-check"
            ><input
              type="checkbox"
              bind:checked={includeClosed}
              onchange={filter}
              disabled={view === "queue"}
            />Include closed postings</label
          ><label class="inline-check"
            ><input
              type="checkbox"
              bind:checked={onlyFavorites}
              onchange={filter}
            />Saved jobs only</label
          ><label class="inline-check"
            ><input
              type="checkbox"
              bind:checked={companyOnly}
              onchange={filter}
            />Company names only</label
          >{#if view === "inbox"}<label class="inline-check"
              ><input
                type="checkbox"
                bind:checked={allDates}
                onchange={filter}
              />All posting dates <kbd>w</kbd></label
            >{/if}
        </div>
        <p class="muted">
          Ranks help you decide what to review first: A is highest priority, B
          is worth considering, and C is lower priority or filtered out. Read
          the reason in each job; rank does not guarantee eligibility.
        </p>
      </details>
      <div class="selection-toolbar">
        <label class="inline-check"
          ><input
            type="checkbox"
            aria-label="Select this page"
            disabled={busy || loading}
            checked={jobs.length > 0 &&
              jobs.every((job) => markedSet.has(job.id))}
            onchange={(event) => {
              const next = new Set(marked);
              for (const job of jobs) {
                if (event.currentTarget.checked) next.add(job.id);
                else next.delete(job.id);
              }
              marked = [...next];
            }}
          />Select this page</label
        ><button
          class="text-button"
          disabled={busy || loading || !total}
          onclick={selectAll}
          >Select all {total.toLocaleString()} matching</button
        ><span class="muted">{marked.length} selected</span
        >{#if marked.length}<button
            class="text-button"
            onclick={() => (marked = [])}>Clear selection</button
          >{/if}
      </div>
      {#if marked.length}
        <div class="selection-toolbar">
          {#if view !== "favorites"}<button
              class="button"
              class:primary={view !== "queue"}
              disabled={busy || loading}
              onclick={() => bulk("favorite", true)}
              >Save selected ({marked.length})</button
            >{/if}
          <button
            class="button"
            class:primary={view === "queue" || view === "favorites"}
            disabled={busy || loading}
            onclick={prepareSelected}>Prepare selected ({marked.length})</button
          >
        </div>
        <details class="advanced-filters" bind:open={bulkOptionsOpen}>
          <summary>More actions for selected jobs</summary>
          <form
            class="bulk-toolbar"
            onsubmit={(event) => {
              event.preventDefault();
              void bulk(
                bulkAction,
                bulkAction === "favorite"
                  ? true
                  : bulkAction === "unfavorite"
                    ? false
                    : bulkValue,
              );
            }}
          >
            <button
              class="button"
              type="button"
              disabled={busy}
              onclick={() => bulk("favorite", true)}
              >Save selected <kbd>*</kbd></button
            ><button
              class="button"
              type="button"
              disabled={busy}
              onclick={() => bulk("favorite", false)}
              >Remove from saved jobs</button
            >
            <label class="sr-only" for="bulk-action">Bulk action</label><select
              id="bulk-action"
              bind:value={bulkAction}
              onchange={() => (bulkValue = "")}
              ><option value="favorite">Save jobs</option><option value="state"
                >Preparation progress</option
              ><option value="tier">Rank</option><option value="add_track"
                >Add category</option
              ></select
            >
            {#if bulkAction !== "favorite"}<label
                class="sr-only"
                for="bulk-value">Bulk value</label
              ><select id="bulk-value" bind:value={bulkValue}
                ><option value="" disabled>Choose…</option
                >{#if bulkAction === "state"}{#each ["new", "queued", "later", "skipped", "not_eligible"] as value}<option
                      {value}>{label(value)}</option
                    >{/each}{:else if bulkAction === "tier"}<option value="A"
                    >A — Highest priority</option
                  ><option value="B">B — Worth considering</option><option
                    value="C">C — Lower priority / filtered out</option
                  >{:else}{#each workspace?.tracks || [] as value}<option
                      value={value.name}>{value.label}</option
                    >{/each}{/if}</select
              >{/if}<button
              class="button"
              disabled={busy || (bulkAction !== "favorite" && !bulkValue)}
              >Apply to {marked.length}</button
            >
          </form>
        </details>{/if}
      <p class="shortcut-hint">
        Ctrl+F to search · Right-click a job to save it (or its selected group,
        including other pages) · Shift-click checkboxes for a range
      </p>
      <details class="advanced-filters" bind:open={preparationOpen}>
        <summary>Prepare another group or check Firefox progress</summary>
        <Automation
          bind:this={automation}
          jobIds={marked}
          {track}
          demo={workspace?.demo ?? true}
          onchanged={refresh}
        />
      </details>
    {/if}
    <div class="job-list" aria-busy={loading}>
      {#if loading}<p class="empty-state" role="status">
          Loading opportunities…
        </p>{:else if jobs.length === 0}<div class="empty-state">
          <h3>No matches in this view.</h3>
          <p>
            Change your filters, add a posting, or discover new jobs from Job
            sources.
          </p>
          <button class="button" onclick={() => onview("sources")}
            >Open Job sources</button
          >
        </div>{:else}
        {#each jobs as job, index (job.id)}
          <div class="selectable-job" class:marked={markedSet.has(job.id)}>
            {#if view !== "today"}<input
                class="row-check"
                type="checkbox"
                aria-label={`Select ${job.company} — ${job.title}`}
                checked={markedSet.has(job.id)}
                onclick={(event) =>
                  mark(index, event.currentTarget.checked, event.shiftKey)}
              />{/if}
            <button
              class="job-row"
              data-job-id={job.id}
              class:selected={selected === job.id}
              aria-pressed={selected === job.id}
              onfocus={() => (cursor = index)}
              onclick={() => {
                cursor = index;
                pendingCommand = "";
                if (view === "today" && onopenjob) onopenjob(job.id);
                else selected = job.id;
              }}
              oncontextmenu={(event) => {
                event.preventDefault();
                if (!busy)
                  void bulk(
                    "favorite",
                    true,
                    markedSet.has(job.id) ? marked : [job.id],
                  );
              }}
            >
              <span class="company-mark" class:alternate={job.id % 3 === 0}
                >{job.company.slice(0, 2).toUpperCase()}</span
              ><span class="job-main"
                ><span class="job-title">{job.title}</span><span
                  class="job-company"
                  >{job.company}<span class="inline-dot">·</span
                  >{job.location || "Location not provided"}</span
                ><span class="job-meta"
                  >{job.tracks.join(" / ")}{#if job.deadline_at}<span
                      class="inline-dot">·</span
                    >Due {dateLabel(
                      job.deadline_at,
                    )}{/if}{#if job.fill_state && job.fill_state !== "unattempted"}<span
                      class="inline-dot">·</span
                    >Preparation: {label(job.fill_state)}{/if}</span
                ></span
              ><span class="job-tail"
                >{#if job.favorite}<span class="saved-star" aria-label="Saved"
                    ><Icon name="favorites" size={15} /></span
                  >{/if}<span class="badge" class:tier-a={job.tier === "A"}
                  >{job.status
                    ? label(job.status)
                    : job.tier
                      ? `Rank ${job.tier}`
                      : "Not ranked"}</span
                ><Icon name="arrow" size={17} /></span
              >
            </button>
          </div>
        {/each}
      {/if}
    </div>
    {#if view !== "today" && total > 0}<div class="pagination">
        <span
          >{offset + 1}–{Math.min(offset + limit, total)} of {total.toLocaleString()}</span
        >
        <div>
          <button
            class="button"
            disabled={offset === 0 || loading}
            onclick={() => {
              offset = Math.max(0, offset - limit);
              void load();
            }}>Previous</button
          ><button
            class="button"
            disabled={offset + limit >= total || loading}
            onclick={() => {
              offset += limit;
              void load();
            }}>Next</button
          >
        </div>
      </div>{/if}
  </section>
  {#if selected !== null}{#key selected}<JobDetail
        bind:this={detail}
        id={selected}
        initialCommand={pendingCommand}
        demo={workspace?.demo ?? true}
        onclose={() => {
          const id = selected;
          selected = null;
          void tick().then(() =>
            document
              .querySelector<HTMLButtonElement>(`[data-job-id="${id}"]`)
              ?.focus(),
          );
        }}
        onchange={refresh}
      />{/key}{/if}
</div>

<style>
  .flow-guide {
    padding: 0 20px 16px;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.7;
  }
  .flow-guide h3 {
    color: var(--ink);
    margin: 0;
  }
  .flow-guide ol {
    margin-bottom: 0;
    padding-left: 20px;
  }
  .action-feedback {
    padding: 12px 16px;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--success);
    font-size: 12px;
    background: var(--surface);
  }
  .advanced-filters {
    padding: 12px 20px;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
  }
  summary {
    cursor: pointer;
    color: var(--muted);
    min-height: 24px;
  }
  .filter-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 12px;
    padding-top: 14px;
  }
  .filter-grid label {
    display: grid;
    gap: 6px;
    color: var(--muted);
  }
  .filter-grid select {
    width: 100%;
    min-width: 0;
  }
  .inline-check {
    display: flex !important;
    align-items: center;
    gap: 8px;
    font-size: 11px;
  }
  .selection-toolbar,
  .bulk-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    padding: 10px 20px;
    border-bottom: 1px solid var(--border);
    font-size: 11px;
  }
  .bulk-toolbar select {
    max-width: 180px;
    font-size: 11px;
  }
  .shortcut-hint {
    margin: 0;
    padding: 9px 20px;
    background: var(--chrome);
    color: var(--muted);
    font-size: 10px;
    line-height: 1.7;
  }
  .selectable-job {
    display: flex;
    align-items: center;
    border-bottom: 1px solid var(--border);
  }
  .selectable-job:last-child {
    border-bottom: 0;
  }
  .selectable-job .job-row {
    border: 0;
    min-width: 0;
  }
  .selectable-job.marked {
    background: var(--surface-raised);
  }
  .row-check {
    flex-shrink: 0;
    margin: 0 0 0 16px;
    accent-color: var(--accent);
    width: 16px;
    height: 16px;
  }
  @media (max-width: 600px) {
    .selection-toolbar,
    .bulk-toolbar,
    .advanced-filters {
      padding: 10px 12px;
    }
    .shortcut-hint {
      padding: 9px 12px;
    }
    .row-check {
      margin-left: 10px;
    }
    .filter-grid {
      grid-template-columns: 1fr 1fr;
    }
    .selectable-job .job-row {
      padding-left: 10px;
    }
    .bulk-toolbar select {
      max-width: 130px;
    }
  }
</style>
