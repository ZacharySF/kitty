<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import { api, type Bootstrap } from "./lib/api";
  import { navigation, navigationGroups, isView, notePageLink, viewEnabled, visibleGroups, type View } from "./lib/workspace";
  import { setActiveModules } from "./lib/modules.svelte";
  import { Badge, Button } from "@nunera/bracket";
  import Icon from "./lib/Icon.svelte";
  import GettingStarted from "./lib/GettingStarted.svelte";
  import AddJob from "./lib/AddJob.svelte";
  import CommandPalette from "./lib/CommandPalette.svelte";
  import KeyboardHelp from "./lib/KeyboardHelp.svelte";
  import NotificationBell from "./lib/NotificationBell.svelte";
  import NotificationCenter from "./lib/NotificationCenter.svelte";
  import NotificationToasts from "./lib/NotificationToasts.svelte";
  import { notifier } from "./lib/notifications.svelte";
  import type { Notice } from "./lib/notify";
  import StudyFocusBar from "./lib/StudyFocusBar.svelte";
  import ThemePicker from "./lib/ThemePicker.svelte";
  import WorkspacePane from "./lib/WorkspacePane.svelte";
  import WorkspaceTabs from "./lib/WorkspaceTabs.svelte";
  import type { SaveStatus, WorkspaceTrail } from "./lib/workspaceContext.svelte";
  import { focusContext, installPanelFullscreen, type PanelFullscreen } from "./lib/panelFullscreen";
  import { isCloseTabShortcut } from "./lib/tabShortcut";
  import Onboarding from "./lib/Onboarding.svelte";
  import PairingPrompt from "./lib/PairingPrompt.svelte";
  import BuildEditor from "./lib/BuildEditor.svelte";
  import { buildStore } from "./lib/build.svelte";
  import PetalDrift from "./lib/PetalDrift.svelte";
  import ThemeLayer from "./lib/themes/ThemeLayer.svelte";
  import { APPEARANCE_EVENT, savedAppearance, type Appearance } from "./lib/appearance";
  import QuickCard from "./lib/flashcards/QuickCard.svelte";
  import Rika from "./lib/rika/Rika.svelte";
  import { rika } from "./lib/rika/session.svelte";
  import { goalHash } from "./lib/goals/goals";

  type PaneHandle = {
    breadcrumbs: () => WorkspaceTrail;
    saveStatus: () => SaveStatus | null;
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
  const enabled = $derived(workspace?.modules.enabled ?? []);
  const applyai = $derived(enabled.includes("applyai"));
  const studyTools = $derived(enabled.includes("workspace"));
  const terminal = $derived(enabled.includes("terminal"));
  const groups = $derived(visibleGroups(enabled));
  let moduleNotice = $state("");
  let error = $state("");
  let loading = $state(true);
  let palette = $state(false);
  let help = $state(false);
  let demoHidden = $state(false);
  let guide = $state(false);
  let addJob = $state(false);
  /** The quick-add card popover (Q66), opened by N on Flashcards or Ctrl K "New card". */
  let quickCard = $state<{ deck: number | null } | null>(null);
  let revision = $state(0);
  let guideDialog = $state<HTMLDialogElement>();
  let tabBar: { openMenu: (event: MouseEvent) => void; closeMenu: (restore?: boolean) => void } | undefined = $state();
  const title = $derived(navigation.find(item => item.id === view)?.label || "Workspace");
  const tabLabels = $derived(tabs.map(tab => ({
    id: tab.id,
    label: tab.view === "today" ? "rika!" : navigation.find(item => item.id === tab.view)?.label || "Workspace",
    icon: tab.view === "today" ? "kitty" : navigation.find(item => item.id === tab.view)?.icon || "atlas",
    tone: navigationGroups.find(group => group.views.includes(tab.view))?.tone ?? "var(--c1)",
    status: tab.pane?.saveStatus() ?? null,
  })));
  const schemaLabel = $derived(
    ({ jobs: "Jobs", notes: "Notes", flashcards: "Flashcards", opportunities: "Opportunities", schema: "Mind maps" } as Partial<Record<View, string>>)[view] || title,
  );
  const ownsTrail = $derived(trail.section === view);
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
    panelFullscreen?.exit();
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
    panelFullscreen?.exit();
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
    if (id === activeId) panelFullscreen?.exit();
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
  function disabledNotice(target: View) {
    const owner = navigation.find(item => item.id === target);
    const info = workspace?.modules.available.find(item => item.id === owner?.module);
    const pending = owner?.module && workspace?.modules.configured.includes(owner.module);
    return `${owner?.label ?? "That page"} belongs to the ${info?.label ?? owner?.module} module, which is off in this session. ${
      pending ? "It is saved as on and starts after you restart kitty." : "Turn it on in Settings, then restart kitty."}`;
  }
  function navigate(next: View) {
    if (workspace && !viewEnabled(next, enabled)) { moduleNotice = disabledNotice(next); return false; }
    if (!mayLeaveEditor()) return false;
    panelFullscreen?.exit();
    moduleNotice = "";
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
  /** Module notices open Settings on its Modules page (`#settings/modules`). */
  function openModuleSettings() {
    if (navigate("settings")) window.history.replaceState(window.history.state, "", "#settings/modules");
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
  function openJob(id: number) { if (applyai && navigate("jobs")) activeTab.initialJobId = id; }
  /** Q1 C: terminals open as Tern windows; without a usable Tern, the Tern view says why. */
  async function openTerminal() {
    try { await api("/terminal/tern/open", {}); } catch { navigate("tern"); }
  }
  function openNotice(notice: Notice) {
    if (notice.target.kind === "rika") window.location.hash = goalHash(notice.target.goal_id);
    else if (notice.target.kind === "calendar") navigate("calendar");
    else if (notice.target.job_id !== null && applyai) openJob(notice.target.job_id);
    else navigate("reminders");
  }
  // Reminder sources the active modules provide, plus Rika's urgent goal matches (always on);
  // polling restarts only when they change.
  const notifySources = $derived(workspace ? `${studyTools}:${applyai}` : "");
  $effect(() => {
    if (!notifySources) return;
    const [calendar, job] = notifySources.split(":").map(value => value === "true");
    return untrack(() => notifier.start({ calendar, job, rika: true }));
  });
  function studyText(event: Event) {
    if (!studyTools) return;
    const detail = (event as CustomEvent).detail;
    const next: View = detail?.purpose === "flashcards" ? "flashcards" : "study";
    if (view === next || detail?.routed || !navigate(next)) return;
    void tick().then(() => window.dispatchEvent(new CustomEvent("kitty-study-text", { detail: { ...detail, routed: true } })));
  }
  function studyFocus(event: Event) {
    if (!studyTools) return;
    const detail = (event as CustomEvent).detail;
    if (view === "study" || detail?.routed || !navigate("study")) return;
    void tick().then(() => window.dispatchEvent(new CustomEvent("kitty-study-focus", { detail: { ...detail, routed: true } })));
  }
  function quickAddCard(event: Event) {
    if (studyTools) quickCard = { deck: (event as CustomEvent<{ deck_id?: number }>).detail?.deck_id || null };
  }
  function showGuide() { if (applyai) guide = true; }
  $effect(() => { if (guide && guideDialog && !guideDialog.open) guideDialog.showModal(); });

  let bootstrapRequest = 0;
  async function refresh() {
    const request = ++bootstrapRequest;
    error = "";
    try {
      const data = await api<Bootstrap>("/bootstrap");
      if (request !== bootstrapRequest) return;
      setActiveModules(data.modules.enabled);
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
    // `#<view>/<detail>` (e.g. `#settings/appearance`) opens the view; the view reads its detail.
    const hash = window.location.hash.slice(1);
    // A link to one page opens Notes on it, the way rika!'s own links between views do.
    const linkedPage = notePageLink(hash);
    if (linkedPage !== null) {
      try { sessionStorage.setItem("kitty.notes.open-page", String(linkedPage)); } catch { /* The event still opens it. */ }
      window.history.replaceState(window.history.state, "", "#notes");
      window.dispatchEvent(new CustomEvent("kitty-open-note", { detail: { page_id: linkedPage } }));
    }
    const view = hash.split("/")[0];
    let next: View = linkedPage !== null ? "notes" : isView(view) ? view : "today";
    let redirected = false;
    if (workspace && !viewEnabled(next, enabled)) {
      moduleNotice = disabledNotice(next);
      next = "today";
      redirected = true;
    }
    const savedId = window.history.state?.workspaceTabId;
    panelFullscreen?.exit();
    const target = tabs.find(tab => tab.id === savedId) ?? activeTab;
    if (target.view !== next) {
      if (!mayLeaveEditor(target)) { route(activeTab, true); return; }
      target.view = next;
      target.initialJobId = null;
      target.epoch += 1;
      target.scrollY = 0;
    }
    activeId = target.id;
    if (redirected) route(target, true);
    if (!workspace) void refresh();
  }
  // A deep link read before bootstrap may name a view the active modules do not provide.
  $effect(() => {
    if (!workspace) return;
    const blocked = tabs.filter(tab => !viewEnabled(tab.view, enabled));
    if (!blocked.length) return;
    untrack(() => {
      for (const tab of blocked) {
        moduleNotice = disabledNotice(tab.view);
        tab.view = "today";
        tab.initialJobId = null;
        tab.epoch += 1;
        tab.scrollY = 0;
        if (tab.id === activeId) route(tab, true);
      }
    });
  });
  let topBar = $state<HTMLElement>();
  let background = $state(savedAppearance().background);
  const appearanceChanged = (event: Event) => (background = (event as CustomEvent<Appearance>).detail.background);
  let panelFullscreen: PanelFullscreen | undefined;
  onMount(() => {
    readRoute();
    const fullscreen = installPanelFullscreen(window, document);
    panelFullscreen = fullscreen.panels;
    window.addEventListener("kitty-study-text", studyText);
    window.addEventListener("kitty-study-focus", studyFocus);
    window.addEventListener("kitty-quick-card", quickAddCard);
    // Capture before terminal/editor handlers consume Ctrl+W as word deletion.
    window.addEventListener("keydown", closeTabShortcut, true);
    window.addEventListener(APPEARANCE_EVENT, appearanceChanged);
    // Page-level sticky panels sit below the pinned bar, whose height wraps responsively.
    const measure = new ResizeObserver(([entry]) =>
      document.documentElement.style.setProperty("--workbench-top", `${Math.ceil(entry.borderBoxSize[0].blockSize)}px`));
    if (topBar) measure.observe(topBar);
    return () => {
      fullscreen.dispose();
      measure.disconnect();
      window.removeEventListener("kitty-study-text", studyText);
      window.removeEventListener("kitty-study-focus", studyFocus);
      window.removeEventListener("kitty-quick-card", quickAddCard);
      window.removeEventListener("keydown", closeTabShortcut, true);
      window.removeEventListener(APPEARANCE_EVENT, appearanceChanged);
    };
  });
  function closeTabShortcut(event: KeyboardEvent) {
    if (!isCloseTabShortcut(event, Boolean(document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')))) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.repeat) return;
    tabBar?.closeMenu(false);
    closeTab(activeId);
  }
  let planChord = false;
  function keyboardUp(event: KeyboardEvent) {
    const chord = planChord;
    planChord = false;
    if (chord && ["Alt", "Control"].includes(event.key) && groups.some(item => item.label === "Plan")) {
      event.preventDefault();
      void focusGroup("Plan");
    }
  }
  async function keyboard(event: KeyboardEvent) {
    planChord = false;
    if (event.defaultPrevented || document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return;
    const target = event.target as HTMLElement | null;
    const context = focusContext(target);
    if (context === "terminal") return;
    const typing = context === "text";
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
    // Ctrl+Alt alone opens Plan when it is released, so chords that start with it
    // (Ctrl+Alt+Shift+S, safe mode) never open Plan on the way.
    planChord = !typing && !event.repeat && !event.metaKey && !event.shiftKey && event.ctrlKey && event.altKey &&
      ["Alt", "Control"].includes(event.key) && !window.getSelection()?.toString() && !event.getModifierState("AltGraph");
    if (planChord) return;
    if (!typing && !event.repeat && !event.metaKey && !event.shiftKey &&
        !window.getSelection()?.toString() && !event.getModifierState("AltGraph")) {
      const group = event.ctrlKey && !event.altKey ? ({ x: "Study", c: "Job search", v: "Settings" } as Record<string, string>)[event.key.toLowerCase()] : null;
      if (group && groups.some(item => item.label === group)) { event.preventDefault(); await focusGroup(group); return; }
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault(); palette = true; return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f" && applyai) {
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

<svelte:window onpopstate={readRoute} onhashchange={readRoute} onkeydown={keyboard} onkeyup={keyboardUp} />
<ThemeLayer />
{#if background === "petals"}<PetalDrift />{/if}
<svelte:head><title>{title} · rika!</title></svelte:head>
<a href="#main-content" class="skip-link">Skip to content</a>
<div class="workbench" class:atlas-home={view === "today"}>
  <div class="workbench-top" bind:this={topBar}>
  <header class="system-bar" role="group" aria-label="Workspace controls" oncontextmenu={(event) => tabBar?.openMenu(event)}>
    {#if view !== "today"}<button class="schema-back" onclick={schemaBack} aria-label={schemaNodes.length ? "Back to parent schema" : "Back to rika! schema"} title="Back to parent schema">←</button>{/if}
    <a class="wordmark" class:current-schema={view === "today"} href="#today" aria-label="rika! home" aria-current={view === "today" ? "page" : undefined} title="Main rika! schema" onclick={(event) => { event.preventDefault(); navigate("today"); }}>
      <span class="brand-mark" aria-hidden="true"><Icon name="kitty" size={22} /></span>
      <span class="word">rika<span class="brand-dot">!</span></span>
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
      {#each groups as group}<button aria-current={sectionGroup === group.label ? "page" : undefined}
        title={`${group.label} · Focus its schema node: ${group.shortcut}`}
        onclick={() => navigate(group.views[0])}>{group.label}</button>{/each}
    </nav>
    {#if studyTools}<StudyFocusBar {revision} demo={workspace?.demo ?? true} />{/if}
    {#if notifySources}<NotificationBell />{/if}
    <ThemePicker />
    <button
      class="system-command"
      onclick={() => (palette = true)}
      aria-label={applyai ? "Search pages, jobs and commands" : "Search pages and commands"}
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
  </div>
  <main id="main-content" tabindex="-1" class:atlas-main={view === "today"}>
    {#if workspace && view !== "settings"}<PairingPrompt onsettings={() => navigate("settings")} />{/if}
    {#if workspace?.demo && !demoHidden}<div class="demo-notice">
        <Badge label="DEMO" intent="warning" size="sm" />Synthetic workspace.
        Local changes work; external actions are disabled.
        <button class="icon-button" aria-label="Dismiss demo notice" onclick={() => (demoHidden = true)}>×</button>
      </div>{/if}
    {#if workspace?.modules.restart_required && view !== "settings"}<div class="demo-notice" role="status">
        Module changes are saved but not active yet. Restart rika! to apply them.
        <button class="text-button" onclick={openModuleSettings}>Review in Settings</button>
      </div>{/if}
    {#if moduleNotice}<div class="demo-notice" role="status">
        {moduleNotice}
        {#if view !== "settings"}<button class="text-button" onclick={openModuleSettings}>Open Settings</button>{/if}
        <button class="icon-button" aria-label="Dismiss module message" onclick={() => (moduleNotice = "")}>×</button>
      </div>{/if}
    {#if loading && !workspace && !error}<p class="muted" role="status">Loading workspace…</p>{/if}
    {#if error}<div class="error-banner" role="alert">
        {error}<button class="button" onclick={refresh}>Try again</button><button class="icon-button" aria-label="Dismiss workspace message" onclick={() => (error = "")}>×</button>
      </div>{/if}
    {#each tabs as tab (tab.id)}
      <div
        id={`workspace-pane-${tab.id}`} role="tabpanel"
        aria-labelledby={`workspace-tab-${tab.id}`}
        tabindex="0"
        data-kitty-panel="view"
        hidden={tab.id !== activeId} inert={tab.id !== activeId}
      >
        {#if (workspace && viewEnabled(tab.view, enabled)) || tab.view === "settings" || tab.view === "ai"}
        <WorkspacePane
          bind:this={tab.pane} tabId={tab.id} view={tab.view}
          active={tab.id === activeId} {workspace} {revision}
          epoch={tab.epoch} initialJobId={tab.initialJobId}
          onchange={refresh}
          onview={next => { if (tab.id === activeId) navigate(next); }}
          onopenjob={id => { if (tab.id === activeId) openJob(id); }}
          onadd={() => { if (tab.id === activeId && applyai) addJob = true; }}
          onguide={() => { if (tab.id === activeId) showGuide(); }}
        />
        {/if}
      </div>
    {/each}
  </main>
</div>
{#if notifySources}<NotificationToasts onopen={openNotice} /><NotificationCenter onopen={openNotice} />{/if}
{#if workspace}<Rika onnavigate={next => { if (isView(next) && next !== view) navigate(next); }} />{/if}
{#if buildStore.editorOpen}<BuildEditor onclose={() => (buildStore.editorOpen = false)} />{/if}
{#if workspace}<Onboarding {workspace} onview={next => { if (next !== view) navigate(next); }} />{/if}
{#if guide && applyai}<dialog
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
    {enabled}
    onclose={() => (palette = false)}
    onview={navigate}
    onopenjob={openJob}
    onadd={() => { if (applyai) addJob = true; }}
    onhelp={() => (help = true)}
    onnewcard={studyTools ? () => (quickCard = { deck: null }) : undefined}
    onreload={() => void reload()}
    onterminal={terminal ? () => void openTerminal() : undefined}
    onrika={() => rika.showDrawer()}
  />{/if}
{#if help}<KeyboardHelp {enabled} onclose={() => (help = false)} />{/if}
{#if quickCard && studyTools}<QuickCard deck={quickCard.deck} onclose={() => (quickCard = null)} />{/if}
{#if addJob && applyai}<AddJob
    {workspace}
    onclose={() => (addJob = false)}
    onsaved={async (id) => {
      addJob = false;
      await refresh();
      openJob(id);
    }}
  />{/if}
