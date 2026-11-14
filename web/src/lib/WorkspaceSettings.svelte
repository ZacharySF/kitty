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
  import type { ModuleId } from "./api";
  import AppearanceSettings from "./AppearanceSettings.svelte";
  import DesktopSettings from "./DesktopSettings.svelte";
  import QuietHours from "./QuietHours.svelte";
  import AccountSettings from "./AccountSettings.svelte";
  import ConnectionSettings from "./ConnectionSettings.svelte";
  import ConnectedApps from "./ConnectedApps.svelte";
  import PhoneSettings from "./PhoneSettings.svelte";
  import Keys from "./Keys.svelte";
  import ApplyAccounts from "./ApplyAccounts.svelte";
  import Devices from "./Devices.svelte";
  import TagSettings from "./TagSettings.svelte";
  import BuildSettings from "./BuildSettings.svelte";
  import { buildStore, setPreferences } from "./build.svelte";
  import "./settingsRows.css";
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
  import ModulePicker from "./ModulePicker.svelte";
  import { moduleStore } from "./modulePicker.svelte";
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
      { page: "account", label: "Account", icon: "shield", keywords: "google sign-in connect welcome guided tour onboarding identity microsoft outlook slack calendar mail reaction" },
      { page: "modules", label: "Modules", icon: "atlas", keywords: "applyai job search workspace study terminal brain atlas enable disable restart download pack" },
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
  // The module picker's switches, kept here so they survive a visit to another page.
  let moduleOverrides = $state<Partial<Record<ModuleId, boolean>>>({});
  const modulesDirty = $derived(
    Object.entries(moduleOverrides).some(([id, on]) => !!moduleStore.modules && moduleStore.modules.configured.includes(id as ModuleId) !== on),
  );
  export function hasUnsavedChanges() { return modulesDirty; }
  // Q3: an unsaved module selection is the tab's dot; every other setting saves at once.
  $effect(() => {
    pane.setStatus(modulesDirty ? { state: "unsaved", detail: "Unsaved module selection" } : null);
    return () => pane.setStatus(null);
  });
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

    <div class="page settings-rows" data-settings-page={shown}>
      {#if shown === "appearance"}
        <AppearanceSettings />
      {:else if shown === "desktop"}
        <DesktopSettings />
      {:else if shown === "quiet-hours"}
        <QuietHours />
      {:else if shown === "account"}
        <AccountSettings />
        <ConnectionSettings />
      {:else if shown === "rika-routing"}
        <RoutingMemory />
      {:else if shown === "rika-email"}
        <EmailSettings />
      {:else if shown === "rika-memory"}
        <MemorySettings />
      {:else if shown === "modules"}
        <Panel title={title} aria-label={title}>
          <ModulePicker bind:overrides={moduleOverrides} onsaved={onchange} />
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
        <PhoneSettings />
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
    font: 500 11px var(--font-ui);
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
