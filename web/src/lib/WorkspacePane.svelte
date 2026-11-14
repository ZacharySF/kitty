<script lang="ts">
  import { provideWorkspaceContext, type SaveStatus, type WorkspaceTrail } from "./workspaceContext.svelte";
  import type { Bootstrap } from "./api";
  import { missingPack, navigation, viewEnabled, type View } from "./workspace";
  import { activeModules } from "./modules.svelte";
  import Home from "./Home.svelte";
  import GraphNavigator from "./GraphNavigator.svelte";
  import PackMissing from "./PackMissing.svelte";

  import WorkspaceSettings from "./WorkspaceSettings.svelte";
  import AI from "./AI.svelte";
  import Loader from "./Loader.svelte";

  let { tabId, view, active, workspace, revision, epoch, initialJobId, notePage, onchange, onadd, onview, onopenjob, onguide, onnotepage, onfocusnotepage, onnotepagetab }: {
    tabId: string;
    view: View;
    active: boolean;
    workspace: Bootstrap | null;
    revision: number;
    epoch: number;
    initialJobId: number | null;
    /** The note page this tab shows; Notes opens it on mount. */
    notePage: number | null;
    onchange: () => Promise<void>;
    onadd: () => void;
    onview: (next: View) => void;
    onopenjob: (id: number) => void;
    onguide: () => void;
    onnotepage: (id: number | null, title: string) => void;
    onfocusnotepage: (id: number) => boolean;
    onnotepagetab: (id: number) => void;
  } = $props();
  const trail = $state<WorkspaceTrail>({ section: null, root: null, nodes: [] });
  let status = $state<SaveStatus | null>(null);
  provideWorkspaceContext({
    get id() { return tabId; },
    get active() { return active; },
    get trail() { return trail; },
    setTrail: (section, nodes, root) => {
      trail.section = section;
      trail.nodes = nodes;
      trail.root = root ?? null;
    },
    clearTrail: (section) => {
      if (section && trail.section !== section) return;
      trail.section = null;
      trail.root = null;
      trail.nodes = [];
    },
    get status() { return status; },
    setStatus: (next) => {
      status = next;
    },
    get notePage() { return notePage; },
    setNotePage: (id, title) => onnotepage(id, title),
    focusNotePage: (id) => onfocusnotepage(id),
    openNotePageTab: (id) => onnotepagetab(id),
  });

  let home: { focusSearch: () => void } | undefined = $state();
  let atlas: { focusSearch: () => void; focusGroup: (label: string) => void } | undefined = $state();
  let jobBrowser: { handleKey: (event: KeyboardEvent) => Promise<boolean> } | undefined = $state();
  let pipeline: { handleKey: (event: KeyboardEvent) => Promise<boolean> } | undefined = $state();
  let study: { handleKey: (event: KeyboardEvent) => Promise<boolean> } | undefined = $state();
  let math: { handleKey: (event: KeyboardEvent) => boolean } | undefined = $state();
  let profile: { hasUnsavedChanges: () => boolean } | undefined = $state();
  let documents: { hasUnsavedChanges: () => boolean } | undefined = $state();
  let settings: { hasUnsavedChanges: () => boolean } | undefined = $state();
  let notes: {
    hasUnsavedChanges: () => boolean;
    isCanvasFullscreen: () => boolean;
    newCanvasPage: () => Promise<void>;
  } | undefined = $state();
  let maps: { hasUnsavedChanges: () => boolean } | undefined = $state();

  export function breadcrumbs() { return trail; }
  export function saveStatus() { return status; }
  export function hasUnsavedChanges(): boolean {
    if (view === "profile") return profile?.hasUnsavedChanges() ?? false;
    if (view === "documents") return documents?.hasUnsavedChanges() ?? false;
    if (view === "settings") return settings?.hasUnsavedChanges() ?? false;
    if (view === "notes") return notes?.hasUnsavedChanges() ?? false;
    if (view === "schema") return maps?.hasUnsavedChanges() ?? false;
    return false;
  }
  /** Finds a navigation group on the Brain (the only view that maps them). */
  export function focusGroup(label: string) { if (active && view === "brain") atlas?.focusGroup(label); }
  /** "/": home asks Rika; the Brain opens Sections on its search. */
  export function focusSearch() {
    if (!active) return;
    if (view === "today") home?.focusSearch();
    else if (view === "brain") atlas?.focusSearch();
  }
  export async function createCanvasPage(): Promise<boolean> {
    if (!active || !notes?.isCanvasFullscreen()) return false;
    await notes.newCanvasPage();
    return true;
  }
  export async function handleKey(event: KeyboardEvent): Promise<boolean> {
    if (!active) return false;
    if (view === "pipeline") return await pipeline?.handleKey(event) ?? false;
    if (view === "study") return await study?.handleKey(event) ?? false;
    if (view === "math") return math?.handleKey(event) ?? false;
    if (["inbox", "jobs", "queue", "applications", "favorites"].includes(view))
      return await jobBrowser?.handleKey(event) ?? false;
    return false;
  }
</script>

{#snippet pending()}<Loader kind="view" label="rika! is waking up…" />{/snippet}
{#key `${view}:${epoch}:${initialJobId}`}
  {#if !viewEnabled(view, activeModules.enabled)}
    <!-- Disabled modules never mount or fetch; App redirects such tabs to the atlas. -->
  {:else if missingPack(view, activeModules.missing)}
    <!-- The module is on but its pack is not loaded: its routes answer 409; say how to get it. -->
    <PackMissing id={view} label={navigation.find(item => item.id === view)?.label ?? view} {onview} />
  {:else if view === "today"}
    <Home bind:this={home} {revision} counts={workspace?.counts} {onview} />
  {:else if view === "brain"}
    <GraphNavigator bind:this={atlas} {revision} {onview} />
  {:else if view === "goals"}
    {#await import("./goals/Goals.svelte")}{@render pending()}{:then { default: Goals }}
      <Goals {revision} {onview} {onopenjob} />
    {/await}
  {:else if view === "quick-notes"}
    {#await import("./QuickNotes.svelte")}{@render pending()}{:then { default: QuickNotes }}
      <QuickNotes />
    {/await}
  {:else if view === "learnharness"}
    {#await import("./LearnHarness.svelte")}{@render pending()}{:then { default: LearnHarness }}
      <LearnHarness />
    {/await}
  {:else if view === "herdr"}
    {#await import("./TerminalView.svelte")}{@render pending()}{:then { default: TerminalView }}
      <div class="herdr-view"><TerminalView label="herdr session" /></div>
    {/await}
  {:else if view === "tern"}
    {#await import("./TernView.svelte")}{@render pending()}{:then { default: TernView }}
      <TernView />
    {/await}
  {:else if view === "agent-skills"}
    {#await import("./AgentSkillsView.svelte")}{@render pending()}{:then { default: AgentSkillsView }}
      <AgentSkillsView />
    {/await}
  {:else if view === "settings"}
    <WorkspaceSettings bind:this={settings} {onview} {onchange} />
  {:else if view === "ai"}
    <AI {revision} demo={workspace?.demo ?? true} />
  {:else if view === "calendar"}
    {#await import("./Calendar.svelte")}{@render pending()}{:then { default: Calendar }}
      <Calendar {revision} {active} demo={workspace?.demo ?? true} />
    {/await}
  {:else if view === "notes"}
    {#await import("./Notes.svelte")}{@render pending()}{:then { default: Notes }}
      <Notes bind:this={notes} {revision} demo={workspace?.demo ?? true} />
    {/await}
  {:else if view === "schema"}
    {#await import("./SchemaMaps.svelte")}{@render pending()}{:then { default: SchemaMaps }}
      <SchemaMaps bind:this={maps} {revision} demo={workspace?.demo ?? true} />
    {/await}
  {:else if view === "opportunities"}
    {#await import("./Opportunities.svelte")}{@render pending()}{:then { default: Opportunities }}
      <Opportunities {revision} demo={workspace?.demo ?? true} />
    {/await}
  {:else if view === "study"}
    {#await import("./Study.svelte")}{@render pending()}{:then { default: Study }}
      <Study bind:this={study} {revision} demo={workspace?.demo ?? true} />
    {/await}
  {:else if view === "math"}
    {#await import("./math/MathView.svelte")}{@render pending()}{:then { default: MathView }}
      <MathView bind:this={math} demo={workspace?.demo ?? true} />
    {/await}
  {:else if view === "flashcards"}
    {#await import("./Flashcards.svelte")}{@render pending()}{:then { default: Flashcards }}
      <Flashcards {revision} demo={workspace?.demo ?? true} />
    {/await}
  {:else if view === "documents"}
    {#await import("./Documents.svelte")}{@render pending()}{:then { default: Documents }}
      <Documents bind:this={documents} />
    {/await}
  {:else if view === "inbox" || view === "jobs" || view === "queue" || view === "applications" || view === "favorites"}
    {#await import("./JobBrowser.svelte")}{@render pending()}{:then { default: JobBrowser }}
      <JobBrowser bind:this={jobBrowser} {view} {workspace} {revision} {initialJobId} onchange={onchange} {onadd} {onview} {onopenjob} {onguide} />
    {/await}
  {:else if view === "pipeline"}
    {#await import("./Pipeline.svelte")}{@render pending()}{:then { default: Pipeline }}
      <Pipeline bind:this={pipeline} {workspace} onchange={onchange} onmail={() => onview("mail")} />
    {/await}
  {:else if view === "reminders"}
    {#await import("./Reminders.svelte")}{@render pending()}{:then { default: Reminders }}
      <Reminders {onopenjob} onchange={onchange} />
    {/await}
  {:else if view === "resumes"}
    {#await import("./Library.svelte")}{@render pending()}{:then { default: Library }}
      <Library demo={workspace?.demo ?? true} {onopenjob} />
    {/await}
  {:else if view === "sources"}
    {#await import("./Sources.svelte")}{@render pending()}{:then { default: Sources }}
      <Sources demo={workspace?.demo ?? true} />
    {/await}
  {:else if view === "profile"}
    {#await import("./Profile.svelte")}{@render pending()}{:then { default: Profile }}
      <Profile bind:this={profile} demo={workspace?.demo ?? true} onresume={() => onview("resumes")} />
    {/await}
  {:else if view === "finder" || view === "reports" || view === "mail" || view === "triage"}
    {#await import("./Insights.svelte")}{@render pending()}{:then { default: Insights }}
      <Insights section={view} demo={workspace?.demo ?? true} />
    {/await}
  {:else if view === "questions"}
    {#await import("./Questions.svelte")}{@render pending()}{:then { default: Questions }}
      <Questions demo={workspace?.demo ?? true} />
    {/await}
  {/if}
{/key}
