<script lang="ts">
  import { provideWorkspaceContext, type WorkspaceTrail } from "./workspaceContext.svelte";
  import type { Bootstrap } from "./api";
  import { viewEnabled, type View } from "./workspace";
  import { activeModules } from "./modules.svelte";
  import GraphNavigator from "./GraphNavigator.svelte";
  import WorkspaceSettings from "./WorkspaceSettings.svelte";
  import AI from "./AI.svelte";

  let { tabId, view, active, workspace, revision, epoch, initialJobId, onchange, onadd, onview, onopenjob, onguide }: {
    tabId: string;
    view: View;
    active: boolean;
    workspace: Bootstrap | null;
    revision: number;
    epoch: number;
    initialJobId: number | null;
    onchange: () => Promise<void>;
    onadd: () => void;
    onview: (next: View) => void;
    onopenjob: (id: number) => void;
    onguide: () => void;
  } = $props();
  const trail = $state<WorkspaceTrail>({ section: null, root: null, nodes: [] });
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
  });

  let atlas: { focusSearch: () => void; focusGroup: (label: string) => void } | undefined = $state();
  let jobBrowser: { handleKey: (event: KeyboardEvent) => Promise<boolean> } | undefined = $state();
  let pipeline: { handleKey: (event: KeyboardEvent) => Promise<boolean> } | undefined = $state();
  let study: { handleKey: (event: KeyboardEvent) => Promise<boolean> } | undefined = $state();
  let profile: { hasUnsavedChanges: () => boolean } | undefined = $state();
  let documents: { hasUnsavedChanges: () => boolean } | undefined = $state();
  let settings: { hasUnsavedChanges: () => boolean } | undefined = $state();
  let notes: {
    hasUnsavedChanges: () => boolean;
    isCanvasFullscreen: () => boolean;
    newCanvasTab: () => Promise<void>;
  } | undefined = $state();

  export function breadcrumbs() { return trail; }
  export function hasUnsavedChanges(): boolean {
    if (view === "profile") return profile?.hasUnsavedChanges() ?? false;
    if (view === "documents") return documents?.hasUnsavedChanges() ?? false;
    if (view === "settings") return settings?.hasUnsavedChanges() ?? false;
    if (view === "notes" || view === "schema") return notes?.hasUnsavedChanges() ?? false;
    return false;
  }
  export function focusGroup(label: string) { if (active) atlas?.focusGroup(label); }
  export function focusSearch() { if (active) atlas?.focusSearch(); }
  export async function createCanvasTab(): Promise<boolean> {
    if (!active || !notes?.isCanvasFullscreen()) return false;
    await notes.newCanvasTab();
    return true;
  }
  export async function handleKey(event: KeyboardEvent): Promise<boolean> {
    if (!active) return false;
    if (view === "pipeline") return await pipeline?.handleKey(event) ?? false;
    if (view === "study") return await study?.handleKey(event) ?? false;
    if (["inbox", "jobs", "queue", "applications", "favorites"].includes(view))
      return await jobBrowser?.handleKey(event) ?? false;
    return false;
  }
</script>

{#snippet pending()}<p class="muted" role="status">Loading…</p>{/snippet}
{#key `${view}:${epoch}:${initialJobId}`}
  {#if !viewEnabled(view, activeModules.enabled)}
    <!-- Disabled modules never mount or fetch; App redirects such tabs to the atlas. -->
  {:else if view === "today"}
    <GraphNavigator bind:this={atlas} {workspace} {revision} {onview} {onopenjob} {onguide} />
  {:else if view === "settings"}
    <WorkspaceSettings bind:this={settings} {onview} {onchange} />
  {:else if view === "ai"}
    <AI {revision} demo={workspace?.demo ?? true} />
  {:else if view === "calendar"}
    {#await import("./Calendar.svelte")}{@render pending()}{:then { default: Calendar }}
      <Calendar {revision} {active} demo={workspace?.demo ?? true} />
    {/await}
  {:else if view === "notes" || view === "schema"}
    {#await import("./Notes.svelte")}{@render pending()}{:then { default: Notes }}
      <Notes bind:this={notes} {revision} demo={workspace?.demo ?? true} initialMode={view === "schema" ? "mindmap" : undefined} />
    {/await}
  {:else if view === "opportunities"}
    {#await import("./Opportunities.svelte")}{@render pending()}{:then { default: Opportunities }}
      <Opportunities {revision} demo={workspace?.demo ?? true} />
    {/await}
  {:else if view === "study"}
    {#await import("./Study.svelte")}{@render pending()}{:then { default: Study }}
      <Study bind:this={study} {revision} demo={workspace?.demo ?? true} />
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
      <JobBrowser bind:this={jobBrowser} {view} {workspace} {initialJobId} onchange={onchange} {onadd} {onview} {onopenjob} />
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
