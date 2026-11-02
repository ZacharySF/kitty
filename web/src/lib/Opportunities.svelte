<script lang="ts">
  import { api, type TaskRecord } from "./api";
  import TaskStatus from "./TaskStatus.svelte";
  import AIReview from "./AIReview.svelte";
  import SectionGraph, {type SectionNode} from "./SectionGraph.svelte";
  import Icon from "./Icon.svelte";
  import Popover from "./ui/Popover.svelte";
  import Select, { type SelectOption } from "./ui/Select.svelte";
  import QuickAdd from "./ui/QuickAdd.svelte";
  import EditDrawer from "./ui/EditDrawer.svelte";
  import OverflowMenu, { type OverflowItem } from "./ui/OverflowMenu.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  import type { Snippet } from "svelte";
  /**
   * Q21: map first. The category → source → opportunity map fills the page's one
   * surface; Filters, List, Add (a quick-add popover) and ⋯ float over it in one row;
   * the list slides in as a drawer on the right, and an opened item edits in the
   * shared side drawer, as do Interests & monitor, Import and the full Add form.
   */
  const pane = useWorkspaceContext();
  const LIST_KEY = "kitty.opportunities.list";
  let listOpen = $state(readListOpen());
  function readListOpen() {
    try { return localStorage.getItem(LIST_KEY) === "open"; } catch { return false; }
  }
  function showList(open: boolean) {
    listOpen = open;
    try { localStorage.setItem(LIST_KEY, open ? "open" : "closed"); } catch { /* Storage can be unavailable; the drawer still opens. */ }
  }
  /** The side drawer: an opened item (`detail`) or one of these forms. */
  let panel = $state<null | "new" | "import" | "monitor">(null);
  function openPanel(next: "new" | "import" | "monitor") {
    if (!pane.active) return;
    detail = null;
    pendingDelete = false;
    panel = next;
  }
  let schemaFocus = $state<string | null>(null);
  let schemaRevision = $state(0);
  function rootSchema() { if (!pane.active) return; schemaFocus = null; detail = null; }
  function chooseNode(node: SectionNode) {
    if (!pane.active) return;
    if (node.entity === "category") schemaFocus = `category:${node.ref}`;
    else if (node.entity === "opportunity" && typeof node.ref === "number") void open(node.ref);
    else if (node.entity === "source") openPanel("monitor");
  }
  $effect(() => {
    const crumbs = [];
    if (schemaFocus) crumbs.push({label: schemaFocus.slice(9), onclick: () => { if (pane.active) detail = null; }});
    if (detail) crumbs.push({label: detail.title});
    pane.setTrail("opportunities", crumbs, rootSchema);
    return () => pane.clearTrail("opportunities");
  });
  type ProjectItem = {
    id: number;
    kind: string;
    title: string;
    due: string | null;
    url: string;
    notes: string;
    done: boolean;
    revision: number;
  };
  type Project = {
    id: number;
    title: string;
    notes: string;
    revision: number;
    items: ProjectItem[];
  };
  type Opportunity = {
    id: number;
    title: string;
    url: string;
    source: string;
    category: string;
    description: string;
    location: string;
    remote: boolean | null;
    start: string | null;
    end: string | null;
    deadline: string | null;
    timezone: string;
    availability: string;
    status: string;
    favorite: boolean;
    notes: string;
    tags: string[];
    overrides: string[];
    revision: number;
    score: number;
    reasons: string[];
    evidence?: {
      source: string;
      url: string;
      observed_at: string;
      facts: Record<string, unknown>;
    }[];
    project?: Project | null;
  };
  type Source = {
    id: string;
    name: string;
    url: string;
    enabled: boolean;
    revision: number;
    last_run: string | null;
    last_status: string;
    last_error: string | null;
    last_count: number;
  };
  type Profile = {
    major: string;
    track: string;
    interests: string[];
    locations: string[];
    prefer_remote: boolean;
  };
  type Schedule = {
    enabled: boolean;
    interval_hours: number;
    last_run: string | null;
    next_run: string | null;
    last_error: string | null;
    revision: number;
  };
  let { revision = 0, demo = false }: { revision?: number; demo?: boolean } =
    $props();
  let items = $state<Opportunity[]>([]),
    sources = $state<Source[]>([]),
    statuses = $state<string[]>([]);
  let profile = $state<Profile>({
    major: "",
    track: "",
    interests: [],
    locations: [],
    prefer_remote: false,
  });
  let schedule = $state<Schedule>({
    enabled: false,
    interval_hours: 24,
    last_run: null,
    next_run: null,
    last_error: null,
    revision: 1,
  });
  let interests = $state(""),
    locations = $state("");
  let selected = $state<number[]>([]),
    focused = $state<number | null>(null),
    detail = $state<Opportunity | null>(null);
  let search = $state(""),
    source = $state(""),
    category = $state(""),
    status = $state(""),
    location = $state(""),
    remote = $state(""),
    deadline = $state(""),
    availability = $state(""),
    favoritesOnly = $state(false);
  let error = $state(""),
    notice = $state(""),
    busy = $state(false),
    task = $state<TaskRecord | null>(null);
  let draft = $state({
    title: "",
    url: "",
    category: "project",
    location: "",
    remote: null as boolean | null,
    description: "",
    deadline: "",
    timezone: "America/Chicago",
  });
  let importText = $state(""),
    tags = $state("");
  let projectDraft = $state({
    kind: "task",
    title: "",
    due: "",
    url: "",
    notes: "",
  });
  let editingItem = $state<number | null>(null);
  let summaryText = $state(""),
    aiResult = $state("");
  let pendingDelete = $state(false);
  // Quick add: a title or a link, plus a category and a deadline chip.
  let quick = $state(""),
    quickCategory = $state("project"),
    quickDeadline = $state("");
  const STATUS_LABELS: Record<string, string> = {
    discovered: "Discovered", new: "New", saved: "Saved", queued: "Queued", applied: "Applied",
    participating: "Participating", completed: "Completed", skipped: "Skipped",
  };
  const statusLabel = (value: string) => STATUS_LABELS[value] ?? value.charAt(0).toUpperCase() + value.slice(1);
  const AVAILABILITY: SelectOption[] = [
    { value: "", label: "Any dates" },
    { value: "future", label: "Upcoming" },
    { value: "active", label: "Open now" },
    { value: "closed", label: "Closed" },
    { value: "unknown", label: "Dates unknown" },
  ];
  /** Remote is three-valued (yes, no, unknown); segments need string values. */
  const REMOTE_CHOICES: SelectOption[] = [
    { value: "unknown", label: "Unknown" },
    { value: "true", label: "Remote" },
    { value: "false", label: "In person" },
  ];
  /** A category keeps one theme colour slot wherever it appears. */
  function tone(category: string) {
    let hash = 0;
    for (const char of category) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
    return `var(--c${(hash % 6) + 1})`;
  }
  const shortDate = (value: string) => {
    const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T12:00:00` : value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };
  const soon = (value: string | null) => {
    if (!value) return false;
    const days = (new Date(value).getTime() - Date.now()) / 86_400_000;
    return days >= 0 && days <= 21;
  };
  /**
   * Full-bleed: the surface runs from where it starts down to the dock. Notices above it
   * (the demo pill) come and go, so the height is measured, not a fixed offset.
   */
  function fillWindow(node: HTMLElement) {
    const measure = () => {
      if (!node.offsetParent) return;
      const dock = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--dock-space")) || 0;
      const top = node.getBoundingClientRect().top + window.scrollY;
      node.style.height = `${Math.max(420, Math.floor(innerHeight - top - dock - 14))}px`;
    };
    let frame = 0;
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(measure); };
    const main = node.closest("main");
    const resize = new ResizeObserver(schedule);
    if (main) resize.observe(main);
    // A tab shown again (it was hidden) is measured as it reappears.
    const shown = new IntersectionObserver(schedule);
    shown.observe(node);
    const notices = new MutationObserver(schedule);
    if (main) notices.observe(main, { childList: true });
    window.addEventListener("resize", measure);
    measure();
    return { destroy: () => { cancelAnimationFrame(frame); resize.disconnect(); shown.disconnect(); notices.disconnect(); window.removeEventListener("resize", measure); } };
  }
  const remoteValue = (value: boolean | null) => (value === null ? "unknown" : String(value));
  const remoteFrom = (value: string) => (value === "unknown" ? null : value === "true");
  const filterCount = $derived(
    [source, category, status, location, remote, deadline, availability].filter(Boolean).length + (favoritesOnly ? 1 : 0),
  );
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  /** Filters and search narrow the list, so the list drawer opens to show the result. */
  function applyFilters() {
    clearTimeout(searchTimer);
    showList(true);
    void refresh();
  }
  function searchChanged() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(applyFilters, 250);
  }
  function clearFilters() {
    source = category = status = location = remote = deadline = availability = "";
    favoritesOnly = false;
    void refresh();
  }
  /** A pasted link becomes the source URL; its host and last path segment stand in for a title. */
  function phrase(text: string) {
    if (!/^https?:\/\//i.test(text)) return { title: text };
    try {
      const url = new URL(text);
      const tail = url.pathname.split("/").filter(Boolean).pop() ?? "";
      const words = decodeURIComponent(tail).replace(/[-_]+/g, " ").replace(/\.\w+$/, "").trim();
      const host = url.hostname.replace(/^www\./, "");
      return { title: words ? `${words.charAt(0).toUpperCase()}${words.slice(1)} · ${host}` : host, url: text };
    } catch {
      return { title: text };
    }
  }
  async function quickSave(text: string) {
    const result = await send("/opportunities", {
      ...phrase(text),
      category: quickCategory,
      ...(quickDeadline ? { deadline: quickDeadline } : {}),
    }, "", false);
    if (!result) return false;
    quickDeadline = "";
    showList(true);
  }
  function quickMore(text: string) {
    openPanel("new");
    draft = { ...draft, ...phrase(text), category: quickCategory, deadline: quickDeadline };
  }
  const detailDirty = $derived.by(() => {
    if (!detail) return false;
    const now: Record<string, unknown> = { ...detail, tags: words(tags) };
    return ["title", "url", "category", "description", "location", "remote", "notes", "status", "deadline", "timezone", "tags"]
      .some((key) => JSON.stringify(now[key] ?? null) !== JSON.stringify(savedDetail[key] ?? null));
  });
  // Q3: the edited item's save state is a dot on the workspace tab, not "Saved locally".
  $effect(() => {
    pane.setStatus(detail ? (detailDirty ? { state: "unsaved", detail: "Unsaved changes · Save review" } : { state: "saved" }) : null);
    return () => pane.setStatus(null);
  });
  let requestGeneration = 0;
  let detailGeneration = 0;
  let savedDetail = $state<Record<string, unknown>>({});
  function adopt(result: Opportunity) {
    detail = result;
    focused = result.id;
    tags = result.tags.join(", ");
    savedDetail = JSON.parse(JSON.stringify(result));
  }
  const categories = $derived(
    [...new Set(items.map((i) => i.category))].sort(),
  );
  const running = $derived(busy || task?.status === "running");
  function words(value: string) {
    return value
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
  }
  function failure(cause: unknown) {
    error =
      cause instanceof Error
        ? cause.message
        : "Unable to complete opportunity action.";
  }
  async function refresh() {
    const generation = ++requestGeneration;
    try {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries({
        search,
        source,
        category,
        status,
        location,
        remote,
        deadline,
        availability,
        favorites: favoritesOnly ? "true" : "",
      }))
        if (value) query.set(key, value);
      const data = await api<{
        items: Opportunity[];
        sources: Source[];
        statuses: string[];
        profile: Profile;
        schedule: Schedule;
      }>(`/opportunities?${query}`);
      if (generation !== requestGeneration) return;
      items = data.items;
      sources = data.sources;
      statuses = data.statuses;
      if (panel !== "monitor") {
        profile = data.profile;
        interests = data.profile.interests.join(", ");
        locations = data.profile.locations.join(", ");
        schedule = data.schedule;
      }
      selected = selected.filter((id) => items.some((i) => i.id === id));
    } catch (cause) {
      if (generation === requestGeneration) failure(cause);
    }
  }
  $effect(() => {
    void revision;
    void refresh();
  });
  async function open(id: number) {
    if (!pane.active && detail?.id !== id) return;
    const generation = ++detailGeneration;
    try {
      const result = await api<Opportunity>(`/opportunities/${id}`);
      if (generation !== detailGeneration || (!pane.active && detail?.id !== id)) return;
      adopt(result);
      panel = null;
      pendingDelete = false;
      editingItem = null;
    } catch (cause) {
      failure(cause);
    }
  }
  async function send(
    path: string,
    body: Record<string, unknown>,
    message = "",
    show = true,
  ) {
    busy = true;
    error = "";
    notice = "";
    try {
      const result = await api<Opportunity & { task?: TaskRecord }>(path, body);
      if (result.task) task = result.task;
      else if (show && result.id && result.source && (pane.active || detail?.id === result.id)) adopt(result);
      notice = message;
      await refresh();
      schemaRevision += 1;
      return result;
    } catch (cause) {
      failure(cause);
      return null;
    } finally {
      busy = false;
    }
  }
  async function saveSelected(favorite = true) {
    const ids = selected.length ? selected : focused ? [focused] : [];
    const selections = ids
      .map((id) => items.find((i) => i.id === id))
      .filter((i): i is Opportunity => !!i)
      .map((i) => ({ id: i.id, revision: i.revision }));
    if (!selections.length) return;
    const result = await send(
      "/opportunities/favorites",
      { selections, favorite },
      favorite
        ? ""
        : "",
    );
    if (result && detail && ids.includes(detail.id)) await open(detail.id);
  }
  function choose(item: Opportunity, event: MouseEvent) {
    if (event.ctrlKey || event.metaKey)
      selected = selected.includes(item.id)
        ? selected.filter((id) => id !== item.id)
        : [...selected, item.id];
    else if (event.shiftKey && focused) {
      const a = items.findIndex((i) => i.id === focused),
        b = items.findIndex((i) => i.id === item.id);
      selected = [
        ...new Set([
          ...selected,
          ...items.slice(Math.min(a, b), Math.max(a, b) + 1).map((i) => i.id),
        ]),
      ];
    } else {
      selected = [item.id];
    }
    void open(item.id);
  }
  async function deleteOpportunity(id: number) {
    if (!pane.active || busy) return;
    try {
      const item = await api<Opportunity>(`/opportunities/${id}`);
      if (!pane.active || !window.confirm(`Delete “${item.title}” and its project items?`)) return;
      const result = await send(`/opportunities/${id}/delete`, {revision: item.revision}, "Opportunity deleted.");
      if (result && detail?.id === id) { detail = null; detailGeneration += 1; }
    } catch (cause) { failure(cause); }
  }
  function keydown(event: KeyboardEvent) {
    if (
      !pane.active ||
      event.defaultPrevented ||
      event.isComposing ||
      (event.target as HTMLElement).closest(
        "input,textarea,select,[contenteditable=true]",
      )
    )
      return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
      event.preventDefault();
      event.stopPropagation();
      void saveSelected();
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const index = items.findIndex((i) => i.id === focused);
    if (event.key === "j" || event.key === "ArrowDown") {
      event.preventDefault();
      const item = items[Math.min(items.length - 1, index + 1)];
      if (item) {
        selected = [item.id];
        void open(item.id);
      }
    }
    if (event.key === "k" || event.key === "ArrowUp") {
      event.preventDefault();
      const item = items[Math.max(0, index - 1)];
      if (item) {
        selected = [item.id];
        void open(item.id);
      }
    }
    if (event.key === "r") void refresh();
    if (event.key === "Escape") detail = null;
  }
  async function saveDetail() {
    if (!detail) return;
    const fields: Record<string, unknown> = {
      title: detail.title,
      url: detail.url,
      category: detail.category,
      description: detail.description,
      location: detail.location,
      remote: detail.remote,
      notes: detail.notes,
      tags: words(tags),
      status: detail.status,
      deadline: detail.deadline || null,
      timezone: detail.timezone,
    };
    const changes = Object.fromEntries(
      Object.entries(fields).filter(
        ([key, value]) =>
          JSON.stringify(value) !== JSON.stringify(savedDetail[key]),
      ),
    );
    await send(`/opportunities/${detail.id}`, {
      revision: detail.revision,
      ...changes,
    });
  }
  async function add() {
    const result = await send("/opportunities", {
      ...draft,
      deadline: draft.deadline || null,
    });
    if (result) {
      panel = null;
      showList(true);
      draft = {
        title: "",
        url: "",
        category: "project",
        location: "",
        remote: null,
        description: "",
        deadline: "",
        timezone: "America/Chicago",
      };
    }
  }
  async function importItems() {
    try {
      const items = JSON.parse(importText);
      const result = await send(
        "/opportunities/import",
        { items },
        `Imported ${Array.isArray(items) ? items.length : 0}.`,
      );
      if (result) {
        importText = "";
        panel = null;
        showList(true);
      }
    } catch (cause) {
      failure(cause);
    }
  }
  async function saveConfig() {
    const result = await send(
      "/opportunities/settings",
      {
        revision: schedule.revision,
        profile: {
          ...profile,
          interests: words(interests),
          locations: words(locations),
        },
        enabled: schedule.enabled,
        interval_hours: Number(schedule.interval_hours),
      },
      "",
    );
    if (result) {
      panel = null;
      await refresh();
    }
  }
  async function discover() {
    await send(
      "/opportunities/discover",
      { confirmed: true },
      "Discovery started.",
    );
  }
  async function saveProjectItem() {
    if (!detail?.project) return;
    const result = await send(`/opportunities/${detail.id}/items`, {
      ...projectDraft,
      due: projectDraft.due || null,
      revision: detail.project.revision,
      ...(editingItem ? { id: editingItem } : {}),
    });
    if (result) {
      editingItem = null;
      projectDraft = { kind: "task", title: "", due: "", url: "", notes: "" };
    }
  }
  function editProjectItem(item: ProjectItem) {
    editingItem = item.id;
    projectDraft = {
      kind: item.kind,
      title: item.title,
      due: item.due || "",
      url: item.url,
      notes: item.notes,
    };
  }
  function aiSummary() {
    if (detail)
      summaryText = JSON.stringify({
        title: detail.title,
        description: detail.description,
        evidence: detail.evidence,
      });
  }
</script>

<svelte:window onkeydown={keydown} />
{#snippet field(label: string, control: Snippet, tip = "")}
  <label class="field" title={tip || undefined}><span class="field-label">{label}</span>{@render control()}</label>
{/snippet}

<section class="opportunities surface" class:with-list={listOpen} aria-label="Opportunities" use:fillWindow>
  <div class="map">
    <SectionGraph bare section="opportunities" revision={revision + schemaRevision} focus={schemaFocus} onselect={chooseNode}
      ondelete={(node) => { if (node.entity === "opportunity" && typeof node.ref === "number") return deleteOpportunity(node.ref); }} />
  </div>

  <div class="float-row" role="toolbar" aria-label="Opportunities controls">
    <h1>Opportunities</h1>
    {#if schemaFocus}<button type="button" class="pill ghost" onclick={rootSchema} title="Show every category">All categories</button>{/if}
    <div class="tools">
      <label class="search">
        <Icon name="jobs" size={14} />
        <input type="search" aria-label="Search opportunities" placeholder="Search" bind:value={search} oninput={searchChanged}
          onkeydown={(event) => { if (event.key === "Enter") applyFilters(); }} />
      </label>
      <Popover label="Filters" placement="bottom-end" class="opportunity-filters">
        {#snippet trigger(props)}
          <button type="button" class="pill" {...props}>Filters{#if filterCount}<span class="count">{filterCount}</span>{/if}</button>
        {/snippet}
        <div class="filter-grid">
          {#snippet sourceSelect()}<Select label="Source" kind="list" bind:value={source} onchange={applyFilters}
            options={[{ value: "", label: "All sources" }, { value: "manual", label: "Added by you" }, ...sources.map((s) => ({ value: s.id, label: s.name }))]} />{/snippet}
          {#snippet categorySelect()}<Select label="Category" kind="search" bind:value={category} onchange={applyFilters}
            options={[{ value: "", label: "All categories" }, ...categories.map((c) => ({ value: c, label: c }))]} />{/snippet}
          {#snippet statusSelect()}<Select label="Review status" kind="list" bind:value={status} onchange={applyFilters}
            options={[{ value: "", label: "Any status" }, ...statuses.map((s) => ({ value: s, label: statusLabel(s) }))]} />{/snippet}
          {#snippet remoteSelect()}<Select label="Remote" kind="segments" value={remote || "all"} onchange={(next) => { remote = next === "all" ? "" : next; applyFilters(); }}
            options={[{ value: "all", label: "Any" }, { value: "true", label: "Remote" }, { value: "false", label: "In person" }, { value: "unknown", label: "Unknown" }]} />{/snippet}
          {#snippet availabilitySelect()}<Select label="Dates" kind="list" bind:value={availability} onchange={applyFilters} options={AVAILABILITY} />{/snippet}
          {#snippet locationInput()}<input bind:value={location} placeholder="Any" onchange={applyFilters} />{/snippet}
          {#snippet deadlineInput()}<input type="date" bind:value={deadline} onchange={applyFilters} />{/snippet}
          {@render field("Source", sourceSelect)}
          {@render field("Category", categorySelect)}
          {@render field("Review status", statusSelect)}
          {@render field("Dates", availabilitySelect)}
          {@render field("Remote", remoteSelect)}
          {@render field("Location", locationInput)}
          {@render field("Deadline by", deadlineInput)}
          <label class="check"><input type="checkbox" bind:checked={favoritesOnly} onchange={applyFilters} />Favorites only</label>
        </div>
        {#if filterCount}<div class="filter-foot"><button type="button" class="text-button" onclick={clearFilters}>Clear filters</button></div>{/if}
      </Popover>
      <button type="button" class="pill" aria-pressed={listOpen} onclick={() => showList(!listOpen)} title="Show the list beside the map"
        ><Icon name="menu" size={14} />List<span class="count">{items.length}</span></button
      >
      <QuickAdd label="Add" placeholder="Title or link" bind:value={quick} onsave={quickSave} onmore={quickMore}>
        {#snippet chips()}
          <Select label="Category" kind="search" bind:value={quickCategory} oncreate={(name) => (quickCategory = name)}
            options={[...new Set(["project", ...categories, quickCategory])].map((c) => ({ value: c, label: c }))} />
          <input class="chip-date" type="date" aria-label="Deadline" title="Deadline" bind:value={quickDeadline} />
        {/snippet}
      </QuickAdd>
      <OverflowMenu items={[
        { label: "Interests & monitor", icon: "shield", onselect: () => openPanel("monitor") },
        { label: "Import JSON…", icon: "download", onselect: () => openPanel("import") },
        ...(demo ? [] : [{ label: running ? "Discovering…" : "Discover from public sources", icon: "refresh", disabled: running, onselect: () => void discover() }]),
      ] satisfies OverflowItem[]} />
    </div>
  </div>

  {#if error || notice || task}
    <div class="float-notes">
      {#if error}<p class="note error" role="alert">{error}<button type="button" class="icon-button" aria-label="Dismiss" onclick={() => (error = "")}><Icon name="close" size={12} /></button></p>{/if}
      {#if notice}<p class="note" role="status">{notice}<button type="button" class="icon-button" aria-label="Dismiss" onclick={() => (notice = "")}><Icon name="close" size={12} /></button></p>{/if}
      <TaskStatus compact {task} oncomplete={(result) => { task = result; void refresh(); if (detail) void open(detail.id); }} />
    </div>
  {/if}

  {#if listOpen}
    <aside class="list-drawer" aria-label="Opportunities list">
      <div class="list-head">
        <label class="check" title="Select every shown opportunity"
          ><input type="checkbox" aria-label="Select all shown" checked={items.length > 0 && selected.length === items.length}
            onchange={(event) => (selected = event.currentTarget.checked ? items.map((i) => i.id) : [])} /></label
        >
        {#if selected.length}
          <span class="muted">{selected.length} selected</span>
          <button type="button" class="pill" disabled={busy} onclick={() => void saveSelected()} title="Ctrl F">Favorite</button>
          <button type="button" class="pill ghost" disabled={busy} onclick={() => void saveSelected(false)}>Unfavorite</button>
        {:else}
          <span class="muted">{filterCount || search ? `${items.length} match` : `${items.length} opportunities`}</span>
        {/if}
        <button type="button" class="icon-button close" aria-label="Hide the list" title="Hide the list" onclick={() => showList(false)}><Icon name="close" size={13} /></button>
      </div>
      <ul>
        {#each items as item (item.id)}
          <li class:on={focused === item.id} class:marked={selected.includes(item.id)} style:--tone={tone(item.category)}>
            <input type="checkbox" aria-label={`Select ${item.title}`} checked={selected.includes(item.id)}
              onchange={(e) => (selected = e.currentTarget.checked ? [...new Set([...selected, item.id])] : selected.filter((id) => id !== item.id))} />
            <button type="button" class="row" onclick={(event) => choose(item, event)}
              oncontextmenu={(event) => { if (window.getSelection()?.toString()) return; event.preventDefault(); void deleteOpportunity(item.id); }}
              title={item.reasons.length ? `Match ${item.score}: ${item.reasons.join("; ")}` : undefined}>
              <span class="dot" aria-hidden="true"></span>
              <span class="row-main"><strong>{#if item.favorite}<span class="star" aria-label="Favorite">★</span>{/if}{item.title}</strong
                ><small>{item.category} · {item.location || (item.remote ? "Remote" : "Location unknown")} · {statusLabel(item.status)}</small></span>
              <span class="due" class:soon={soon(item.deadline)}>{item.deadline ? shortDate(item.deadline) : "—"}</span>
            </button>
          </li>
        {:else}
          <li class="empty">{filterCount || search ? "No opportunities match." : "Nothing yet. Add a project or import a list."}</li>
        {/each}
      </ul>
    </aside>
  {/if}
</section>

{#if detail}
  <EditDrawer title={detail.title} meta={`${detail.category} · ${statusLabel(detail.status)}${detail.deadline ? ` · due ${shortDate(detail.deadline)}` : ""}`}
    accent={tone(detail.category)} onclose={() => { detail = null; pendingDelete = false; }}>
    {#if detail.url || detail.start || detail.end}
      <p class="links">
        {#if detail.url}<a href={detail.url} target="_blank" rel="noreferrer">Open source <Icon name="external" size={11} /></a>{/if}
        {#if detail.start || detail.end}<span class="muted" title="Event dates, not application deadlines">Runs {detail.start || "?"} – {detail.end || "?"}</span>{/if}
      </p>
    {/if}
    <div class="fields">
      <label class="field wide"><span class="field-label">Title</span><input bind:value={detail.title} /></label>
      <label class="field wide"><span class="field-label">URL</span><input bind:value={detail.url} /></label>
      <label class="field"><span class="field-label">Category</span><input bind:value={detail.category} /></label>
      <label class="field"><span class="field-label">Location</span><input bind:value={detail.location} /></label>
      {#snippet detailStatus()}<Select label="Status" kind="list" bind:value={detail!.status} options={statuses.map((s) => ({ value: s, label: statusLabel(s) }))} />{/snippet}
      {#snippet detailRemote()}<Select label="Remote" kind="segments" value={remoteValue(detail!.remote)} onchange={(next) => (detail!.remote = remoteFrom(next))} options={REMOTE_CHOICES} />{/snippet}
      {@render field("Status", detailStatus)}
      {@render field("Remote", detailRemote)}
      <label class="field" title="A date, or an ISO time with offset"><span class="field-label">Deadline</span><input bind:value={detail.deadline} placeholder="2027-01-11" /></label>
      {#snippet detailZone()}<Select label="Time zone" kind="timezone" value={detail!.timezone} onchange={(next) => (detail!.timezone = next)} />{/snippet}
      {@render field("Time zone", detailZone)}
      <label class="field wide"><span class="field-label">Tags</span><input bind:value={tags} placeholder="Comma-separated" /></label>
      <label class="field wide"><span class="field-label">Notes</span><textarea bind:value={detail.notes}></textarea></label>
      <label class="field wide"><span class="field-label">Description</span><textarea bind:value={detail.description}></textarea></label>
    </div>

    <section class="drawer-section" aria-label="Project">
      <span class="section-head">Project</span>
      {#if detail.project}
        <label class="field"><span class="field-label">Project title</span><input bind:value={detail.project.title} /></label>
        <label class="field"><span class="field-label">Project notes</span><textarea bind:value={detail.project.notes}></textarea></label>
        <div class="row-actions">
          <button type="button" class="pill" disabled={busy}
            onclick={() => void send(`/opportunities/${detail!.id}/project-details`, { revision: detail!.project!.revision, title: detail!.project!.title, notes: detail!.project!.notes })}>Save project</button>
        </div>
        {#each detail.project.items as item (item.id)}
          <div class="project-item">
            <label class="check"><input type="checkbox" checked={!!item.done}
              onchange={(e) => void send(`/opportunities/${detail!.id}/items`, { revision: detail!.project!.revision, id: item.id, done: e.currentTarget.checked })} />
              <span><span class="kind">{item.kind}</span> {item.title}</span></label>
            <span class="due">{item.due ? shortDate(item.due) : ""}</span>
            {#if item.url}<a href={item.url} target="_blank" rel="noreferrer" aria-label={`Open resource for ${item.title}`}><Icon name="external" size={12} /></a>{/if}
            <OverflowMenu label={`Actions for ${item.title}`} items={[
              { label: "Edit", onselect: () => editProjectItem(item) },
              { label: "Delete", danger: true, disabled: busy, onselect: () => void send(`/opportunities/${detail!.id}/items`, { revision: detail!.project!.revision, id: item.id, delete: true }) },
            ]} />
            {#if item.notes}<p class="item-notes">{item.notes}</p>{/if}
          </div>
        {/each}
        <div class="item-form" aria-label={editingItem ? "Edit project item" : "Add project item"} role="group">
          {#snippet kindSelect()}<Select label="Kind" kind="segments" bind:value={projectDraft.kind}
            options={[{ value: "task", label: "Task" }, { value: "milestone", label: "Milestone" }, { value: "resource", label: "Resource" }]} />{/snippet}
          {@render field("Kind", kindSelect)}
          <label class="field"><span class="field-label">{editingItem ? "Edit item" : "New item"}</span><input bind:value={projectDraft.title} placeholder="Title" /></label>
          <div class="pair">
            <label class="field"><span class="field-label">Due</span><input bind:value={projectDraft.due} placeholder="2027-01-11" /></label>
            <label class="field"><span class="field-label">Resource URL</span><input bind:value={projectDraft.url} /></label>
          </div>
          <label class="field"><span class="field-label">Item notes</span><textarea bind:value={projectDraft.notes}></textarea></label>
          <div class="row-actions">
            {#if editingItem}<button type="button" class="pill ghost" onclick={() => { editingItem = null; projectDraft = { kind: "task", title: "", due: "", url: "", notes: "" }; }}>Cancel edit</button>{/if}
            <button type="button" class="pill" disabled={busy || !projectDraft.title.trim()} onclick={saveProjectItem}>{editingItem ? "Save item" : "Add item"}</button>
          </div>
        </div>
      {:else}
        <p class="muted">Track tasks, milestones and resources for it.</p>
        <div class="row-actions">
          <button type="button" class="pill" disabled={busy} onclick={() => void send(`/opportunities/${detail!.id}/project`, { revision: detail!.revision })}>Create project</button>
        </div>
      {/if}
    </section>

    {#if detail.evidence?.length}
      <section class="drawer-section" aria-label="Evidence">
        <span class="section-head">Evidence</span>
        <ul class="evidence">
          {#each detail.evidence as evidence}
            <li><a href={evidence.url} target="_blank" rel="noreferrer" title={JSON.stringify(evidence.facts, null, 2)}>{evidence.source}<Icon name="external" size={11} /></a><span class="muted">{evidence.observed_at.slice(0, 10)}</span></li>
          {/each}
        </ul>
      </section>
    {/if}

    {#if summaryText || aiResult}
      <section class="drawer-section" aria-label="AI summary">
        <span class="section-head">AI summary</span>
        {#if summaryText}
          <AIReview purpose="opportunity-summary" text={summaryText} {demo} active={pane.active} onresult={(text) => { aiResult = text; summaryText = ""; }} />
          <div class="row-actions"><button type="button" class="pill ghost" onclick={() => (summaryText = "")}>Cancel</button></div>
        {/if}
        {#if aiResult}
          <p class="muted">Verify against the evidence; nothing was applied.</p>
          <pre>{aiResult}</pre>
          <div class="row-actions"><button type="button" class="pill ghost" onclick={() => (aiResult = "")}>Dismiss</button></div>
        {/if}
      </section>
    {/if}

    {#if pendingDelete}
      <p class="confirm" role="alert">Delete it with its evidence and project? Exported events stay.</p>
    {/if}
    {#snippet footer()}
      {#if pendingDelete}
        <button type="button" class="pill ghost" onclick={() => (pendingDelete = false)}>Cancel</button>
        <button type="button" class="pill danger" disabled={busy}
          onclick={async () => {
            const result = await send(`/opportunities/${detail!.id}/delete`, { revision: detail!.revision }, "Opportunity deleted.");
            if (result) { selected = selected.filter((id) => id !== focused); detail = null; pendingDelete = false; }
          }}>Delete</button>
      {:else}
        <OverflowMenu placement="top-start" items={[
          { label: "Add deadline to calendar", icon: "calendar", disabled: busy || !detail!.deadline,
            onselect: () => void send(`/opportunities/${detail!.id}/calendar`, {}, "Deadline added to the calendar.") },
          { label: "Draft an AI summary", icon: "ai", disabled: demo || busy, onselect: aiSummary },
          { label: detail!.favorite ? "Unfavorite" : "Favorite", icon: "favorites", disabled: busy,
            onselect: () => { selected = [detail!.id]; void saveSelected(!detail!.favorite); } },
          { separator: true },
          { label: "Delete…", danger: true, onselect: () => (pendingDelete = true) },
        ] satisfies OverflowItem[]} />
        <span class="grow"></span>
        <button type="button" class="pill primary" disabled={busy || !detailDirty} onclick={saveDetail}
          title={`Your edits survive provider refresh${detail!.overrides.length ? ` · pinned: ${detail!.overrides.join(", ")}` : ""}`}>Save review</button>
      {/if}
    {/snippet}
  </EditDrawer>
{:else if panel === "new"}
  <EditDrawer title="New opportunity" onclose={() => (panel = null)}>
    <div class="fields">
      <label class="field wide"><span class="field-label">Title</span><input bind:value={draft.title} /></label>
      <label class="field wide"><span class="field-label">Source URL</span><input type="url" bind:value={draft.url} /></label>
      <label class="field"><span class="field-label">Category</span><input bind:value={draft.category} /></label>
      <label class="field"><span class="field-label">Location</span><input bind:value={draft.location} /></label>
      {#snippet draftRemote()}<Select label="Remote" kind="segments" value={remoteValue(draft.remote)} onchange={(next) => (draft.remote = remoteFrom(next))} options={REMOTE_CHOICES} />{/snippet}
      {@render field("Remote", draftRemote)}
      <label class="field" title="A date, or an ISO time with offset"><span class="field-label">Deadline</span><input bind:value={draft.deadline} placeholder="2027-01-11" /></label>
      {#snippet draftZone()}<Select label="Time zone" kind="timezone" bind:value={draft.timezone} />{/snippet}
      {@render field("Time zone", draftZone)}
      <label class="field wide"><span class="field-label">Description</span><textarea bind:value={draft.description}></textarea></label>
    </div>
    {#snippet footer()}
      <button type="button" class="pill ghost" onclick={() => (panel = null)}>Cancel</button>
      <button type="button" class="pill primary" disabled={busy || !draft.title.trim()} onclick={add}>Add</button>
    {/snippet}
  </EditDrawer>
{:else if panel === "import"}
  <EditDrawer title="Import opportunities" onclose={() => (panel = null)}>
    <label class="field" title="A JSON array of objects with a title and optional url, category, location, remote, description, deadline, timezone, notes, tags and status. Duplicate URLs are rejected.">
      <span class="field-label">JSON array</span>
      <textarea class="json" bind:value={importText} placeholder={'[{"title":"My project","category":"project"}]'}></textarea>
    </label>
    {#snippet footer()}
      <button type="button" class="pill ghost" onclick={() => (panel = null)}>Cancel</button>
      <button type="button" class="pill primary" disabled={busy || !importText.trim()} onclick={importItems}>Import</button>
    {/snippet}
  </EditDrawer>
{:else if panel === "monitor"}
  <EditDrawer title="Interests & monitor" meta={`Last run ${schedule.last_run || "never"} · next ${schedule.next_run || "not scheduled"}`} onclose={() => (panel = null)}>
    <div class="fields" title="Ranking uses literal profile matches and explains every score; it never decides eligibility.">
      <label class="field"><span class="field-label">Major</span><input bind:value={profile.major} /></label>
      <label class="field"><span class="field-label">Track</span><input bind:value={profile.track} /></label>
      <label class="field wide"><span class="field-label">Interests</span><input bind:value={interests} placeholder="quant, data science, hackathon" /></label>
      <label class="field wide"><span class="field-label">Locations</span><input bind:value={locations} placeholder="College Station, Texas" /></label>
      <label class="check wide"><input type="checkbox" bind:checked={profile.prefer_remote} />Prefer remote</label>
      <label class="check wide" title={demo ? "Monitoring is off in the demo" : "Checks free public sources while kitty! is open"}
        ><input type="checkbox" bind:checked={schedule.enabled} disabled={demo} />Monitor public sources</label>
      <label class="field"><span class="field-label">Every (hours)</span><input type="number" min="6" max="168" bind:value={schedule.interval_hours} /></label>
    </div>
    {#if schedule.last_error}<p class="note error">{schedule.last_error}</p>{/if}
    <section class="drawer-section" aria-label="Sources">
      <span class="section-head" title="Public pages only: no logins, private portals or paid search. URLs stay on the listed origin.">Sources</span>
      {#each sources as provider (provider.id)}
        <div class="source">
          <label class="check"><input type="checkbox" checked={!!provider.enabled}
            onchange={(e) => void send(`/opportunities/sources/${provider.id}`, { revision: provider.revision, enabled: e.currentTarget.checked })} />{provider.name}</label>
          <a href={provider.url} target="_blank" rel="noreferrer" aria-label={`Open ${provider.name}`}><Icon name="external" size={12} /></a>
          <span class="muted status" title={provider.last_error ?? undefined}>{provider.last_status} · {provider.last_count}</span>
          <div class="url-row">
            <input aria-label={`${provider.name} URL`} bind:value={provider.url} />
            <button type="button" class="pill ghost" disabled={busy}
              onclick={() => void send(`/opportunities/sources/${provider.id}`, { revision: provider.revision, url: provider.url })}>Save URL</button>
          </div>
        </div>
      {/each}
    </section>
    {#snippet footer()}
      <button type="button" class="pill ghost" onclick={() => (panel = null)}>Cancel</button>
      <button type="button" class="pill primary" disabled={busy} onclick={saveConfig}>Save</button>
    {/snippet}
  </EditDrawer>
{/if}

<style>
  /* Q21: one full-bleed surface; the map fills it and everything else floats over it. */
  .opportunities {
    position: relative;
    height: calc(100dvh - var(--workbench-top, 0px) - var(--dock-space, 0px) - 34px);
    min-height: 420px;
    overflow: hidden;
    border-radius: var(--r-panel);
    color: var(--ink);
  }
  .map {
    position: absolute;
    inset: 0;
  }
  /* The open list takes the right edge; the map recentres in what is left. */
  .with-list .map {
    right: min(340px, 100%);
  }
  .float-row {
    position: absolute;
    top: 12px;
    left: 16px;
    right: 12px;
    z-index: 3;
    display: flex;
    align-items: center;
    gap: 10px;
    pointer-events: none;
  }
  .float-row > * {
    pointer-events: auto;
  }
  .float-row h1 {
    margin: 0 auto 0 0;
    font-size: 22px;
    line-height: 1.2;
    pointer-events: none;
  }
  .tools {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-left: auto;
    padding: 4px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-control);
    /* No backdrop blur: the map under it redraws every frame, and a blur would re-raster with it. */
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 94%, transparent);
    box-shadow: var(--overlay-shadow);
  }
  .search {
    display: flex;
    align-items: center;
    gap: 6px;
    height: var(--control-h);
    padding: 0 10px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-control);
    background: color-mix(in srgb, var(--solid-inset, #111) 45%, transparent);
    color: var(--muted);
  }
  :global(:root body) .search input {
    width: 150px;
    height: 100%;
    padding: 0;
    border: 0;
    background: none;
    color: var(--ink);
    font: 400 var(--fs-control) var(--font-ui);
    outline: none;
  }
  .search:focus-within {
    border-color: var(--accent);
  }
  .pill.ghost {
    border-color: transparent;
    background: transparent;
  }
  .pill[aria-pressed="true"] {
    background: var(--select-fill, color-mix(in srgb, var(--ink) 12%, transparent));
    border-color: transparent;
    color: var(--select-ink, var(--ink));
  }
  .pill.danger {
    border-color: color-mix(in srgb, var(--danger) 60%, transparent);
    color: var(--danger);
  }
  .count {
    min-width: 18px;
    padding: 1px 5px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 12%, transparent);
    font: 500 10.5px/1.4 var(--font-mono);
    text-align: center;
  }
  :global(.kitty-popover.opportunity-filters) {
    width: min(420px, calc(100vw - 16px));
    padding: 12px;
  }
  .filter-grid,
  .fields {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 12px;
  }
  .filter-foot {
    display: flex;
    justify-content: flex-end;
    margin-top: 10px;
    padding-top: 8px;
    border-top: 1px solid var(--hairline);
  }
  .field {
    display: grid;
    gap: 4px;
    min-width: 0;
  }
  .field.wide,
  .check.wide {
    grid-column: 1 / -1;
  }
  .field-label {
    color: var(--muted);
    font: 600 var(--fs-label) / 1.3 var(--font-ui);
  }
  .field :global(.select-trigger) {
    width: 100%;
  }
  .field input,
  .field textarea {
    min-width: 0;
    width: 100%;
    box-sizing: border-box;
    padding: 0 9px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-field);
    color: var(--ink);
    font: 400 var(--fs-control) var(--font-ui);
  }
  .field input {
    height: var(--control-h);
  }
  .field textarea {
    min-height: 72px;
    padding: 7px 9px;
    resize: vertical;
  }
  .field textarea.json {
    min-height: 220px;
    font: 12px/1.5 var(--font-mono);
  }
  .check {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: var(--fs-control);
  }
  .check input {
    accent-color: var(--accent);
  }
  .chip-date {
    height: var(--control-h);
    padding: 0 8px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-control);
    color: var(--ink);
    font: 400 12px var(--font-ui);
  }
  .float-notes {
    position: absolute;
    top: 60px;
    left: 16px;
    z-index: 3;
    display: grid;
    gap: 6px;
    max-width: min(440px, calc(100% - 32px));
  }
  .note {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    padding: 6px 6px 6px 12px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-field);
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 90%, transparent);
    font-size: 12.5px;
    overflow-wrap: anywhere;
  }
  .note.error {
    border-color: color-mix(in srgb, var(--warning) 60%, transparent);
    color: var(--warning);
  }
  .note .icon-button {
    margin-left: auto;
  }
  /* The list: a drawer docked to the right edge under the control row. */
  .list-drawer {
    position: absolute;
    top: 60px;
    right: 0;
    bottom: 0;
    z-index: 2;
    display: flex;
    flex-direction: column;
    width: min(340px, 100%);
    border-top: 1px solid var(--hairline);
    border-left: 1px solid var(--hairline);
    border-top-left-radius: var(--r-panel);
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 94%, transparent);
  }
  .list-head {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 40px;
    padding: 4px 8px 4px 14px;
    border-bottom: 1px solid var(--hairline);
    font-size: 12px;
  }
  .list-head .close {
    margin-left: auto;
  }
  .muted {
    color: var(--muted);
  }
  .list-drawer ul {
    flex: 1;
    min-height: 0;
    margin: 0;
    padding: 4px 6px;
    list-style: none;
    overflow: auto;
  }
  .list-drawer li {
    display: flex;
    align-items: center;
    gap: 4px;
    padding-left: 8px;
    border-radius: 8px;
  }
  .list-drawer li:hover,
  .list-drawer li.on {
    background: var(--wash, color-mix(in srgb, var(--ink) 7%, transparent));
  }
  .list-drawer li input {
    accent-color: var(--accent);
    opacity: 0.55;
  }
  .list-drawer li:hover input,
  .list-drawer li.marked input {
    opacity: 1;
  }
  .list-drawer li.empty {
    padding: 24px 12px;
    color: var(--muted);
    font-size: 12.5px;
  }
  .row {
    display: flex;
    flex: 1;
    align-items: center;
    gap: 10px;
    min-width: 0;
    min-height: 42px;
    padding: 0 8px 0 4px;
    border: 0;
    background: none;
    color: var(--ink);
    text-align: left;
    cursor: pointer;
  }
  .dot {
    flex-shrink: 0;
    width: 6px;
    height: 6px;
    border-radius: 999px;
    background: var(--tone);
  }
  .row-main {
    display: grid;
    flex: 1;
    min-width: 0;
  }
  .row-main strong,
  .row-main small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .row-main strong {
    font: 500 12.5px/1.35 var(--font-ui);
  }
  .row-main small {
    color: var(--muted);
    font-size: 11.5px;
  }
  .star {
    margin-right: 4px;
    color: var(--warning);
  }
  .due {
    flex-shrink: 0;
    color: var(--muted);
    font: 10.5px var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .due.soon {
    color: var(--warning);
  }
  /* Inside the side drawer: hairline sections, never boxes. */
  .links {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
    margin: 0;
    font-size: 12.5px;
  }
  a {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--accent);
  }
  .drawer-section {
    display: grid;
    gap: 8px;
    margin-top: 6px;
    padding-top: 12px;
    border-top: 1px solid var(--hairline);
  }
  .drawer-section p {
    margin: 0;
    font-size: 12.5px;
  }
  .row-actions {
    display: flex;
    justify-content: flex-end;
    gap: 6px;
  }
  .project-item {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    min-height: 32px;
    font-size: 12.5px;
  }
  .project-item .check {
    flex: 1;
    min-width: 0;
  }
  .kind {
    color: var(--muted);
    font: 10.5px var(--font-mono);
    text-transform: uppercase;
  }
  .item-notes {
    flex-basis: 100%;
    margin: 0 0 4px 24px;
    color: var(--muted);
  }
  .item-form {
    display: grid;
    gap: 8px;
    padding-top: 8px;
    border-top: 1px dashed var(--hairline);
  }
  .pair {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  .evidence {
    display: grid;
    gap: 4px;
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: 12.5px;
  }
  .evidence li {
    display: flex;
    justify-content: space-between;
    gap: 8px;
  }
  .source {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 8px;
    padding: 6px 0;
    font-size: 12.5px;
  }
  .source + .source {
    border-top: 1px solid color-mix(in srgb, var(--hairline) 70%, transparent);
  }
  .source .check {
    flex: 1;
    min-width: 0;
  }
  .source .status {
    font: 10.5px var(--font-mono);
  }
  .url-row {
    display: flex;
    flex-basis: 100%;
    gap: 6px;
  }
  .url-row input {
    flex: 1;
    min-width: 0;
    height: var(--control-h);
    padding: 0 9px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-field);
    color: var(--ink);
    font: 400 12px var(--font-ui);
  }
  .confirm {
    margin: 0;
    color: var(--warning);
    font-size: 12.5px;
  }
  .grow {
    flex: 1;
  }
  pre {
    max-height: 280px;
    margin: 0;
    overflow: auto;
    font-size: 12px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  @media (max-width: 760px) {
    .float-row {
      flex-wrap: wrap;
    }
    .float-row h1 {
      display: none;
    }
    :global(:root body) .search input {
      width: 90px;
    }
    .list-drawer {
      top: 104px;
    }
  }
</style>
