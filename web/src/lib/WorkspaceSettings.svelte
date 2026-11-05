<script lang="ts" module>
  /** The short pages Settings opens beside its index, deep-linked as `#settings/<page>`. */
  export const SETTINGS_PAGES = [
    "appearance", "desktop", "quiet-hours", "build", "account", "modules", "graphs", "tags", "connected-apps", "agents", "keys", "devices",
    "apply-accounts", "rika-routing", "rika-email", "rika-memory",
  ] as const;
  export type SettingsPage = (typeof SETTINGS_PAGES)[number];
  const isPage = (value: unknown): value is SettingsPage => SETTINGS_PAGES.includes(value as SettingsPage);
  /** The page a hash names: `undefined` when the hash is another view, `null` for bare `#settings`. */
  function pageIn(hash: string): SettingsPage | null | undefined {
    const [view, slug] = hash.slice(1).split("/");
    if (view !== "settings") return undefined;
    return isPage(slug) ? slug : null;
  }
</script>

<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api, type ModuleId, type ModulesState } from "./api";
  import AppearanceSettings from "./AppearanceSettings.svelte";
  import DesktopSettings from "./DesktopSettings.svelte";
  import QuietHours from "./QuietHours.svelte";
  import AccountSettings from "./AccountSettings.svelte";
  import ConnectedApps from "./ConnectedApps.svelte";
  import Keys from "./Keys.svelte";
  import ApplyAccounts from "./ApplyAccounts.svelte";
  import Devices from "./Devices.svelte";
  import TagSettings from "./TagSettings.svelte";
  import BuildSettings from "./BuildSettings.svelte";
  import { buildStore, setPreferences } from "./build.svelte";
  import RoutingMemory from "./rika/settings/RoutingMemory.svelte";
  import EmailSettings from "./rika/settings/EmailSettings.svelte";
  import MemorySettings from "./rika/settings/MemorySettings.svelte";
  import AgentSettings from "./agents/AgentSettings.svelte";
  import Icon from "./Icon.svelte";
  import Select from "./ui/Select.svelte";
  import { DEFAULT_PHYSICS, PHYSICS_LIMITS, type GraphPhysics, type GraphLayout } from "./graph3d";
  import { GRAPH_LAYOUTS, setGraphSettings } from "./graphPreferences";
  import type { View } from "./workspace";
  import { activeModules } from "./modules.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  let { onview, onchange }: { onview: (view: View) => void; onchange: () => Promise<void> } = $props();
  const pane = useWorkspaceContext();
  const uid = $props.id();

  /**
   * Q23: a left index of short pages. Each page is one surface; the index filters by
   * name and a few keywords; Careers entries open their own views.
   */
  type Entry = { label: string; icon: string; keywords: string } & ({ page: SettingsPage } | { view: "profile" | "ai" });
  const quietAvailable = $derived(activeModules.enabled.includes("workspace") || activeModules.enabled.includes("applyai"));
  const groups = $derived<{ label: string; items: Entry[] }[]>([
    { label: "General", items: [
      { page: "appearance", label: "Appearance", icon: "kitty", keywords: "theme preset cozy focused minimal title font density motion background petals motif home brain look 2d 3d" },
      { page: "desktop", label: "Desktop", icon: "terminal", keywords: "app updates tray keep running close quit quick note window" },
      ...(quietAvailable ? [{ page: "quiet-hours", label: "Quiet hours", icon: "moon", keywords: "notifications reminders do not disturb digest calendar dial held" } as Entry] : []),
      { page: "build", label: "Build", icon: "download", keywords: "build file export import share install rollback history custom css tokens safe mode editor" },
    ] },
    { label: "Workspace", items: [
      { page: "account", label: "Account", icon: "shield", keywords: "google sign-in connect welcome guided tour onboarding identity" },
      { page: "modules", label: "Modules", icon: "atlas", keywords: "applyai job search workspace study terminal enable disable restart" },
      { page: "graphs", label: "Graphs", icon: "schema", keywords: "schemas default layout tree clusters sphere force physics repulsion spring damping" },
      ...(activeModules.enabled.includes("workspace") ? [{ page: "tags", label: "Tags", icon: "favorites", keywords: "auto tags keywords suggest always attach" } as Entry] : []),
    ] },
    { label: "Connections", items: [
      { page: "connected-apps", label: "Connected apps", icon: "external", keywords: "pairing tern plugin browser extension mirror token revoke approve" },
      { page: "agents", label: "Agents", icon: "terminal", keywords: "agent mediation landlock confine sandbox guarantee session journal access policy simulate apply retention hold omp claude codex herdr" },
      { page: "keys", label: "Keys", icon: "shield", keywords: "api keys openrouter anthropic usajobs brave keyring secret" },
      { page: "devices", label: "Devices", icon: "refresh", keywords: "sync peer pair code tailscale lan other computer" },
    ] },
    { label: "Careers & AI", items: [
      ...(activeModules.enabled.includes("applyai") ? [{ view: "profile", label: "Profile & saved answers", icon: "questions", keywords: "resume answers questions personal details" } as Entry] : []),
      { view: "ai", label: "AI & privacy", icon: "ai", keywords: "openrouter key provider budget privacy model" },
    ] },
    { label: "Apply", items: [
      ...(activeModules.enabled.includes("applyai") ? [{ page: "apply-accounts", label: "Accounts", icon: "shield", keywords: "apply employer accounts grants workday oracle eightfold icims sign in create reset password keyring" } as Entry] : []),
    ] },
    { label: "Rika", items: [
      { page: "rika-routing", label: "Routing memory", icon: "reports", keywords: "rika tokens cost cache tier route tool path shortcut forget learned cheaper requests" },
      { page: "rika-email", label: "Email", icon: "mail", keywords: "rika gmail mail auto-send send reply replies drafts undo hold inbox scheduling confirmation thanks" },
      { page: "rika-memory", label: "Memory", icon: "notes", keywords: "rika index search preferences remember learned knows notes sharing privacy" },
    ] },
  ]);
  let query = $state("");
  const filtered = $derived.by(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return groups
      .map((group) => ({ ...group, items: group.items.filter((item) => words.every((word) => `${item.label} ${item.keywords}`.toLowerCase().includes(word))) }))
      .filter((group) => group.items.length);
  });
  const pages = $derived(groups.flatMap((group) => group.items.flatMap((item) => ("page" in item ? [item.page] : []))));
  let page = $state<SettingsPage>(untrack(() => (pane.active && pageIn(location.hash)) || "appearance"));
  const shown = $derived(pages.includes(page) ? page : "appearance");
  const title = $derived(groups.flatMap((group) => group.items).find((item) => "page" in item && item.page === shown)?.label ?? "Appearance");

  /** Opens a page and records it, so Back returns to the previous one. */
  function choose(next: SettingsPage) {
    if (next !== page || pageIn(location.hash) !== next)
      history.pushState({ ...history.state, settingsPage: next }, "", `#settings/${next}`);
    page = next;
  }
  function pick(item: Entry) {
    if ("page" in item) choose(item.page);
    else onview(item.view);
  }
  /** A bare `#settings` keeps the page shown unless that history entry remembers one. */
  function follow() {
    if (!pane.active) return;
    const named = pageIn(location.hash);
    if (named === undefined) return;
    const remembered = history.state?.settingsPage;
    page = named ?? (isPage(remembered) ? remembered : page);
  }
  onMount(() => {
    if (pane.active && pageIn(location.hash) === null) history.replaceState({ ...history.state, settingsPage: page }, "");
    window.addEventListener("popstate", follow);
    window.addEventListener("hashchange", follow);
    return () => {
      window.removeEventListener("popstate", follow);
      window.removeEventListener("hashchange", follow);
    };
  });

  // The build's `ui.graph`; a slider being dragged shows its draft until it is saved.
  const layout = $derived(buildStore.build.ui.graph.layout);
  let draft = $state<GraphPhysics | null>(null);
  const physics = $derived(draft ?? buildStore.build.ui.graph.physics);
  let physicsTimer: ReturnType<typeof setTimeout> | undefined;
  let error = $state("");
  let modules = $state<ModulesState | null>(null);
  let selection = $state<ModuleId[]>([]);
  let savingModules = $state(false);
  let moduleError = $state("");
  const modulesDirty = $derived(
    !!modules && (selection.length !== modules.configured.length || selection.some(id => !modules!.configured.includes(id))),
  );
  const changes = $derived.by(() => {
    if (!modules) return { start: [] as string[], stop: [] as string[] };
    const label = (id: ModuleId) => modules!.available.find(item => item.id === id)?.label ?? id;
    return {
      start: modules.configured.filter(id => !modules!.enabled.includes(id)).map(label),
      stop: modules.enabled.filter(id => !modules!.configured.includes(id)).map(label),
    };
  });
  export function hasUnsavedChanges() { return modulesDirty; }
  // Q3: an unsaved module selection is the tab's dot; every other setting saves at once.
  $effect(() => {
    pane.setStatus(modulesDirty ? { state: "unsaved", detail: "Unsaved module selection" } : null);
    return () => pane.setStatus(null);
  });
  function adopt(next: ModulesState) {
    modules = next;
    selection = [...next.configured];
  }
  // The build's modules can change from anywhere (an install, a rollback, an outside editor):
  // show the saved selection again unless the user is editing it here.
  const buildModules = $derived(buildStore.build.modules.join(","));
  $effect(() => {
    void buildModules;
    if (untrack(() => modulesDirty || savingModules)) return;
    let cancelled = false;
    api<ModulesState>("/modules")
      .then(next => { if (!cancelled) adopt(next); })
      .catch(cause => { if (!cancelled) moduleError = (cause as Error).message; });
    return () => { cancelled = true; };
  });
  function toggleModule(id: ModuleId, on: boolean) {
    if (!modules) return;
    selection = modules.available.map(item => item.id).filter(item => (item === id ? on : selection.includes(item)));
  }
  async function saveModules() {
    if (!modules || savingModules) return;
    savingModules = true;
    moduleError = "";
    try {
      adopt(await api<ModulesState>("/modules", { enabled: selection }));
      await onchange();
    } catch (cause) {
      moduleError = `Could not save modules: ${(cause as Error).message}`;
    } finally {
      savingModules = false;
    }
  }
  const labels: Record<keyof GraphPhysics, string> = { repulsion: "Repulsion", spring: "Spring", damping: "Damping" };
  const explanations: Record<keyof GraphPhysics, string> = {
    repulsion: "How strongly nodes push apart. Increase to give crowded branches more room.",
    spring: "How strongly connected nodes pull together. Lower values leave looser branches.",
    damping: "How quickly node movement slows. Higher values settle the graph sooner.",
  };
  // Graph settings save to the build at once; a refusal is shown here (and by the build's notice).
  async function saveGraph(save: Promise<boolean>) {
    error = (await save) ? "" : buildStore.error;
  }
  function movePhysics(key: keyof GraphPhysics, value: number) {
    draft = { ...physics, [key]: value };
    clearTimeout(physicsTimer);
    physicsTimer = setTimeout(() => {
      const next = draft;
      draft = null;
      if (next) void saveGraph(setGraphSettings(layout, next));
    }, 250);
  }
  function reset() {
    clearTimeout(physicsTimer);
    draft = null;
    void saveGraph(setPreferences({ ui: { graph: { look: "2d", layout: "tree", physics: { ...DEFAULT_PHYSICS } } } }));
  }
</script>

<section class="settings-workspace" aria-label="Workspace settings">
  <div class="split">
    <div class="index">
      <h1>Settings</h1>
      <label class="find">
        <Icon name="jobs" size={13} />
        <input type="search" bind:value={query} placeholder="Find a setting" aria-label="Find a setting"
          onkeydown={(event) => { const first = filtered[0]?.items[0]; if (event.key === "Enter" && first) { event.preventDefault(); pick(first); } }} />
      </label>
      <nav aria-label="Settings pages">
        {#each filtered as group (group.label)}
          <h2 class="group">{group.label}</h2>
          <ul>
            {#each group.items as item (item.label)}
              <li>
                {#if "page" in item}
                  <a class="item" href={`#settings/${item.page}`} aria-current={item.page === shown ? "page" : undefined}
                    data-tutorial={item.page === "modules" ? "modules" : undefined}
                    onclick={(event) => { if (event.ctrlKey || event.metaKey || event.shiftKey || event.button) return; event.preventDefault(); choose(item.page); }}
                    ><Icon name={item.icon} size={15} />{item.label}</a>
                {:else}
                  <button type="button" class="item" onclick={() => pick(item)}><Icon name={item.icon} size={15} />{item.label}</button>
                {/if}
              </li>
            {/each}
          </ul>
        {:else}
          <p class="empty">No setting matches “{query.trim()}”.</p>
        {/each}
      </nav>
    </div>

    <div class="page" data-settings-page={shown}>
      {#if shown === "appearance"}
        <AppearanceSettings />
      {:else if shown === "desktop"}
        <DesktopSettings />
      {:else if shown === "quiet-hours"}
        <QuietHours />
      {:else if shown === "account"}
        <AccountSettings />
      {:else if shown === "rika-routing"}
        <RoutingMemory />
      {:else if shown === "rika-email"}
        <EmailSettings />
      {:else if shown === "rika-memory"}
        <MemorySettings />
      {:else if shown === "modules"}
        <Panel title={title} aria-label={title}>
          {#if modules}
            <fieldset class="rows" disabled={savingModules}>
              <legend class="sr-only">Modules</legend>
              {#each modules.available as item (item.id)}
                {@const on = selection.includes(item.id)}
                {@const configured = modules.configured.includes(item.id)}
                <div class="set-row">
                  <button type="button" class="set-switch" role="switch" id={`${uid}-module-${item.id}`} aria-checked={on} aria-label={item.label}
                    aria-describedby={`${uid}-module-${item.id}-about`} onclick={() => toggleModule(item.id, !on)}><i></i></button>
                  <label class="set-label" for={`${uid}-module-${item.id}`}>{item.label}</label>
                  <span class="set-desc one-line" id={`${uid}-module-${item.id}-about`} title={item.description}>{item.description}</span>
                  {#if configured !== modules.enabled.includes(item.id)}
                    <span class="set-end pending" title={configured ? "Starts after rika! restarts" : "Stops after rika! restarts"}>Restart</span>
                  {/if}
                </div>
              {/each}
            </fieldset>
            {#if modules.error}<p class="set-note" role="alert">Saved selection unreadable ({modules.error}). Saving replaces it.</p>{/if}
            {#if modules.restart_required}
              <p class="set-note restart" role="status">Restart rika! to apply{#if changes.start.length} · on: {changes.start.join(", ")}{/if}{#if changes.stop.length} · off: {changes.stop.join(", ")}{/if}.</p>
            {/if}
            <div class="set-row foot">
              {#if modulesDirty}<span class="set-desc">Applies after restart; your data is kept.</span>{/if}
              <span class="set-end">
                <Button size="sm" variant="ghost" disabled={!modulesDirty || savingModules} onclick={() => { if (modules) selection = [...modules.configured]; }}>Discard</Button>
                <Button intent="primary" size="sm" disabled={!modulesDirty || savingModules} onclick={saveModules}>{savingModules ? "Saving…" : "Save modules"}</Button>
              </span>
            </div>
          {:else if !moduleError}<p class="set-note" role="status">Loading modules…</p>{/if}
          {#if moduleError}<p class="set-note" role="alert">{moduleError}</p>{/if}
        </Panel>
      {:else if shown === "graphs"}
        <Panel title={title} aria-label={title}>
          <div class="rows">
            <div class="set-row">
              <span class="set-label" title="The layout used when you open Home or a section schema">Default layout</span>
              <span class="set-end">
                <Select label="Default layout" value={layout} options={GRAPH_LAYOUTS.map((item) => ({ value: item.id, label: item.label }))}
                  onchange={(next) => void saveGraph(setGraphSettings(next as GraphLayout, physics))} />
              </span>
            </div>
            {#each Object.keys(PHYSICS_LIMITS) as (keyof GraphPhysics)[] as key (key)}
              <div class="set-row slider">
                <span class="set-label">
                  <label for={`${uid}-${key}`}>{labels[key]}</label><i class="info" title={explanations[key]} aria-hidden="true">i</i>
                </span>
                <input type="range" id={`${uid}-${key}`} aria-describedby={`${uid}-${key}-help`} min={PHYSICS_LIMITS[key].min} max={PHYSICS_LIMITS[key].max}
                  step={PHYSICS_LIMITS[key].step} value={physics[key]} oninput={(event) => movePhysics(key, Number(event.currentTarget.value))} />
                <output for={`${uid}-${key}`}>{physics[key].toFixed(key === "repulsion" ? 0 : 2)}</output>
                <span class="sr-only" id={`${uid}-${key}-help`}>{explanations[key]}</span>
              </div>
            {/each}
            <div class="set-row foot"><span class="set-end"><Button size="sm" variant="ghost" onclick={reset}>Reset to defaults</Button></span></div>
          </div>
          {#if error}<p class="set-note" role="alert">{error}</p>{/if}
        </Panel>
      {:else if shown === "build"}
        <BuildSettings />
      {:else if shown === "tags"}
        <TagSettings />
      {:else if shown === "connected-apps"}
        <ConnectedApps />
      {:else if shown === "agents"}
        <AgentSettings />
      {:else if shown === "keys"}
        <Keys />
      {:else if shown === "devices"}
        <Devices />
      {:else if shown === "apply-accounts"}
        <ApplyAccounts />
      {/if}
    </div>
  </div>
</section>

<style>
  .settings-workspace {
    container-type: inline-size;
  }
  .split {
    display: grid;
    grid-template-columns: 220px minmax(0, 760px);
    justify-content: center;
    align-items: start;
    gap: 22px;
  }
  .index {
    position: sticky;
    top: calc(var(--workbench-top, 0px) + 14px);
    display: grid;
    gap: 8px;
  }
  h1 {
    margin: 0 0 6px;
  }
  .find {
    display: flex;
    align-items: center;
    gap: 7px;
    height: var(--control-h);
    padding: 0 10px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-field);
    background: var(--select-fill, color-mix(in srgb, var(--solid-inset) 45%, transparent));
    color: var(--muted);
  }
  .find:focus-within {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  :global(:root body) .find input[type="search"] {
    flex: 1;
    min-width: 0;
    height: 100%;
    padding: 0;
    border: 0;
    outline: 0;
    background: transparent;
    color: var(--ink);
    font: var(--fs-control) var(--font-ui);
  }
  nav {
    display: grid;
  }
  .group {
    margin: 10px 0 4px;
    padding: 0 10px;
    color: var(--muted);
    font: 600 var(--fs-label) / 1.3 var(--font-ui);
  }
  ul {
    display: grid;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .item {
    display: flex;
    align-items: center;
    gap: 9px;
    width: 100%;
    height: var(--control-h);
    padding: 0 10px;
    border: 0;
    border-radius: var(--r-control);
    background: transparent;
    color: var(--muted);
    font: 500 var(--fs-control) var(--font-ui);
    text-align: left;
    text-decoration: none;
    cursor: pointer;
  }
  .item:hover {
    color: var(--ink);
  }
  .item:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  .item[aria-current="page"] {
    background: var(--wash);
    box-shadow: inset 2px 0 var(--accent);
    color: var(--ink);
  }
  .empty {
    margin: 8px 10px;
    color: var(--muted);
    font-size: var(--fs-control);
  }
  /* One page, one surface: rows inside are split by full-width hairlines. */
  .page :global(.rows) {
    display: grid;
    min-width: 0;
    margin: 0;
    padding: 0;
    border: 0;
  }
  .page :global(.set-row) {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    min-height: 44px;
    margin: 0 calc(-1 * var(--br-panel-p));
    padding: 6px var(--br-panel-p);
    border-top: 1px solid var(--hairline);
    font-size: 13px;
  }
  .page :global(.rows > .set-row:first-of-type) {
    border-top: 0;
  }
  .page :global(.set-label) {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    flex-shrink: 0;
    min-width: 130px;
    color: var(--ink);
    font-weight: 500;
  }
  .page :global(.set-desc) {
    min-width: 0;
    color: var(--muted);
    line-height: 1.45;
  }
  /* Module rows stay one line; the whole description is in the tooltip. */
  .set-desc.one-line {
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .page :global(.set-end) {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    margin-left: auto;
  }
  .page :global(.set-note) {
    margin: 8px 0 0;
    color: var(--muted);
    font-size: 12px;
  }
  .page :global(.set-switch) {
    position: relative;
    flex-shrink: 0;
    width: 32px;
    height: 18px;
    padding: 0;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    cursor: pointer;
    transition: background-color 160ms;
  }
  .page :global(.set-switch i) {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 12px;
    height: 12px;
    border-radius: 999px;
    background: var(--muted);
    transition: transform 160ms, background-color 160ms;
  }
  .page :global(.set-switch[aria-checked="true"]) {
    background: color-mix(in srgb, var(--accent) 45%, transparent);
  }
  .page :global(.set-switch[aria-checked="true"] i) {
    transform: translateX(14px);
    background: var(--ink);
  }
  .page :global(.set-switch:focus-visible) {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .page :global(.set-switch:disabled) {
    opacity: 0.55;
    cursor: default;
  }
  .set-row .set-label[for] {
    cursor: pointer;
  }
  .pending {
    color: var(--warning);
    font-size: 12px;
    cursor: help;
  }
  .restart {
    color: var(--ink);
  }
  .restart::before {
    content: "";
    display: inline-block;
    width: 6px;
    height: 6px;
    margin-right: 7px;
    border-radius: 999px;
    background: var(--warning);
    vertical-align: 1px;
  }
  .set-row.slider {
    display: grid;
    grid-template-columns: 130px minmax(0, 1fr) 48px;
  }
  .set-row.slider label {
    cursor: pointer;
  }
  .info {
    display: inline-grid;
    place-items: center;
    width: 14px;
    height: 14px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--muted);
    font: 600 9px/1 var(--font-ui);
    font-style: normal;
    cursor: help;
  }
  output {
    color: var(--muted);
    font: 500 11px var(--font-mono);
    font-variant-numeric: tabular-nums;
    text-align: right;
  }
  /* A bare track: the global field box would draw a box around each slider. */
  :global(:root body) .set-row input[type="range"] {
    width: 100%;
    height: auto;
    margin: 0;
    padding: 0;
    border: 0;
    background: transparent;
    box-shadow: none;
    accent-color: var(--accent);
  }
  .foot {
    justify-content: flex-end;
  }
  @container (max-width: 640px) {
    .split {
      grid-template-columns: minmax(0, 1fr);
    }
    .index {
      position: static;
    }
    .set-row.slider {
      grid-template-columns: 100px minmax(0, 1fr) 40px;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .page :global(.set-switch),
    .page :global(.set-switch i) {
      transition: none;
    }
  }
</style>
