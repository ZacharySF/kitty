<script lang="ts">
  import { provideWorkspaceContext, type WorkspaceTrail } from "./workspaceContext.svelte";
  import type { Bootstrap } from "./api";
  import type { View } from "./workspace";
  import GraphNavigator from "./GraphNavigator.svelte";
  import JobBrowser from "./JobBrowser.svelte";
  import Pipeline from "./Pipeline.svelte";
  import Reminders from "./Reminders.svelte";
  import Library from "./Library.svelte";
  import Sources from "./Sources.svelte";
  import Profile from "./Profile.svelte";
  import Insights from "./Insights.svelte";
  import Questions from "./Questions.svelte";
  import Calendar from "./Calendar.svelte";
  import Opportunities from "./Opportunities.svelte";
  import Study from "./Study.svelte";
  import LearnHarness from "./LearnHarness.svelte";
  import Flashcards from "./Flashcards.svelte";
  import Notes from "./Notes.svelte";
  import QuickNotes from "./QuickNotes.svelte";
  import Documents from "./Documents.svelte";
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
  let notes: {
    hasUnsavedChanges: () => boolean;
    isCanvasFullscreen: () => boolean;
    newCanvasTab: () => Promise<void>;
  } | undefined = $state();

  export function breadcrumbs() { return trail; }
  export function hasUnsavedChanges(): boolean {
    if (view === "profile") return profile?.hasUnsavedChanges() ?? false;
    if (view === "documents") return documents?.hasUnsavedChanges() ?? false;
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

{#key `${view}:${epoch}:${initialJobId}`}
  {#if view === "today"}
    <GraphNavigator bind:this={atlas} {workspace} {revision} {onview} {onopenjob} {onguide} />
  {:else if view === "calendar"}
    <Calendar {revision} {active} demo={workspace?.demo ?? true} />
  {:else if view === "notes" || view === "schema"}
    <Notes bind:this={notes} {revision} demo={workspace?.demo ?? true} initialMode={view === "schema" ? "mindmap" : undefined} />
  {:else if view === "quick-notes"}
    <QuickNotes />
  {:else if view === "opportunities"}
    <Opportunities {revision} demo={workspace?.demo ?? true} />
  {:else if view === "study"}
    <Study bind:this={study} {revision} demo={workspace?.demo ?? true} />
  {:else if view === "learnharness"}
    <LearnHarness />
  {:else if view === "flashcards"}
    <Flashcards {revision} demo={workspace?.demo ?? true} />
  {:else if view === "documents"}
    <Documents bind:this={documents} />
  {:else if view === "settings"}
    <WorkspaceSettings {onview} />
  {:else if view === "ai"}
    <AI {revision} demo={workspace?.demo ?? true} />
  {:else if view === "inbox" || view === "jobs" || view === "queue" || view === "applications" || view === "favorites"}
    <JobBrowser bind:this={jobBrowser} {view} {workspace} {initialJobId} onchange={onchange} {onadd} {onview} {onopenjob} />
  {:else if view === "pipeline"}
    <Pipeline bind:this={pipeline} {workspace} onchange={onchange} onmail={() => onview("mail")} />
  {:else if view === "reminders"}
    <Reminders {onopenjob} onchange={onchange} />
  {:else if view === "resumes"}
    <Library demo={workspace?.demo ?? true} {onopenjob} />
  {:else if view === "sources"}
    <Sources demo={workspace?.demo ?? true} />
  {:else if view === "profile"}
    <Profile bind:this={profile} demo={workspace?.demo ?? true} onresume={() => onview("resumes")} />
  {:else if view === "finder" || view === "reports" || view === "mail" || view === "triage"}
    <Insights section={view} demo={workspace?.demo ?? true} />
  {:else if view === "questions"}
    <Questions demo={workspace?.demo ?? true} />
  {/if}
{/key}
