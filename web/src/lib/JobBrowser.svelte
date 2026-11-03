<script lang="ts" module>
  /** Map/List chosen from Saved jobs: the Find jobs view that opens next shows it. */
  let presentationHandoff: "map" | "list" | null = null;
</script>

<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import { api, dateLabel, safeUrl, type Bootstrap, type Job } from "./api";
  import Icon from "./Icon.svelte";
  import JobDetail from "./JobDetail.svelte";
  import Automation from "./Automation.svelte";
  import SectionGraph, { type SectionNode } from "./SectionGraph.svelte";
  import type { View } from "./workspace";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  import PostingAvailability from "./PostingAvailability.svelte";
  import EmptyState from "./EmptyState.svelte";
  import Loader from "./Loader.svelte";
  import Dashboard from "./Dashboard.svelte";
  import Select, { type SelectOption } from "./ui/Select.svelte";
  import Popover from "./ui/Popover.svelte";
  import EditDrawer from "./ui/EditDrawer.svelte";
  let {
    view,
    workspace,
    revision = 0,
    onchange,
    onadd,
    onview,
    onopenjob,
    onguide,
    initialJobId = null,
  }: {
    view: "today" | "inbox" | "jobs" | "queue" | "applications" | "favorites";
    workspace: Bootstrap | null;
    revision?: number;
    onchange: () => Promise<void>;
    onadd: () => void;
    onview: (view: View) => void;
    onopenjob?: (id: number) => void;
    onguide?: () => void;
    initialJobId?: number | null;
  } = $props();
  const pane = useWorkspaceContext();
  const fieldId = (name: string) => `${pane.id}-${name}`;
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
  const preparationIds = $derived(
    marked.length ? marked : selected !== null ? [selected] : jobs[cursor] ? [jobs[cursor].id] : [],
  );
  let presentation = $state<"map" | "list">(untrack(() => {
    const handoff = presentationHandoff;
    presentationHandoff = null;
    return initialJobId ? "list" : view === "jobs" ? (handoff ?? "map") : "list";
  }));
  /** Map and List switch presentation here; from Saved jobs they open Find jobs. */
  function present(next: "map" | "list") {
    if (view === "jobs") {
      presentation = next;
      return;
    }
    presentationHandoff = next;
    onview("jobs");
  }
  let graphFocus = $state<string | null>(null);
  let graphLabel = $state("");
  function rootSchema() {
    presentation = "map";
    graphFocus = null;
    graphLabel = "";
    track = "";
    selected = null;
    graphRevision += 1;
    void filter();
  }
  $effect(() => {
    const crumbs = [];
    if (graphFocus) crumbs.push({label: graphFocus.slice(6), onclick: () => { selected = null; presentation = "map"; }});
    if (selected !== null) crumbs.push({label: jobs.find(job => job.id === selected)?.title || graphLabel || `Job ${selected}`});
    pane.setTrail(view, crumbs, rootSchema);
    return () => pane.clearTrail(view);
  });
  let graphRevision = $state(0);
  let bulkOptionsOpen = $state(false);
  let filtersOpen = $state(false);
  let results: HTMLDivElement | undefined = $state();
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
    if (busy || loading) return;
    if (!preparationIds.length) {
      message = "Select or focus a job to fill for review.";
      return;
    }
    preparationOpen = true;
    await tick();
    if (pane.active) await automation?.reviewSelected();
  }
  async function selectGraphNode(node: SectionNode) {
    if (node.entity === "track" && typeof node.ref === "string") {
      track = node.ref;
      graphFocus = `track:${node.ref}`;
      graphLabel = node.label;
      await filter();
    } else if (node.entity === "job" && typeof node.ref === "number") {
      const index = jobs.findIndex((job) => job.id === node.ref);
      if (index >= 0) cursor = index;
      pendingCommand = "";
      graphLabel = node.label;
      selected = node.ref;
    }
  }
  let searchInput: HTMLInputElement | undefined = $state();
  let bulkAction = $state("state");
  let bulkValue = $state("");
  const RANK_HELP = "A is highest priority, B is worth considering, C is lower priority or filtered out. Read the reason in each job; rank does not guarantee eligibility.";
  const RANKS: Record<string, string> = { A: "Highest priority", B: "Worth considering", C: "Lower priority / filtered out" };
  const choices = (values: string[], none?: string): SelectOption[] => [
    ...(none ? [{ value: "", label: none }] : []),
    ...values.map((value) => ({ value, label: label(value) })),
  ];
  const trackOptions = $derived<SelectOption[]>([
    { value: "", label: "All job types / tracks" },
    ...(workspace?.tracks || []).map((item) => ({ value: item.name, label: item.label })),
  ]);
  const orderOptions: SelectOption[] = [
    { value: "best", label: "Best fit" },
    { value: "score", label: "Saved-job similarity score" },
    { value: "newest", label: "Newest" },
    { value: "track", label: "Job type / track" },
    { value: "company", label: "Company" },
    { value: "deadline", label: "Deadline" },
  ];
  const rankOptions: SelectOption[] = [{ value: "", label: "All" }, ...Object.keys(RANKS).map((value) => ({ value, label: value }))];
  const savedViewOptions = $derived<SelectOption[]>([
    { value: "", label: "No saved view" },
    ...options.views.map((item) => ({ value: String(item.key), label: item.title, hint: String(item.key) })),
  ]);
  const sourceOptions = $derived<SelectOption[]>([{ value: "", label: "All sources" }, ...options.sources.map((value) => ({ value, label: value }))]);
  const eligibilityOptions = choices(["eligible", "boosted", "ambiguous", "unknown", "ineligible"], "All eligibility");
  const statusOptions = choices(["applied", "oa", "interview", "offer", "rejected", "ghosted", "withdrawn"], "All statuses");
  const stateOptions = choices(["new", "queued", "later", "skipped", "not_eligible", "applied"], "All states");
  const bulkActionOptions: SelectOption[] = [
    { value: "state", label: "Progress" },
    { value: "tier", label: "Rank" },
    { value: "add_track", label: "Category" },
  ];
  const bulkValueOptions = $derived<SelectOption[]>(
    bulkAction === "state"
      ? choices(["new", "queued", "later", "skipped", "not_eligible"])
      : bulkAction === "tier"
        ? Object.entries(RANKS).map(([value, hint]) => ({ value, label: `Rank ${value}`, hint }))
        : (workspace?.tracks || []).map((item) => ({ value: item.name, label: item.label })),
  );
  /** Filters set inside the popover, counted on its button. */
  const activeFilters = $derived(
    [tier, saved, source, eligibility, status, queueState].filter(Boolean).length +
      [includeClosed && view !== "queue", onlyFavorites, companyOnly, view === "inbox" && allDates].filter(Boolean).length,
  );
  const limit = 100;
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
  async function changeBatch(direction: -1 | 1) {
    offset = Math.max(0, offset + direction * limit);
    cursor = 0;
    anchor = -1;
    await load();
    results?.scrollTo({ top: 0 });
  }
  async function refresh() {
    await load();
    graphRevision += 1;
    await onchange();
  }
  async function checkedAvailability(unavailableIds: number[]) {
    const unavailable = new Set(unavailableIds);
    marked = marked.filter(id => !unavailable.has(id));
    if (selected !== null && unavailable.has(selected)) selected = null;
    await refresh();
  }
  async function filter() {
    clearTimeout(searchTimer);
    offset = 0;
    marked = [];
    anchor = -1;
    selected = null;
    cursor = 0;
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
      message = `Selected all ${result.total} matching jobs across every batch.`;
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
    if (action === "delete" && !window.confirm(`Delete ${ids.length} job${ids.length === 1 ? "" : "s"} and their reminders? Submitted applications are protected. Source discovery may import a deleted posting again.`)) return;
    busy = true;
    error = "";
    try {
      const result = await api<{ favorite?: boolean }>("/jobs/bulk", {
        ids,
        action,
        value,
      });
      message = `${action === "delete" ? "Deleted" : action === "favorite" ? (result.favorite ? "Saved" : "Removed from saved jobs:") : "Updated"} ${ids.length} job${ids.length === 1 ? "" : "s"}.`;
      marked = [];
      if (action === "delete" && selected !== null && ids.includes(selected)) selected = null;
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
    if (!pane.active) return false;
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
    const target = event.target as HTMLElement | null;
    if (
      event.defaultPrevented ||
      target?.closest("input:not([type='checkbox']):not([type='radio']), textarea, select, [contenteditable]:not([contenteditable='false']), dialog") ||
      document.querySelector("dialog[open]")
    ) return false;
    // Bare F belongs to panel fullscreen; Shift+F keeps reviewed preparation.
    if (event.shiftKey && event.key.toLowerCase() === "f") {
      event.preventDefault();
      await prepareSelected();
      return true;
    }
    if (selected !== null) {
      if (event.key === "Escape") {
        const id = selected;
        selected = null;
        await tick();
        if (pane.active) results?.querySelector<HTMLButtonElement>(`[data-job-id="${id}"]`)?.focus();
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
      results?.querySelector<HTMLButtonElement>(`[data-job-id="${jobs[cursor]?.id}"]`)?.focus();
      return true;
    }
    if (event.key === "ArrowUp" || (view === "queue" && event.key === "p")) {
      event.preventDefault();
      cursor = Math.max(0, cursor - 1);
      results?.querySelector<HTMLButtonElement>(`[data-job-id="${jobs[cursor]?.id}"]`)?.focus();
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
      document.getElementById(fieldId("track-filter"))?.focus();
      return true;
    }
    if (event.key === "g") {
      event.preventDefault();
      if (!marked.length && jobs[cursor]) marked = [jobs[cursor].id];
      bulkAction = "add_track";
      bulkOptionsOpen = true;
      await tick();
      if (pane.active) document.getElementById(fieldId("bulk-value"))?.focus();
      return true;
    }
    if (event.key === "t" && marked.length) {
      event.preventDefault();
      bulkAction = "tier";
      bulkOptionsOpen = true;
      await tick();
      if (pane.active) document.getElementById(fieldId("bulk-value"))?.focus();
      return true;
    }
    if (event.key === "w" && view === "inbox") {
      event.preventDefault();
      allDates = !allDates;
      await filter();
      return true;
    }
    if (event.key === "w") {
      preparationOpen = true;
      await tick();
      return automation?.handleKey(event) ?? false;
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

{#if error}<p class="error-banner" role="alert">{error}<button class="icon-button" aria-label="Dismiss job error" onclick={() => (error = "")}>×</button></p>{/if}
{#if message}<p class="action-feedback" role="status">{message}<button class="icon-button" aria-label="Dismiss job message" onclick={() => (message = "")}>×</button></p>{/if}
<div class="content-grid" class:with-detail={selected !== null}>
  <section class="opportunities panel" aria-label={heading}>
    {#if view === "today"}
      <div class="panel-heading">
        <div>
          <h2>{heading}<span class="count-pill">{total}</span></h2>
        </div>
        <button class="text-button" onclick={() => onview("jobs")}>View all <Icon name="arrow" size={16} /></button>
      </div>
    {:else}
      <header class="jobs-head">
        <h1 title={view === "queue" ? "Open, unsubmitted jobs ranked A or B" : undefined}>{heading}</h1>
        <span class="total" title={`${total.toLocaleString()} matching`}>{total.toLocaleString()}</span>
        <div class="tools">
          {#if view === "jobs" && graphFocus}<button type="button" class="text-button all-types" onclick={rootSchema}>All job types</button>{/if}
          {#if view === "jobs" || view === "favorites"}
            <nav class="jobs-views" aria-label="Jobs view">
              <button type="button" aria-pressed={view === "jobs" && presentation === "map"} onclick={() => present("map")}>Map</button>
              <button type="button" aria-pressed={view === "jobs" && presentation === "list"} onclick={() => present("list")}>List</button>
              <button type="button" aria-current={view === "favorites" ? "page" : undefined} onclick={() => onview("favorites")}>Saved</button>
              <button type="button" onclick={() => onview("pipeline")}>Applications</button>
              <button type="button" onclick={() => onview("resumes")}>Resumes</button>
              <button type="button" onclick={() => onview("sources")}>Sources</button>
            </nav>
          {/if}
          <button type="button" class="pill primary page-action" onclick={onadd}><Icon name="plus" size={14} />Add a job link</button>
        </div>
      </header>
    {/if}
    {#if view === "jobs"}
      <Dashboard {revision} onopenjob={(id) => onopenjob?.(id)} {onview} {onguide} />
    {/if}
    {#if presentation === "map"}
      <div class="map-stage">
        <SectionGraph bare section="jobs" focus={graphFocus} revision={graphRevision} onselect={selectGraphNode}
          ondelete={(node) => { if (node.entity === "job" && typeof node.ref === "number") return bulk("delete", true, [node.ref]); }} listToggle={false} />
      </div>
    {:else}
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
        <Select id={fieldId("track-filter")} label="Filter by job type or track" kind="search" options={trackOptions} bind:value={track} onchange={filter} />
        <Select id={fieldId("job-order")} label="Sort jobs" kind="list" options={orderOptions} bind:value={order} onchange={filter} />
        <Popover label="Filters" bind:open={filtersOpen} placement="bottom-end" class="job-filters">
          {#snippet trigger(props)}
            <button type="button" class="pill" {...props}>Filters{#if activeFilters}<span class="filter-count">{activeFilters}</span>{/if}</button>
          {/snippet}
          <div class="filter-grid">
            <span class="field-label" title={RANK_HELP}>Rank</span>
            <Select id={fieldId("tier-filter")} label="Rank" options={rankOptions} bind:value={tier} onchange={filter} />
            <span class="field-label">Saved view</span>
            <Select label="Saved view" kind="list" options={savedViewOptions} bind:value={saved} onchange={() => {
              onlyFavorites = false;
              void filter();
            }} />
            <span class="field-label">Source</span>
            <Select label="Source" kind={options.sources.length > 8 ? "search" : "list"} options={sourceOptions} bind:value={source} onchange={filter} />
            <span class="field-label">Eligibility</span>
            <Select label="Eligibility" kind="list" options={eligibilityOptions} bind:value={eligibility} onchange={filter} />
            <span class="field-label">Application status</span>
            <Select label="Application status" kind="list" options={statusOptions} bind:value={status} onchange={filter} />
            <span class="field-label">Preparation progress</span>
            <Select label="Preparation progress" kind="list" options={stateOptions} bind:value={queueState} onchange={filter} />
          </div>
          <div class="filter-checks">
            <label class="inline-check"
              ><input type="checkbox" bind:checked={includeClosed} onchange={filter} disabled={view === "queue"} />Include closed postings</label
            ><label class="inline-check"
              ><input type="checkbox" bind:checked={onlyFavorites} onchange={filter} />Saved jobs only</label
            ><label class="inline-check"
              ><input type="checkbox" bind:checked={companyOnly} onchange={filter} />Company names only</label
            >{#if view === "inbox"}<label class="inline-check"
                ><input type="checkbox" bind:checked={allDates} onchange={filter} />All posting dates <kbd>w</kbd></label
              >{/if}
          </div>
        </Popover>
      </form>
      <div class="selection-toolbar">
        <label class="inline-check"
          ><input
            type="checkbox"
            aria-label="Select this batch"
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
          />Select this batch</label
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
        <span class="toolbar-gap"></span>
        {#if marked.length}
          {#if view !== "favorites"}<button
              class="button"
              disabled={busy || loading}
              onclick={() => bulk("favorite", true)}
              >Save selected ({marked.length})</button
            >{/if}
          <Popover label="Change selected jobs" bind:open={bulkOptionsOpen} placement="bottom-end" class="job-bulk">
            {#snippet trigger(props)}
              <button type="button" class="pill" {...props}>Change selected</button>
            {/snippet}
            <form
              class="bulk-form"
              onsubmit={(event) => {
                event.preventDefault();
                void bulk(bulkAction, bulkValue);
              }}
            >
              <div class="bulk-row">
                <button class="button" type="button" disabled={busy} onclick={() => bulk("favorite", true)}>Save <kbd>*</kbd></button>
                <button class="button" type="button" disabled={busy} onclick={() => bulk("favorite", false)}>Remove from saved jobs</button>
              </div>
              <div class="bulk-row">
                <span class="field-label">Set</span>
                <Select label="Bulk action" options={bulkActionOptions} bind:value={bulkAction} onchange={() => (bulkValue = "")} />
              </div>
              <div class="bulk-row">
                <Select id={fieldId("bulk-value")} label="Bulk value" kind="list" options={bulkValueOptions} bind:value={bulkValue} />
                <button class="button primary" disabled={busy || !bulkValue}>Apply to {marked.length}</button>
              </div>
            </form>
          </Popover>
        {/if}
        <PostingAvailability
          jobIds={marked.length ? marked : jobs.map(job => job.id)}
          label={marked.length ? "Check selected" : "Check this batch"}
          demo={workspace?.demo ?? true}
          oncomplete={checkedAvailability}
        />
        <button type="button" class="text-button" title="Prepare another group or check Firefox progress" onclick={() => (preparationOpen = true)}>Preparation</button>
        {#if marked.length}<span class="muted">Fill stops before submission; you submit each one.</span>{/if}
        <button
          class="button primary"
          disabled={busy || loading || (!marked.length && selected === null && !jobs[cursor])}
          onclick={prepareSelected}
        >Fill {marked.length ? `${marked.length} selected` : selected !== null ? "opened job" : "focused job"} for review <kbd>Shift+F</kbd></button>
      </div>
    {/if}
    <div bind:this={results} class="job-list" class:dense={view !== "today"} role="region" aria-label="Job results" aria-busy={loading}>
      {#if loading}<Loader label="Loading opportunities…" rows={4} />{:else if jobs.length === 0}<EmptyState level={3} title="No matches in this view.">
          <p>Change your filters, add a posting, or discover new jobs from Job sources.</p>
          {#snippet actions()}<button class="button" onclick={() => onview("sources")}>Open Job sources</button>{/snippet}
        </EmptyState>{:else}
        {#each jobs as job, index (job.id)}
          <div class="selectable-job" class:marked={markedSet.has(job.id)} class:focused={cursor === index}>
            {#if view !== "today"}<input
                class="row-check"
                type="checkbox"
                aria-label={`Select ${job.company} — ${job.title}`}
                checked={markedSet.has(job.id)}
                onfocus={() => (cursor = index)}
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
                if (window.getSelection()?.toString()) return;
                event.preventDefault();
                if (!busy) void bulk("delete", true, markedSet.has(job.id) ? marked : [job.id]);
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
        <span role="status"
          >{offset + 1}–{offset + jobs.length} of {total.toLocaleString()} matching · {marked.length} selected across batches</span
        >
        <div>
          <button
            class="button"
            disabled={offset === 0 || loading}
            onclick={() => changeBatch(-1)}>Previous 100</button
          ><button
            class="button"
            disabled={offset + limit >= total || loading}
            onclick={() => changeBatch(1)}>Next 100</button
          >
        </div>
      </div>{/if}
    {/if}
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
  {#if preparationOpen && view !== "today"}
    <EditDrawer title="Prepare applications" onclose={() => (preparationOpen = false)}>
      <!-- Escape in the plan review closes only that dialog, not the drawer. -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="preparation" onkeydown={(event) => { if (event.key === "Escape" && (event.target as HTMLElement).closest("dialog")) event.stopPropagation(); }}>
        <Automation
          bind:this={automation}
          jobIds={preparationIds}
          {track}
          demo={workspace?.demo ?? true}
          onchanged={refresh}
        />
      </div>
    </EditDrawer>
  {/if}
</div>

<style>
  /* Q22: one header row: Fraunces title and count, the view segments, Add last. */
  .jobs-head {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
    min-height: 60px;
    padding: 10px 14px 10px 20px;
  }
  .jobs-head h1 {
    margin: 0;
  }
  .total {
    margin-top: 4px;
    color: var(--muted);
    font: 500 var(--fs-chrome, 10.5px) / 1 var(--font-mono);
    letter-spacing: 0.06em;
  }
  .tools {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
    margin-left: auto;
  }
  .all-types {
    font-size: var(--fs-control);
  }
  .opportunities > .filters {
    border-top: 1px solid var(--hairline);
  }
  /* One pill of segments, drawn like Select's segments: the pill owns the hairline. */
  .jobs-views {
    display: inline-flex;
    align-items: stretch;
    gap: 2px;
    box-sizing: border-box;
    height: var(--control-h);
    padding: 2px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-control);
    background: color-mix(in srgb, var(--solid-inset, #111) 40%, transparent);
  }
  :global(:root body) .jobs-views button {
    height: auto;
    min-height: 0;
    margin: 0;
    padding: 0 10px;
    border: 0;
    border-radius: var(--r-control);
    background: transparent;
    color: var(--muted);
    font: 500 12px/1 var(--font-ui);
    white-space: nowrap;
  }
  :global(:root body) .jobs-views button:hover {
    color: var(--ink);
  }
  /* The pressed segment takes the theme's pressed-toggle pair: the selection must read at
     3:1 against its neighbours (--select-fill is a subtler wash, too faint for that here). */
  :global(:root body) .jobs-views button:is([aria-pressed="true"], [aria-current="page"]) {
    background: var(--press-fill, var(--accent));
    color: var(--press-ink, var(--on-accent));
  }
  :global(:root body) .jobs-views button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  /* The map fills the page surface below the strip; no box of its own. */
  .map-stage {
    position: relative;
    /* Fits under the top bar, header and strip so the floating controls stay on screen. */
    height: clamp(320px, calc(100dvh - var(--workbench-top, 0px) - var(--rika-space, 0px) - 230px), 720px);
    border-top: 1px solid var(--hairline);
    border-radius: 0 0 var(--r-panel) var(--r-panel);
    overflow: hidden;
  }
  .preparation {
    display: contents;
  }
  .preparation :global(.automation.panel) {
    border: 0;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
  }
  .job-list.dense {
    max-height: min(68vh, 880px);
    overflow-y: auto;
    overscroll-behavior: contain;
    scrollbar-gutter: stable;
  }
  .dense .job-row {
    padding: 10px 16px;
    gap: 12px;
  }
  .dense .company-mark {
    width: 32px;
    height: 32px;
    flex-basis: 32px;
    font-size: 10px;
  }
  .selectable-job.focused {
    box-shadow: inset 3px 0 var(--accent);
  }
  .selectable-job:focus-within {
    outline: 1px solid var(--accent);
    outline-offset: -1px;
  }
  .action-feedback {
    padding: 12px 16px;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--success);
    font-size: 12px;
    background: var(--surface);
  }
  .filter-count {
    display: inline-grid;
    place-items: center;
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 12%, transparent);
    color: var(--ink);
    font: 600 10px/1 var(--font-mono);
  }
  /* The filters popover: label column + control column, then the checkboxes. */
  :global(.kitty-popover.job-filters) {
    width: min(380px, calc(100vw - 16px));
  }
  .filter-grid {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    gap: 8px 12px;
    padding: 8px;
    font-size: var(--fs-control);
  }
  .field-label {
    color: var(--muted);
    font: 600 var(--fs-label) / 1.3 var(--font-ui);
  }
  .field-label[title] {
    cursor: help;
    text-decoration: underline dotted color-mix(in srgb, var(--muted) 60%, transparent);
    text-underline-offset: 3px;
  }
  .filter-grid :global(.select-trigger) {
    width: 100%;
  }
  .filter-checks {
    display: grid;
    gap: 6px;
    padding: 10px 8px 8px;
    border-top: 1px solid var(--hairline);
  }
  .inline-check {
    display: flex !important;
    align-items: center;
    gap: 8px;
    font-size: 11px;
  }
  .selection-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    padding: 10px 20px;
    border-bottom: 1px solid var(--border);
    font-size: 11px;
  }
  .toolbar-gap {
    flex: 1;
  }
  .bulk-form {
    display: grid;
    gap: 8px;
    padding: 6px;
  }
  .bulk-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  .bulk-row :global(.select-trigger) {
    flex: 1;
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
    .jobs-head {
      padding: 10px 12px;
    }
    .selection-toolbar {
      padding: 10px 12px;
    }
    .row-check {
      margin-left: 10px;
    }
    .filter-grid {
      grid-template-columns: 1fr;
    }
    .selectable-job .job-row {
      padding-left: 10px;
    }
  }
</style>
