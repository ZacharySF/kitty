<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api, type Bootstrap } from "./lib/api";
  import { navigation, navigationGroups, type View } from "./lib/workspace";
  import { Badge, Button } from "@nunera/bracket";
  import Icon from "./lib/Icon.svelte";
  import GettingStarted from "./lib/GettingStarted.svelte";
  import AddJob from "./lib/AddJob.svelte";
  import CommandPalette from "./lib/CommandPalette.svelte";
  import KeyboardHelp from "./lib/KeyboardHelp.svelte";
  import CalendarReminders from "./lib/CalendarReminders.svelte";
  import StudyFocusBar from "./lib/StudyFocusBar.svelte";
  import ThemePicker from "./lib/ThemePicker.svelte";
  import WorkspacePane from "./lib/WorkspacePane.svelte";
  import WorkspaceTabs from "./lib/WorkspaceTabs.svelte";
  import type { WorkspaceTrail } from "./lib/workspaceContext.svelte";

  type PaneHandle = {
    breadcrumbs: () => WorkspaceTrail;
    hasUnsavedChanges: () => boolean;
    focusGroup: (label: string) => void;
    focusSearch: () => void;
    createCanvasTab: () => Promise<boolean>;
    handleKey: (event: KeyboardEvent) => Promise<boolean>;
  };
  type WorkspaceTab = {
    id: string;
    view: View;
    epoch: number;
    initialJobId: number | null;
    scrollY: number;
    pane?: PaneHandle;
  };
  let nextTab = 1;
  let tabs = $state<WorkspaceTab[]>([{ id: "workspace-1", view: "today", epoch: 0, initialJobId: null, scrollY: 0 }]);
  let activeId = $state("workspace-1");
  const activeTab = $derived(tabs.find(tab => tab.id === activeId) ?? tabs[0]);
  const view = $derived(activeTab.view);
  const emptyTrail: WorkspaceTrail = { section: null, root: null, nodes: [] };
  const trail = $derived(activeTab.pane?.breadcrumbs() ?? emptyTrail);
  let workspace = $state<Bootstrap | null>(null);
  let error = $state("");
  let loading = $state(true);
  let palette = $state(false);
  let help = $state(false);
  let demoHidden = $state(false);
  let guide = $state(false);
  let addJob = $state(false);
  let revision = $state(0);
  let guideDialog = $state<HTMLDialogElement>();
  let tabBar: { openMenu: (event: MouseEvent) => void; closeMenu: (restore?: boolean) => void } | undefined = $state();
  const title = $derived(navigation.find(item => item.id === view)?.label || "Workspace");
  const tabLabels = $derived(tabs.map(tab => ({
    id: tab.id, label: tab.view === "today" ? "kitty" : navigation.find(item => item.id === tab.view)?.label || "Workspace",
  })));
  const schemaLabel = $derived(
    ({ jobs: "Jobs", notes: "Notes", flashcards: "Flashcards", opportunities: "Opportunities", schema: "Mind maps" } as Partial<Record<View, string>>)[view] || title,
  );
  const ownsTrail = $derived(trail.section === view || (view === "schema" && trail.section === "notes"));
  const schemaNodes = $derived(ownsTrail ? trail.nodes : []);
  const sectionGroup = $derived(navigationGroups.find(group => group.views.includes(view))?.label ?? "Workspace");

  function mayLeaveEditor(tab = activeTab) {
    return !tab.pane?.hasUnsavedChanges() || window.confirm("You have unsaved edits. Leave and discard them?");
  }
  function route(tab: WorkspaceTab, replace = false) {
    const method = replace ? "replaceState" : "pushState";
    window.history[method]({ workspaceTabId: tab.id }, "", `#${tab.view}`);
  }
  async function selectTab(id: string) {
    const target = tabs.find(tab => tab.id === id);
    if (!target || id === activeId) return;
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      activeTab.scrollY = window.scrollY;
      activeId = id;
      route(target);
      await tick();
      window.scrollTo(0, target.scrollY);
    } catch (cause) { error = (cause as Error).message; }
  }
  async function newTab() {
    tabBar?.closeMenu(false);
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      activeTab.scrollY = window.scrollY;
      const tab: WorkspaceTab = { id: `workspace-${++nextTab}`, view: "today", epoch: 0, initialJobId: null, scrollY: 0 };
      tabs = [...tabs, tab];
      activeId = tab.id;
      route(tab);
      await tick();
      document.getElementById("main-content")?.focus({ preventScroll: true });
      window.scrollTo(0, 0);
    } catch (cause) { error = (cause as Error).message; }
  }
  function closeTab(id: string) {
    const index = tabs.findIndex(tab => tab.id === id);
    if (index < 0 || !mayLeaveEditor(tabs[index])) return;
    const wasActive = id === activeId;
    tabs = tabs.filter(tab => tab.id !== id);
    if (!tabs.length) {
      const tab: WorkspaceTab = { id: `workspace-${++nextTab}`, view: "today", epoch: 0, initialJobId: null, scrollY: 0 };
      tabs = [tab];
      activeId = tab.id;
    } else if (wasActive) activeId = tabs[Math.min(index, tabs.length - 1)].id;
    if (wasActive) {
      route(activeTab, true);
      void tick().then(() => {
        document.getElementById(`workspace-tab-${activeId}`)?.focus();
        window.scrollTo(0, activeTab.scrollY);
      });
    }
  }
  function navigate(next: View) {
    if (!mayLeaveEditor()) return false;
    activeTab.view = next;
    activeTab.initialJobId = null;
    activeTab.epoch += 1;
    activeTab.scrollY = 0;
    route(activeTab);
    void refresh();
    void tick().then(() => {
      document.getElementById("main-content")?.focus({ preventScroll: true });
      window.scrollTo(0, 0);
    });
    return true;
  }
  async function focusGroup(label: string) {
    const id = activeId;
    if (view !== "today" && !navigate("today")) return;
    await tick();
    if (activeId === id && view === "today") activeTab.pane?.focusGroup(label);
  }
  function schemaRoot() {
    if (ownsTrail && trail.root) trail.root();
    else { activeTab.initialJobId = null; activeTab.epoch += 1; }
  }
  function schemaBack() {
    if (!schemaNodes.length) { navigate("today"); return; }
    const parent = schemaNodes.slice(0, -1).reverse().find(crumb => crumb.onclick);
    if (parent?.onclick) parent.onclick();
    else schemaRoot();
  }
  function openJob(id: number) { if (navigate("jobs")) activeTab.initialJobId = id; }
  function studyText(event: Event) {
    const detail = (event as CustomEvent).detail;
    const next: View = detail?.purpose === "flashcards" ? "flashcards" : "study";
    if (view === next || detail?.routed || !navigate(next)) return;
    void tick().then(() => window.dispatchEvent(new CustomEvent("kitty-study-text", { detail: { ...detail, routed: true } })));
  }
  function studyFocus(event: Event) {
    const detail = (event as CustomEvent).detail;
    if (view === "study" || detail?.routed || !navigate("study")) return;
    void tick().then(() => window.dispatchEvent(new CustomEvent("kitty-study-focus", { detail: { ...detail, routed: true } })));
  }
  function showGuide() { guide = true; }
  $effect(() => { if (guide && guideDialog && !guideDialog.open) guideDialog.showModal(); });

  let bootstrapRequest = 0;
  async function refresh() {
    const request = ++bootstrapRequest;
    error = "";
    try {
      const data = await api<Bootstrap>("/bootstrap");
      if (request !== bootstrapRequest) return;
      workspace = data;
      revision += 1;
    } catch (cause) {
      if (request === bootstrapRequest) error = (cause as Error).message;
    } finally { if (request === bootstrapRequest) loading = false; }
  }
  async function reload() {
    if (!mayLeaveEditor()) return;
    loading = true;
    try {
      await api("/reload", {});
      await refresh();
      activeTab.epoch += 1;
    } catch (cause) { error = (cause as Error).message; }
    finally { loading = false; }
  }
  function readRoute() {
    const next = navigation.find(item => item.id === window.location.hash.slice(1))?.id || "today";
    const savedId = window.history.state?.workspaceTabId;
    const target = tabs.find(tab => tab.id === savedId) ?? activeTab;
    if (target.view !== next) {
      if (!mayLeaveEditor(target)) { route(activeTab, true); return; }
      target.view = next;
      target.initialJobId = null;
      target.epoch += 1;
      target.scrollY = 0;
    }
    activeId = target.id;
    if (!workspace) void refresh();
  }
  onMount(() => {
    readRoute();
    window.addEventListener("kitty-study-text", studyText);
    window.addEventListener("kitty-study-focus", studyFocus);
    return () => {
      window.removeEventListener("kitty-study-text", studyText);
      window.removeEventListener("kitty-study-focus", studyFocus);
    };
  });
  async function keyboard(event: KeyboardEvent) {
    if (event.defaultPrevented || document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return;
    const target = event.target as HTMLElement | null;
    const typing = !!target?.closest('input:not([type="checkbox"]):not([type="radio"]):not([type="button"]):not([type="submit"]),textarea,select,[contenteditable="true"]');
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "t") {
      event.preventDefault();
      if (event.repeat) return;
      tabBar?.closeMenu(false);
      try {
        if (!await activeTab.pane?.createCanvasTab()) await newTab();
      } catch (cause) { error = (cause as Error).message; }
      return;
    }
    if (event.ctrlKey && !event.altKey && ["PageUp", "PageDown", "Tab"].includes(event.key)) {
      event.preventDefault();
      const direction = event.key === "PageUp" || (event.key === "Tab" && event.shiftKey) ? -1 : 1;
      const index = tabs.findIndex(tab => tab.id === activeId);
      await selectTab(tabs[(index + direction + tabs.length) % tabs.length].id);
      return;
    }
    if (!typing && !event.repeat && !event.metaKey && !event.shiftKey &&
        !window.getSelection()?.toString() && !event.getModifierState("AltGraph")) {
      const group = event.ctrlKey && event.altKey && ["Alt", "Control"].includes(event.key) ? "Plan"
        : event.ctrlKey && !event.altKey ? ({ x: "Study", c: "Job search", v: "Settings" } as Record<string, string>)[event.key.toLowerCase()] : null;
      if (group) { event.preventDefault(); await focusGroup(group); return; }
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault(); palette = true; return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
      if (typing && !["inbox", "jobs", "queue", "applications", "favorites"].includes(view)) return;
      event.preventDefault();
      if (!["inbox", "jobs", "queue", "applications", "favorites"].includes(view)) {
        if (!navigate("jobs")) return;
        await tick();
      }
      await activeTab.pane?.handleKey(event);
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Escape" && typing) { target?.blur(); event.preventDefault(); return; }
    if (typing) return;
    if (event.key === "?") { event.preventDefault(); help = true; return; }
    const control = target?.closest('button,a,summary,input[type="checkbox"],input[type="radio"],input[type="button"],input[type="submit"]');
    if (control && !control.matches(".job-row,[data-pipeline]") && ["Enter", " "].includes(event.key)) return;
    if (view === "today" && event.key === "/") { event.preventDefault(); activeTab.pane?.focusSearch(); return; }
    await activeTab.pane?.handleKey(event);
  }
</script>

<svelte:window onpopstate={readRoute} onhashchange={readRoute} onkeydown={keyboard} />
<svelte:head><title>{title} · kitty!</title></svelte:head>
<a href="#main-content" class="skip-link">Skip to content</a>
<div class="workbench" class:atlas-home={view === "today"}>
  <header class="system-bar" role="group" aria-label="Workspace controls" oncontextmenu={(event) => tabBar?.openMenu(event)}>
    {#if view !== "today"}<button class="schema-back" onclick={schemaBack} aria-label={schemaNodes.length ? "Back to parent schema" : "Back to kitty schema"} title="Back to parent schema">←</button>{/if}
    <a class="wordmark" class:current-schema={view === "today"} href="#today" aria-label="kitty home" aria-current={view === "today" ? "page" : undefined} title="Main kitty schema" onclick={(event) => { event.preventDefault(); navigate("today"); }}>
      <span class="brand-mark" aria-hidden="true"><Icon name="kitty" size={22} /></span>
      <span>kitty<span class="brand-dot">!</span></span>
    </a>
    <nav class="breadcrumb" aria-label="Schema tabs">
      {#if view !== "today"}
        <span class="separator" aria-hidden="true">›</span>
        <button class="schema-tab" class:current-schema={!schemaNodes.length} aria-current={!schemaNodes.length ? "page" : undefined} onclick={schemaRoot} title={`Back to ${schemaLabel} schema`}>{schemaLabel}</button>
        {#each schemaNodes as crumb, index}
          <span class="separator" aria-hidden="true">›</span>
          {#if crumb.onclick}
            <button class="schema-tab" class:current-schema={index === schemaNodes.length - 1} aria-current={index === schemaNodes.length - 1 ? "page" : undefined} onclick={crumb.onclick} title={crumb.label}>{crumb.label}</button>
          {:else}
            <span class="schema-tab current-schema" aria-current="page" title={crumb.label}>{crumb.label}</span>
          {/if}
        {/each}
      {/if}
    </nav>
    <nav class="workspace-groups" aria-label="Workspace groups">
      {#each navigationGroups as group, index}<button aria-current={sectionGroup === group.label ? "page" : undefined}
        title={`${group.label} · Focus its schema node: ${["Ctrl + Alt", "Ctrl + X", "Ctrl + C", "Ctrl + V"][index]}`}
        onclick={() => navigate(group.views[0])}>{group.label}</button>{/each}
    </nav>
    <ThemePicker />
    <button
      class="system-command"
      onclick={() => (palette = true)}
      aria-label="Search pages, jobs and commands"
      ><Icon name="jobs" size={15} /><span>Search anything</span><kbd
        >Ctrl K</kbd
      ></button
    >
    <Button
      variant="ghost"
      size="sm"
      aria-label="Refresh workspace and configuration"
      title="Reload configuration and refresh"
      disabled={loading}
      onclick={reload}><Icon name="refresh" size={17} /></Button
    >
  </header>
  <WorkspaceTabs bind:this={tabBar} tabs={tabLabels} {activeId} onselect={selectTab} onclose={closeTab} onnew={newTab} />
  <StudyFocusBar {revision} demo={workspace?.demo ?? true} />
  {#if view !== "calendar"}<CalendarReminders />{/if}
  <main id="main-content" tabindex="-1" class:atlas-main={view === "today"}>
    {#if workspace?.demo && !demoHidden}<div class="demo-notice">
        <Badge label="DEMO" intent="warning" size="sm" />Synthetic workspace.
        Local changes work; external actions are disabled.
        <button class="icon-button" aria-label="Dismiss demo notice" onclick={() => (demoHidden = true)}>×</button>
      </div>{/if}
    {#if error}<div class="error-banner" role="alert">
        {error}<button class="button" onclick={refresh}>Try again</button><button class="icon-button" aria-label="Dismiss workspace message" onclick={() => (error = "")}>×</button>
      </div>{/if}
    {#each tabs as tab (tab.id)}
      <div
        id={`workspace-pane-${tab.id}`} role="tabpanel"
        aria-labelledby={`workspace-tab-${tab.id}`}
        tabindex="0"
        hidden={tab.id !== activeId} inert={tab.id !== activeId}
      >
        <WorkspacePane
          bind:this={tab.pane} tabId={tab.id} view={tab.view}
          active={tab.id === activeId} {workspace} {revision}
          epoch={tab.epoch} initialJobId={tab.initialJobId}
          onchange={refresh}
          onview={next => { if (tab.id === activeId) navigate(next); }}
          onopenjob={id => { if (tab.id === activeId) openJob(id); }}
          onadd={() => { if (tab.id === activeId) addJob = true; }}
          onguide={() => { if (tab.id === activeId) showGuide(); }}
        />
      </div>
    {/each}
  </main>
</div>
{#if guide}<dialog
    bind:this={guideDialog}
    class="workspace-dialog guide-dialog"
    aria-labelledby="guide-title"
    onclose={() => (guide = false)}
  >
    <header>
      <span class="eyebrow">HOW IT WORKS</span>
      <button
        class="icon-button"
        onclick={() => (guide = false)}
        aria-label="Close the guide">×</button
      >
    </header>
    <GettingStarted
      {revision}
      {workspace}
      onview={(next) => {
        guide = false;
        navigate(next);
      }}
    />
  </dialog>{/if}
{#if palette}<CommandPalette
    onclose={() => (palette = false)}
    onview={navigate}
    onopenjob={openJob}
    onadd={() => (addJob = true)}
    onhelp={() => (help = true)}
    onreload={() => void reload()}
  />{/if}
{#if help}<KeyboardHelp onclose={() => (help = false)} />{/if}
{#if addJob}<AddJob
    {workspace}
    onclose={() => (addJob = false)}
    onsaved={async (id) => {
      addJob = false;
      await refresh();
      openJob(id);
    }}
  />{/if}
