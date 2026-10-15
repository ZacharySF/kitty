<script lang="ts">
  import { api, type TaskRecord } from "./api";
  import TaskStatus from "./TaskStatus.svelte";
  import AIReview from "./AIReview.svelte";
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
    task = $state<TaskRecord | null>(null),
    configOpen = $state(false),
    addOpen = $state(false);
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
      if (!configOpen) {
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
    const generation = ++detailGeneration;
    try {
      const result = await api<Opportunity>(`/opportunities/${id}`);
      if (generation !== detailGeneration) return;
      adopt(result);
      pendingDelete = false;
      editingItem = null;
    } catch (cause) {
      failure(cause);
    }
  }
  async function send(
    path: string,
    body: Record<string, unknown>,
    message = "Saved locally.",
  ) {
    busy = true;
    error = "";
    notice = "";
    try {
      const result = await api<Opportunity & { task?: TaskRecord }>(path, body);
      if (result.task) task = result.task;
      else if (result.id && result.source) adopt(result);
      notice = message;
      await refresh();
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
        ? "Selection saved to favorites."
        : "Selection removed from favorites.",
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
  function contextSave(item: Opportunity, event: MouseEvent) {
    event.preventDefault();
    if (!selected.includes(item.id)) selected = [item.id];
    focused = item.id;
    void saveSelected();
  }
  function keydown(event: KeyboardEvent) {
    if (
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
    if (event.key === "f") void saveSelected();
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
      addOpen = false;
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
        "Import stored locally.",
      );
      if (result) importText = "";
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
      "Interest profile and opt-in monitoring saved.",
    );
    if (result) {
      configOpen = false;
      await refresh();
    }
  }
  async function discover() {
    await send(
      "/opportunities/discover",
      { confirmed: true },
      "Free public discovery started; source failures remain visible.",
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
<section class="opportunities">
  <header>
    <div>
      <h1>Opportunities & projects</h1>
      <p>
        Competitions, hackathons, campus events and your own projects. Separate
        from jobs.
      </p>
    </div>
    <div class="actions">
      <button
        onclick={() => {
          configOpen = !configOpen;
        }}>Interests & monitor</button
      ><button onclick={() => (addOpen = !addOpen)}>Add / import</button><button
        disabled={demo || running}
        onclick={discover}>Discover free public sources</button
      >
    </div>
  </header>
  {#if demo}<p class="notice">
      Demo: local review, favorites, projects and calendar work; external
      discovery and scheduled monitoring are disabled.
    </p>{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if notice}<p class="notice" role="status">{notice}</p>{/if}
  <TaskStatus
    {task}
    oncomplete={(result) => {
      task = result;
      void refresh();
      if (detail) void open(detail.id);
    }}
  />
  {#if configOpen}
    <section class="panel">
      <h2>Interest profile & public monitor</h2>
      <p>
        Ranking uses literal profile matches and explains every score. It does
        not determine eligibility or guess missing dates.
      </p>
      <div class="fields">
        <label>Major<input bind:value={profile.major} /></label><label
          >Track<input bind:value={profile.track} /></label
        ><label
          >Interests (comma-separated)<input
            bind:value={interests}
            placeholder="quant, data science, hardware, hackathon"
          /></label
        ><label
          >Preferred locations (comma-separated)<input
            bind:value={locations}
            placeholder="College Station, Texas"
          /></label
        ><label
          ><input type="checkbox" bind:checked={profile.prefer_remote} /> Prefer remote</label
        ><label
          ><input
            type="checkbox"
            bind:checked={schedule.enabled}
            disabled={demo}
          /> Enable scheduled free monitoring while kitty! is open</label
        ><label
          >Interval (hours)<input
            type="number"
            min="6"
            max="168"
            bind:value={schedule.interval_hours}
          /></label
        >
      </div>
      <p>
        Last run: {schedule.last_run || "never"} · Next run: {schedule.next_run ||
          "not scheduled"}. Missed due runs resume next launch.
      </p>
      {#if schedule.last_error}<p class="notice">{schedule.last_error}</p>{/if}
      <button disabled={busy} onclick={saveConfig}
        >Save profile & schedule</button
      >
      <h3>Primary source citations & health</h3>
      <p>
        No login bypass, private portal access or paid search. Unknown dates are
        not proof of open registration. Edit public provider paths to use an
        authoritative feed; URLs must stay on the listed origin.
      </p>
      {#each sources as provider (provider.id)}<div class="source">
          <label
            ><input
              type="checkbox"
              checked={!!provider.enabled}
              onchange={(e) =>
                void send(`/opportunities/sources/${provider.id}`, {
                  revision: provider.revision,
                  enabled: e.currentTarget.checked,
                })}
            />
            {provider.name}</label
          ><a href={provider.url} target="_blank" rel="noreferrer"
            >Primary source</a
          ><label>Public URL<input bind:value={provider.url} /></label><button
            disabled={busy}
            onclick={() =>
              void send(`/opportunities/sources/${provider.id}`, {
                revision: provider.revision,
                url: provider.url,
              })}>Save URL</button
          ><span
            >{provider.last_status} · {provider.last_count} records · {provider.last_run ||
              "not fetched"}</span
          >{#if provider.last_error}<p class="notice">
              {provider.last_error}
            </p>{/if}
        </div>{/each}
    </section>
  {/if}
  {#if addOpen}<section class="panel">
      <h2>Manual opportunity / project</h2>
      <div class="fields">
        <label>Title<input bind:value={draft.title} /></label><label
          >Source URL<input type="url" bind:value={draft.url} /></label
        ><label>Category<input bind:value={draft.category} /></label><label
          >Location<input bind:value={draft.location} /></label
        ><label
          >Remote<select bind:value={draft.remote}
            ><option value={null}>Unknown</option><option value={true}
              >Yes</option
            ><option value={false}>No</option></select
          ></label
        ><label
          >Deadline (date or aware ISO8601)<input
            bind:value={draft.deadline}
            placeholder="2027-01-11 or 2027-01-11T17:00:00-06:00"
          /></label
        ><label>IANA timezone<input bind:value={draft.timezone} /></label><label
          >Description<textarea bind:value={draft.description}
          ></textarea></label
        >
      </div>
      <button disabled={busy || !draft.title.trim()} onclick={add}
        >Add locally</button
      >
      <h3>Import JSON</h3>
      <p>
        A JSON array of objects with title and optional url, category, location,
        remote, description, deadline, timezone, notes, tags and status.
        Duplicate canonical URLs are rejected.
      </p>
      <textarea
        aria-label="Opportunity import JSON"
        bind:value={importText}
        placeholder={'[{"title":"My project","category":"project"}]'}
      ></textarea><button
        disabled={busy || !importText.trim()}
        onclick={importItems}>Import locally</button
      >
    </section>{/if}
  <form
    class="filters"
    onsubmit={(e) => {
      e.preventDefault();
      void refresh();
    }}
  >
    <label
      >Search<input
        bind:value={search}
        placeholder="Title, description, notes, tags"
      /></label
    ><label
      >Source<select bind:value={source}
        ><option value="">All sources</option><option value="manual"
          >Manual</option
        >{#each sources as s}<option value={s.id}>{s.name}</option
          >{/each}</select
      ></label
    ><label
      >Category<input
        bind:value={category}
        list="opportunity-categories"
        placeholder="All categories"
      /><datalist id="opportunity-categories"
        >{#each categories as c}<option value={c}></option>{/each}</datalist
      ></label
    ><label
      >Review status<select bind:value={status}
        ><option value="">All statuses</option>{#each statuses as s}<option
            value={s}>{s}</option
          >{/each}</select
      ></label
    ><label>Location<input bind:value={location} /></label><label
      >Remote<select bind:value={remote}
        ><option value="">All</option><option value="true">Remote</option
        ><option value="false">In-person</option><option value="unknown"
          >Unknown</option
        ></select
      ></label
    ><label>Deadline on/before<input type="date" bind:value={deadline} /></label
    ><label
      >Dates / availability<select bind:value={availability}
        ><option value="">All</option
        >{#each ["future", "active", "closed", "unknown"] as a}<option value={a}
            >{a}</option
          >{/each}</select
      ></label
    ><label
      ><input type="checkbox" bind:checked={favoritesOnly} /> Favorites</label
    ><button type="submit">Apply filters</button>
  </form>
  <div class="actions">
    <span>{items.length} opportunities · {selected.length} selected</span
    ><button onclick={() => (selected = items.map((i) => i.id))}
      >Select all shown</button
    ><button onclick={() => (selected = [])}>Clear selection</button><button
      disabled={busy || !selected.length}
      onclick={() => void saveSelected()}>Save selected (Ctrl F)</button
    ><button
      disabled={busy || !selected.length}
      onclick={() => void saveSelected(false)}>Unfavorite selected</button
    >
  </div>
  <p class="hint">
    Ctrl-click toggles selection, Shift-click selects a range. Right-click saves
    selected rows. j/k or arrows review; f favorites; r reloads. Dates reflect
    cited evidence; registration eligibility must be checked with the organizer.
  </p>
  <div class="workspace">
    <div class="list">
      {#each items as item (item.id)}<article
          class:selected={selected.includes(item.id)}
        >
          <div class="row">
            <input
              type="checkbox"
              aria-label={`Select ${item.title}`}
              checked={selected.includes(item.id)}
              onchange={(e) =>
                (selected = e.currentTarget.checked
                  ? [...new Set([...selected, item.id])]
                  : selected.filter((id) => id !== item.id))}
            /><button
              class="row-title"
              onclick={(event) => choose(item, event)}
              oncontextmenu={(event) => contextSave(item, event)}
              ><strong>{item.favorite ? "★ " : ""}{item.title}</strong><span
                >{item.source} · {item.category} · {item.status} · {item.availability}</span
              ></button
            >
          </div>
          <p>
            {item.location || "Location unknown"} · {item.remote === null
              ? "Remote unknown"
              : item.remote
                ? "Remote"
                : "In-person"} · Deadline: {item.deadline || "unknown"}
          </p>
          <p>
            Match score {item.score}: {item.reasons.length
              ? item.reasons.join("; ")
              : "No explicit profile matches."}
          </p>
        </article>{:else}<p class="panel">
          No opportunities match. Add a local project or explicitly discover
          public sources.
        </p>{/each}
    </div>
    {#if detail}<aside class="panel detail">
        <div class="actions">
          <h2>{detail.title}</h2>
          <button onclick={() => (detail = null)}>Close</button>
        </div>
        {#if detail.url}<a href={detail.url} target="_blank" rel="noreferrer"
            >Open source URL</a
          >{:else}<p>No source URL supplied.</p>{/if}
        <p>{detail.description}</p>
        <p>
          Event start: {detail.start || "unknown"} · End: {detail.end ||
            "unknown"}. These are not application deadlines.
        </p>
        <div class="fields">
          <label>Title<input bind:value={detail.title} /></label><label
            >URL<input bind:value={detail.url} /></label
          ><label>Category<input bind:value={detail.category} /></label><label
            >Location<input bind:value={detail.location} /></label
          ><label
            >Remote<select bind:value={detail.remote}
              ><option value={null}>Unknown</option><option value={true}
                >Yes</option
              ><option value={false}>No</option></select
            ></label
          ><label
            >Status<select bind:value={detail.status}
              >{#each statuses as s}<option value={s}>{s}</option
                >{/each}</select
            ></label
          ><label
            >Deadline override<input
              bind:value={detail.deadline}
              placeholder="Date or aware ISO8601"
            /></label
          ><label>IANA timezone<input bind:value={detail.timezone} /></label
          ><label>Tags<input bind:value={tags} /></label><label
            >Notes<textarea bind:value={detail.notes}></textarea></label
          >
        </div>
        <p>
          Saving protects edited facts from provider refresh. Manual overrides: {detail.overrides.join(
            ", ",
          ) || "none"}.
        </p>
        <label
          >Description override<textarea bind:value={detail.description}
          ></textarea></label
        >
        <div class="actions">
          <button disabled={busy} onclick={saveDetail}>Save review</button
          ><button
            disabled={busy || !detail.deadline}
            onclick={() =>
              void send(
                `/opportunities/${detail!.id}/calendar`,
                {},
                "Deadline added to local calendar; repeated exports do not overwrite calendar edits.",
              )}>Export saved deadline to calendar</button
          ><button disabled={demo || busy} onclick={aiSummary}
            >Review AI summary prompt</button
          ><button onclick={() => (pendingDelete = true)}
            >Delete opportunity</button
          >
        </div>
        {#if pendingDelete}<p class="notice">
            Delete this opportunity, its evidence and local project items?
            Calendar events already exported are retained.
          </p>
          <button
            onclick={async () => {
              const result = await send(
                `/opportunities/${detail!.id}/delete`,
                { revision: detail!.revision },
                "Opportunity deleted.",
              );
              if (result) {
                detail = null;
                selected = selected.filter((id) => id !== focused);
              }
            }}>Confirm delete</button
          ><button onclick={() => (pendingDelete = false)}>Cancel</button>{/if}
        <h3>Factual source evidence</h3>
        {#each detail.evidence || [] as evidence}<details>
            <summary
              >{evidence.source} · observed {evidence.observed_at}</summary
            ><a href={evidence.url} target="_blank" rel="noreferrer"
              >Evidence citation</a
            >
            <pre>{JSON.stringify(evidence.facts, null, 2)}</pre>
          </details>{:else}<p>
            Manually supplied record; no provider evidence claimed.
          </p>{/each}
        <h3>Project</h3>
        {#if detail.project}<label
            >Project title<input bind:value={detail.project.title} /></label
          ><label
            >Project notes<textarea bind:value={detail.project.notes}
            ></textarea></label
          ><button
            disabled={busy}
            onclick={() =>
              void send(`/opportunities/${detail!.id}/project-details`, {
                revision: detail!.project!.revision,
                title: detail!.project!.title,
                notes: detail!.project!.notes,
              })}>Save project</button
          >
          {#each detail.project.items as item (item.id)}<div
              class="project-item"
            >
              <label
                ><input
                  type="checkbox"
                  checked={!!item.done}
                  onchange={(e) =>
                    void send(`/opportunities/${detail!.id}/items`, {
                      revision: detail!.project!.revision,
                      id: item.id,
                      done: e.currentTarget.checked,
                    })}
                />
                {item.kind}: {item.title}</label
              ><span>{item.due || "No due date"}</span>{#if item.url}<a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer">Resource</a
                >{/if}{#if item.notes}<p>{item.notes}</p>{/if}<button
                onclick={() => editProjectItem(item)}>Edit</button
              ><button
                disabled={busy}
                onclick={() =>
                  void send(`/opportunities/${detail!.id}/items`, {
                    revision: detail!.project!.revision,
                    id: item.id,
                    delete: true,
                  })}>Delete item</button
              >
            </div>{/each}
          <h4>{editingItem ? "Edit" : "Add"} task / milestone / resource</h4>
          <div class="fields">
            <label
              >Kind<select bind:value={projectDraft.kind}
                >{#each ["task", "milestone", "resource"] as kind}<option
                    value={kind}>{kind}</option
                  >{/each}</select
              ></label
            ><label>Title<input bind:value={projectDraft.title} /></label><label
              >Due (date or aware ISO)<input
                bind:value={projectDraft.due}
              /></label
            ><label>Resource URL<input bind:value={projectDraft.url} /></label
            ><label
              >Notes<textarea bind:value={projectDraft.notes}></textarea></label
            >
          </div>
          <button
            disabled={busy || !projectDraft.title.trim()}
            onclick={saveProjectItem}>Save project item</button
          >{#if editingItem}<button
              onclick={() => {
                editingItem = null;
                projectDraft = {
                  kind: "task",
                  title: "",
                  due: "",
                  url: "",
                  notes: "",
                };
              }}>Cancel edit</button
            >{/if}
        {:else}<p>
            Turn this opportunity into a local project with actionable tasks,
            milestones and resources.
          </p>
          <button
            disabled={busy}
            onclick={() =>
              void send(`/opportunities/${detail!.id}/project`, {
                revision: detail!.revision,
              })}>Create project</button
          >{/if}
      </aside>{/if}
  </div>
  {#if summaryText}<section class="panel">
      <h2>Opportunity summary request</h2>
      <AIReview
        purpose="opportunity-summary"
        text={summaryText}
        {demo}
        onresult={(text) => {
          aiResult = text;
          summaryText = "";
        }}
      /><button onclick={() => (summaryText = "")}>Close summary request</button
      >
    </section>{/if}
  {#if aiResult}<section class="panel">
      <h2>AI suggestion — verify against evidence</h2>
      <pre>{aiResult}</pre>
      <p>Not applied to notes, eligibility, dates or project records.</p>
    </section>{/if}
</section>

<style>
  .opportunities {
    color: var(--ink);
    padding: 1.2rem;
    min-width: 0;
  }
  header,
  .actions,
  .row {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    flex-wrap: wrap;
  }
  header {
    justify-content: space-between;
  }
  h1,
  h2,
  h3,
  h4 {
    margin: 0.6rem 0;
  }
  p {
    line-height: 1.55;
    overflow-wrap: anywhere;
  }
  header p,
  .hint,
  .row-title span {
    color: var(--muted);
  }
  button,
  input,
  select,
  textarea {
    font: inherit;
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem;
  }
  button {
    cursor: pointer;
  }
  button:hover {
    border-color: var(--accent);
  }
  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  input[type="checkbox"] {
    width: auto;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    font-size: 0.88rem;
  }
  label:has(input[type="checkbox"]) {
    flex-direction: row;
    align-items: center;
  }
  .fields,
  .filters {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 0.8rem;
    margin: 0.8rem 0;
  }
  .filters {
    padding: 1rem;
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 8px;
  }
  .panel {
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 9px;
    padding: 1rem;
    margin: 1rem 0;
  }
  .source,
  .project-item {
    padding: 0.8rem 0;
    border-bottom: 1px solid var(--border);
    display: flex;
    gap: 0.65rem;
    flex-wrap: wrap;
    align-items: center;
  }
  .source label:has(input:not([type="checkbox"])) {
    flex: 1;
    min-width: 240px;
  }
  .source p,
  .project-item p {
    width: 100%;
    margin: 0.25rem 0;
  }
  .workspace {
    display: grid;
    grid-template-columns: minmax(280px, 1fr) minmax(320px, 1.1fr);
    gap: 1rem;
  }
  .workspace:not(:has(aside)) {
    grid-template-columns: 1fr;
  }
  .list article {
    border: 1px solid var(--border);
    border-radius: 8px;
    margin: 0.65rem 0;
    padding: 0.8rem;
    background: var(--surface);
  }
  article.selected {
    border-color: var(--accent);
    background: var(--surface-raised);
  }
  .row-title {
    border: none;
    text-align: left;
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    background: transparent;
  }
  .list p {
    font-size: 0.85rem;
    margin: 0.45rem 0;
  }
  .detail {
    min-width: 0;
    margin-top: 0.65rem;
  }
  textarea {
    min-height: 95px;
    width: 100%;
    box-sizing: border-box;
  }
  a {
    color: var(--accent);
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 350px;
    overflow: auto;
    font-size: 0.8rem;
  }
  .error,
  .notice {
    border: 1px solid var(--warning);
    padding: 0.8rem;
    border-radius: 6px;
  }
  .error {
    color: var(--warning);
  }
  details {
    margin: 0.7rem 0;
  }
  summary {
    cursor: pointer;
  }
  @media (max-width: 1000px) {
    .workspace {
      grid-template-columns: 1fr;
    }
    .detail {
      order: -1;
    }
  }
</style>
