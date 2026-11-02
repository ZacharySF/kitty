<script lang="ts">
  import { onMount } from "svelte";
  import Icon from "./Icon.svelte";
  import { api, type Job } from "./api";
  import { navigationGroups, visibleNavigation, type View } from "./workspace";
  import { buildStore } from "./build.svelte";

  /**
   * Spotlight-style palette (Ctrl K): one large field, nothing else until you type,
   * then up to six results with the top hit emphasised. Esc or a click outside closes.
   */
  let {
    enabled,
    onclose,
    onview,
    onopenjob,
    onadd,
    onhelp,
    onnewcard,
    onreload,
  }: {
    enabled: readonly string[];
    onclose: () => void;
    onview: (view: View) => void;
    onopenjob: (id: number) => void;
    onadd: () => void;
    onhelp: () => void;
    /** Opens the quick-add flashcard popover; absent when Flashcards is off. */
    onnewcard?: () => void;
    onreload: () => void;
  } = $props();

  const LIMIT = 6;
  let dialog: HTMLDialogElement;
  let searchField = $state<HTMLInputElement>();
  let query = $state("");
  let jobs = $state<Job[]>([]);
  let selected = $state(0);
  let error = $state("");
  const applyai = $derived(enabled.includes("applyai"));
  const typed = $derived(query.trim().length > 0);

  type Choice = { label: string; meta: string; icon: string; run: () => void };
  // Q19: Ctrl K opens the one mind-map editor on a map, or on a new one.
  const mapsOn = $derived(visibleNavigation(enabled).some((item) => item.id === "schema"));
  let mapList = $state<{ id: number; title: string }[]>([]);
  /** Switching view always mounts the editor afresh; it picks the request up from the session. */
  function openMap(id?: number) {
    try { sessionStorage.setItem(id ? "kitty.schema.open-map" : "kitty.schema.new-map", id ? String(id) : "1"); } catch { /* Opens the last map. */ }
    onview("schema");
  }
  const actions = $derived<Choice[]>([
    ...(applyai ? [{ label: "Add job", meta: "Action", icon: "jobs", run: () => onadd() }] : []),
    ...(onnewcard ? [{ label: "New card", meta: "Flashcards", icon: "flashcards", run: () => onnewcard() }] : []),
    ...(mapsOn ? [{ label: "New mind map", meta: "Action", icon: "schema", run: () => openMap() }] : []),
    { label: "Keyboard shortcuts", meta: "Action", icon: "questions", run: () => onhelp() },
    { label: "Reload configuration", meta: "Action", icon: "refresh", run: () => onreload() },
    { label: "Edit build", meta: "Action", icon: "settings", run: () => { buildStore.editorOpen = true; } },
  ]);
  /** Prefix matches rank above substring matches, so "cal" puts Calendar first. */
  const choices = $derived.by<Choice[]>(() => {
    const text = query.trim().toLowerCase();
    if (!text) return [];
    const local: Choice[] = [
      ...visibleNavigation(enabled).map((item) => ({
        label: item.label,
        meta: navigationGroups.find((group) => group.views.includes(item.id))?.label || "Section",
        icon: item.icon,
        run: () => onview(item.id),
      })),
      ...actions,
      ...mapList.map((map) => ({ label: map.title, meta: "Mind map", icon: "schema", run: () => openMap(map.id) })),
    ];
    const rank = (label: string) => {
      const name = label.toLowerCase();
      return name.startsWith(text) ? 0 : name.split(/\s+/).some((word) => word.startsWith(text)) ? 1 : name.includes(text) ? 2 : -1;
    };
    const matched = local
      .map((choice) => ({ choice, score: rank(choice.label) }))
      .filter(({ score }) => score >= 0)
      .sort((a, b) => a.score - b.score)
      .map(({ choice }) => choice);
    const found = jobs.map((job) => ({
      label: `${job.company} · ${job.title}`,
      meta: `Job #${job.id}`,
      icon: "jobs",
      run: () => onopenjob(job.id),
    }));
    return [...matched, ...found].slice(0, LIMIT);
  });

  onMount(() => {
    dialog.showModal();
    searchField?.focus();
    if (mapsOn) void api<{ maps: { id: number; title: string }[] }>("/schema").then((result) => { mapList = result.maps; }).catch(() => { /* Maps stay reachable through Mind maps. */ });
  });
  $effect(() => {
    const text = query.trim();
    selected = 0;
    error = "";
    let cancelled = false;
    const timer = setTimeout(() => {
      if (!text || !applyai) {
        jobs = [];
        return;
      }
      void api<{ jobs: Job[] }>(`/jobs?search=${encodeURIComponent(text)}&limit=${LIMIT}&view=jobs&closed=1`)
        .then((result) => {
          if (!cancelled) jobs = result.jobs;
        })
        .catch((cause) => {
          if (!cancelled) error = cause.message;
        });
    }, 160);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  });
  function run(index: number) {
    const choice = choices[index];
    if (!choice) return;
    onclose();
    choice.run();
  }
  function key(event: KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      selected = Math.max(0, Math.min(choices.length - 1, selected + (event.key === "ArrowDown" ? 1 : -1)));
    } else if (event.key === "Enter") {
      event.preventDefault();
      run(selected);
    }
  }
</script>

<dialog
  bind:this={dialog}
  class="spotlight"
  class:open={typed}
  aria-label="Search kitty!"
  oncancel={onclose}
  onkeydown={key}
  onclick={(event) => {
    if (event.target === dialog) onclose();
  }}
>
  <div class="field">
    <Icon name="jobs" size={20} />
    <input
      bind:this={searchField}
      bind:value={query}
      class="query"
      type="text"
      role="combobox"
      aria-expanded={typed && choices.length > 0}
      aria-controls="spotlight-results"
      aria-activedescendant={choices.length ? `spotlight-${selected}` : undefined}
      aria-label={applyai ? "Search sections, commands and jobs" : "Search sections and commands"}
      placeholder="kitty! search"
      autocomplete="off"
      spellcheck="false"
    />
  </div>
  {#if typed}
    <div class="results" id="spotlight-results" role="listbox" aria-label="Results">
      {#each choices as choice, index (`${choice.meta}:${choice.label}`)}
        <!-- svelte-ignore a11y_click_events_have_key_events (keys are handled on the dialog) -->
        <div
          id={`spotlight-${index}`}
          role="option"
          tabindex="-1"
          aria-selected={selected === index}
          data-top={index === 0}
          onpointermove={() => (selected = index)}
          onclick={() => run(index)}
        >
          <span class="icon"><Icon name={choice.icon} size={index === 0 ? 18 : 15} /></span>
          <span class="label">{choice.label}</span>
          <span class="meta">{index === 0 ? "Top hit" : choice.meta}</span>
        </div>
      {:else}
        <p class="none">No results</p>
      {/each}
      {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
    </div>
  {/if}
</dialog>

<style>
  .spotlight {
    width: min(560px, calc(100vw - 32px));
    margin: 18vh auto auto;
    padding: 0;
    overflow: hidden;
    border: 1px solid color-mix(in srgb, var(--ink) 10%, transparent);
    border-radius: 18px;
    color: var(--ink);
    background: color-mix(in srgb, var(--solid-panel, var(--br-panel)) 80%, transparent);
    backdrop-filter: blur(36px) saturate(170%);
    box-shadow: var(--overlay-shadow);
  }
  .spotlight::backdrop {
    background: color-mix(in srgb, var(--shadow-tint) 28%, transparent);
  }
  .field {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 18px;
    color: var(--muted);
  }
  .spotlight.open .field {
    border-bottom: 1px solid color-mix(in srgb, var(--ink) 8%, transparent);
  }
  :global(:root body) .field input.query {
    flex: 1;
    min-width: 0;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
    outline: none;
    color: var(--ink);
    font: 400 22px var(--font-ui);
    letter-spacing: -0.01em;
  }
  .field input::placeholder {
    color: var(--subtle);
  }
  .results {
    display: grid;
    padding: 6px;
  }
  [role="option"] {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 12px;
    border-radius: 10px;
    font-size: 13.5px;
    cursor: pointer;
  }
  [role="option"][data-top="true"] {
    padding: 10px 12px;
    font-size: 15px;
    font-weight: 500;
  }
  [role="option"][aria-selected="true"] {
    background: color-mix(in srgb, var(--accent) 26%, transparent);
  }
  .icon {
    display: inline-grid;
    place-items: center;
    color: var(--violet);
  }
  .label {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .meta {
    margin-left: auto;
    color: var(--subtle);
    font-size: 11.5px;
    white-space: nowrap;
  }
  .none {
    margin: 10px 14px;
    color: var(--muted);
  }
</style>
