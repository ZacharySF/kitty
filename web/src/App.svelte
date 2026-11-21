<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import { api, type Bootstrap } from "./lib/api";
  import { isView, navigation, navItem, notePageLink, viewEnabled, viewFromHash, visibleGroups, groupOf, type AnyView, type View } from "./lib/workspace";
  import { activeModules, setActiveModules } from "./lib/modules.svelte";
  import Icon from "./lib/Icon.svelte";
  import { ICONS } from "./lib/icons";
  import BrandMark from "./lib/BrandMark.svelte";
  import GettingStarted from "./lib/GettingStarted.svelte";
  import AddJob from "./lib/AddJob.svelte";
  import CommandPalette from "./lib/CommandPalette.svelte";
  import KeyboardHelp from "./lib/KeyboardHelp.svelte";
  import TabSwitcher from "./lib/TabSwitcher.svelte";
  import TabOverview from "./lib/TabOverview.svelte";
  import NotificationCenter from "./lib/NotificationCenter.svelte";
  import NotificationToasts from "./lib/NotificationToasts.svelte";
  import { notifier } from "./lib/notifications.svelte";
  import type { Notice } from "./lib/notify";
  import StudyFocusBar from "./lib/StudyFocusBar.svelte";
  import WorkspacePane from "./lib/WorkspacePane.svelte";
  import type { SaveStatus, WorkspaceTrail } from "./lib/workspaceContext.svelte";
  import { focusContext, installPanelFullscreen, type PanelFullscreen } from "./lib/panelFullscreen";
  import { isCloseTabShortcut } from "./lib/tabShortcut";
  import Onboarding from "./lib/Onboarding.svelte";
  import ModuleDownload from "./lib/ModuleDownload.svelte";
  import PairingPrompt from "./lib/PairingPrompt.svelte";
  import EffectsPrompt from "./lib/EffectsPrompt.svelte";
  import BuildEditor from "./lib/BuildEditor.svelte";
  import { buildStore, previewLook, setTheme } from "./lib/build.svelte";
  import ThemeCarousel from "./lib/themes/ThemeCarousel.svelte";
  import { getTheme } from "./lib/themes/catalog";
  import { shownThemes } from "./lib/themes/installed";
  import PetalDrift from "./lib/PetalDrift.svelte";
  import ThemeLayer from "./lib/themes/ThemeLayer.svelte";
  import { APPEARANCE_EVENT, savedAppearance, type Appearance } from "./lib/appearance";
  import QuickCard from "./lib/flashcards/QuickCard.svelte";
  import Rika from "./lib/rika/Rika.svelte";
  import { rika } from "./lib/rika/session.svelte";
  import { onAskRika, setFocusView } from "./lib/rika/focus.svelte";
  import RikaDock from "./lib/rika/RikaDock.svelte";
  import { templatePicker } from "./lib/home/templatePicker.svelte";
  import { goalHash } from "./lib/goals/goals";
  import PageEdit from "./lib/pageEdit/PageEdit.svelte";
  import PageWidgets from "./lib/pageEdit/PageWidgets.svelte";

  type PaneHandle = {
    breadcrumbs: () => WorkspaceTrail;
    saveStatus: () => SaveStatus | null;
    hasUnsavedChanges: () => boolean;
    focusGroup: (label: string) => void;
    focusSearch: () => void;
    createCanvasPage: () => Promise<boolean>;
    handleKey: (event: KeyboardEvent) => Promise<boolean>;
  };
  type WorkspaceTab = {
    id: string;
    view: AnyView;
    epoch: number;
    initialJobId: number | null;
    scrollY: number;
    /** One tab system: a Notes tab shows one page, and is named after it. */
    page: { id: number; title: string } | null;
    pane?: PaneHandle;
  };
  let nextTab = 1;
  function makeTab(id = `workspace-${++nextTab}`, view: View = "today", page: WorkspaceTab["page"] = null): WorkspaceTab {
    return { id, view, epoch: 0, initialJobId: null, scrollY: 0, page };
  }
  let tabs = $state<WorkspaceTab[]>([makeTab("workspace-1")]);
  let activeId = $state("workspace-1");
  const activeTab = $derived(tabs.find(tab => tab.id === activeId) ?? tabs[0]);
  const view = $derived(activeTab.view);
  // What Rika is told the owner is looking at (rika/focus.svelte.ts): the view, before its open
  // item reports itself (views' effects run after this pre-effect).
  $effect.pre(() => {
    const next = view;
    untrack(() => setFocusView(next));
  });
  const emptyTrail: WorkspaceTrail = { section: null, root: null, nodes: [] };
  const trail = $derived(activeTab.pane?.breadcrumbs() ?? emptyTrail);
  let workspace = $state<Bootstrap | null>(null);
  const enabled = $derived(workspace?.modules.enabled ?? []);
  /** Add-on views active in this process (bootstrap `modules.external.views`). */
  const external = $derived(activeModules.views);
  const applyai = $derived(enabled.includes("applyai"));
  const studyTools = $derived(enabled.includes("workspace"));
  const terminal = $derived(enabled.includes("terminal"));
  const groups = $derived(visibleGroups(enabled, external));
  let moduleNotice = $state("");
  let error = $state("");
  let loading = $state(true);
  let palette = $state(false);
  let help = $state(false);
  let guide = $state(false);
  let addJob = $state(false);
  /** The quick-add card popover (Q66), opened by N on Flashcards or Ctrl K "New card". */
  let quickCard = $state<{ deck: number | null } | null>(null);
  /** Page edit mode (pageEdit/PageEdit.svelte): the page's blocks can be moved and resized. */
  let pageEditing = $state(false);
  /** Ctrl + Shift + Space, or "Edit this page" in search: home has its own edit mode. */
  function togglePageEdit() {
    if (view === "today") window.dispatchEvent(new CustomEvent("kitty:edit-home"));
    else pageEditing = !pageEditing;
  }
  let revision = $state(0);
  let guideDialog = $state<HTMLDialogElement>();
  let tabBar: { close: (restoreFocus?: boolean) => void } | undefined = $state();
  const title = $derived(navItem(view, external)?.label || "Workspace");
  const tabLabels = $derived(tabs.map(tab => ({
    id: tab.id,
    label: tab.view === "today" ? "Home" : tab.view === "notes" && tab.page ? tab.page.title || "Untitled"
      : navItem(tab.view, external)?.label || "Workspace",
    icon: tab.view === "today" ? "kitty" : navItem(tab.view, external)?.icon || "atlas",
    status: tab.pane?.saveStatus() ?? null,
  })));
  /** The bar carries only the groups you move between; Settings is the gear at its end (and in search and the tab menu). */
  const barGroups = $derived(groups.filter(group => group.label !== "Settings"));
  /** The Icons module: a page's title carries its view's icon, drawn by CSS as a mask (styles.css). */
  function viewIcon(view: AnyView): string {
    const path = ICONS[navItem(view, external)?.icon ?? "atlas"]?.path ?? "";
    const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='${path}'/></svg>`;
    return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
  }
  /** The Icons module: one icon per top-bar group. */
  const GROUP_ICONS: Record<string, string> = { Plan: "calendar", Study: "study", Career: "jobs", Rika: "actor-rika" };
  const menuExtras = $derived([
    { label: "Settings", run: () => openSettings() },
    ...(workspace ? [{ label: "Notifications", detail: notifier.unread ? `${notifier.unread} unread` : "", run: () => (notifier.drawer = true) }] : []),
    { label: "Keyboard shortcuts", detail: "?", run: () => (help = true) },
  ]);
  const schemaLabel = $derived(
    ({ jobs: "Jobs", notes: "Notes", flashcards: "Flashcards", opportunities: "Opportunities", schema: "Mind maps" } as Partial<Record<string, string>>)[view] || title,
  );
  const ownsTrail = $derived(trail.section === view);
  const schemaNodes = $derived(ownsTrail ? trail.nodes : []);
  /** Home and the Brain fill the window instead of scrolling. */
  const fills = $derived(view === "today" || view === "brain");
  // The Brain is a place only with its pack's files here (`missing`: modules on whose pack is not installed).
  const brainView = $derived(viewEnabled("brain", enabled) && !((workspace?.modules as { missing?: string[] } | undefined)?.missing ?? []).includes("brain"));
  const sectionGroup = $derived(groupOf(view, external)?.label ?? "Workspace");

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
  /** A page already open in another tab: that tab comes forward (true) instead of a second copy. */
  function focusNotePage(from: string, page: number) {
    const showing = tabs.find(tab => tab.id !== from && tab.view === "notes" && tab.page?.id === page);
    if (!showing) return false;
    void selectTab(showing.id);
    return true;
  }
  async function newTab(view: View = "today", page: WorkspaceTab["page"] = null) {
    tabBar?.close(false);
    panelFullscreen?.exit();
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      activeTab.scrollY = window.scrollY;
      const tab = makeTab(undefined, view, page);
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
      const tab = makeTab();
      tabs = [tab];
      activeId = tab.id;
    } else if (wasActive) activeId = tabs[Math.min(index, tabs.length - 1)].id;
    if (wasActive) {
      route(activeTab, true);
      void tick().then(() => {
        document.getElementById("main-content")?.focus({ preventScroll: true });
        window.scrollTo(0, activeTab.scrollY);
      });
    }
  }
  function disabledNotice(target: AnyView) {
    if (!isView(target))
      return "That add-on page is not available in this session. Check Settings → Modules: add-on changes apply after you restart rika!.";
    const owner = navigation.find(item => item.id === target);
    const info = workspace?.modules.available.find(item => item.id === owner?.module);
    const pending = owner?.module && workspace?.modules.configured.includes(owner.module);
    return `${owner?.label ?? "That page"} belongs to the ${info?.label ?? owner?.module} module, which is off in this session. ${
      pending ? "It is saved as on and starts after you restart kitty." : "Turn it on in Settings, then restart kitty."}`;
  }
  function navigate(next: AnyView) {
    if (workspace && !viewEnabled(next, enabled, external)) { moduleNotice = disabledNotice(next); return false; }
    if (!mayLeaveEditor()) return false;
    panelFullscreen?.exit();
    moduleNotice = "";
    activeTab.view = next;
    activeTab.initialJobId = null;
    activeTab.page = null;
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
  /** Settings on one of its pages (`#settings/<page>`, e.g. `modules`, `appearance`). */
  function openSettings(page = "") {
    if (navigate("settings") && page) window.history.replaceState(window.history.state, "", `#settings/${page}`);
  }
  function openModuleSettings() { openSettings("modules"); }
  /** Where a group opens: its first page other than home (the brand mark is home). */
  function groupLanding(group: { views: AnyView[] }) {
    return group.views.find(item => item !== "today") ?? group.views[0];
  }
  /** A group's shortcut opens it as its header button does; on the Brain it finds the group on the map. */
  function focusGroup(label: string) {
    if (view === "brain") { activeTab.pane?.focusGroup(label); return; }
    const group = groups.find(item => item.label === label);
    if (group && groupLanding(group) !== view) navigate(groupLanding(group));
  }
  function schemaRoot() {
    if (ownsTrail && trail.root) trail.root();
    else { activeTab.initialJobId = null; activeTab.epoch += 1; }
  }
  function openJob(id: number) { if (applyai && navigate("jobs")) activeTab.initialJobId = id; }
  /** Q1 C: terminals open as Tern windows; without a usable Tern, the Tern view says why. */
  async function openTerminal() {
    try { await api("/terminal/tern/open", {}); } catch { navigate("tern"); }
  }
  function openNotice(notice: Notice) {
    const { target } = notice;
    if (target.kind === "rika") {
      // A Rika alert without a goal is a problem with the GitHub backup.
      if (target.goal_id === null) openSettings("backup");
      else window.location.hash = goalHash(target.goal_id);
    }
    else if (target.kind === "calendar") navigate("calendar");
    else if (target.job_id !== null && applyai) openJob(target.job_id);
    else navigate("reminders");
  }
  // Reminder sources the active modules provide, plus Rika's urgent goal matches (always on);
  // polling restarts only when they change.
  const notifySources = $derived(workspace ? `${studyTools}:${applyai}` : "");
  $effect(() => {
    if (!notifySources) return;
    const [calendar, job] = notifySources.split(":").map(value => value === "true");
    return untrack(() => {
      notifier.onopen = openNotice;
      return notifier.start({ calendar, job, rika: true });
    });
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
      setActiveModules(data.modules.enabled, data.modules.external?.views, data.modules.missing);
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
      window.history.replaceState(window.history.state, "", "#notes");
      // A page already open in a tab: that tab comes forward instead of a second copy.
      const showing = tabs.find(tab => tab.view === "notes" && tab.page?.id === linkedPage);
      if (showing) { activeId = showing.id; route(showing, true); return; }
      try { sessionStorage.setItem("kitty.notes.open-page", String(linkedPage)); } catch { /* The event still opens it. */ }
      window.dispatchEvent(new CustomEvent("kitty-open-note", { detail: { page_id: linkedPage } }));
    }
    let next: AnyView = linkedPage !== null ? "notes" : viewFromHash(hash) ?? "today";
    let redirected = false;
    if (workspace && !viewEnabled(next, enabled, external)) {
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
      target.page = null;
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
    const blocked = tabs.filter(tab => !viewEnabled(tab.view, enabled, external));
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
    tabBar?.close(false);
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
  /** Ctrl + Shift + Space arranges the page; Ctrl + Space opens search. Capture phase: element handlers that act on
   *  a bare Space never see the chords. Ctrl + K stays, since input methods such as fcitx5 take Ctrl + Space inside text fields. */
  function paletteChord(event: KeyboardEvent) {
    if (!event.ctrlKey || event.altKey || event.metaKey || event.code !== "Space") return;
    if (focusContext(event.target as HTMLElement | null) === "terminal") return;
    if (document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.repeat) return;
    if (event.shiftKey) togglePageEdit();
    else palette = true;
  }
  $effect(() => {
    window.addEventListener("keydown", paletteChord, { capture: true });
    return () => window.removeEventListener("keydown", paletteChord, { capture: true });
  });
  /** Ctrl + R (Cmd + R): Rika beside the page (rika/RikaDock.svelte), never a reload. Home (with its Rika
   *  column) and the Rika page already show her: there it focuses her field and the dock stays out of the way. */
  let rikaDock = $state(false);
  let dockReturn: HTMLElement | null = null;
  const ownRika = $derived(view === "rika" ||
    (view === "today" && (!buildStore.build.ui.home.hidden.includes("rika") || templatePicker.open)));
  const dockShown = $derived(Boolean(workspace) && rikaDock && !ownRika);
  async function openDock() {
    dockReturn = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null;
    rikaDock = true;
    await tick();
    rika.showDrawer();
  }
  async function closeDock() {
    rikaDock = false;
    const back = dockReturn;
    dockReturn = null;
    await tick();
    if (back?.isConnected && back.getClientRects().length) back.focus({ preventScroll: true });
    else document.getElementById("main-content")?.focus({ preventScroll: true });
  }
  function rikaChord(event: KeyboardEvent) {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.key.toLowerCase() !== "r") return;
    if (focusContext(event.target as HTMLElement | null) === "terminal") return;
    event.preventDefault();
    if (event.repeat || !workspace || document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return;
    event.stopPropagation();
    if (dockShown) void closeDock();
    else if (ownRika || rika.panelVisible) rika.showDrawer();
    else void openDock();
  }
  $effect(() => {
    window.addEventListener("keydown", rikaChord, { capture: true });
    return () => window.removeEventListener("keydown", rikaChord, { capture: true });
  });
  /** Any view's "Ask Rika" (rika/focus.svelte.ts `askRika`): Rika beside the page (or the page's own
   *  Rika panel, as on home and Track), then the prompt if one came. */
  $effect(() => onAskRika(async prompt => {
    if (!workspace) return;
    if (ownRika || rika.panelVisible) rika.showDrawer();
    else await openDock();
    if (prompt) await rika.send(prompt);
  }));
  /** Ctrl + O: every tab as a card to pick from. Ctrl + Y: the theme carousel. */
  let overview = $state(false);
  let carousel = $state(false);
  const carouselThemes = $derived(shownThemes(buildStore.build.ui.themes.installed, getTheme(buildStore.build.theme.base).id));
  function stepTab(direction: number) {
    const index = tabs.findIndex(tab => tab.id === activeId);
    return selectTab(tabs[(index + direction + tabs.length) % tabs.length].id);
  }
  /** Tab and theme chords, in the capture phase: an editor that takes Tab (or a letter) for itself never sees them.
   *  Ctrl + Tab / Ctrl + Shift + Tab (and Ctrl + PageDown / PageUp) step through the tabs, wrapping. In a text field
   *  Ctrl + Y stays the field's redo; anywhere else it opens the theme carousel. */
  function tabKeys(event: KeyboardEvent) {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || !workspace) return;
    const target = event.target as HTMLElement | null;
    const context = focusContext(target);
    if (context === "terminal" || document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return;
    const key = event.key.toLowerCase();
    const stepping = event.ctrlKey && (key === "tab" || key === "pageup" || key === "pagedown");
    const opening = !event.shiftKey && key === "o";
    const theming = !event.shiftKey && key === "y" && context !== "text";
    if (!stepping && !opening && !theming) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.repeat && !stepping) return;
    tabBar?.close(false);
    if (stepping) void stepTab(key === "pageup" || (key === "tab" && event.shiftKey) ? -1 : 1);
    else if (opening) overview = true;
    else carousel = true;
  }
  $effect(() => {
    window.addEventListener("keydown", tabKeys, { capture: true });
    return () => window.removeEventListener("keydown", tabKeys, { capture: true });
  });
  /** The active item of a bar of buttons: a page's tabs or the top bar's groups. */
  const ACTIVE_ITEM = '[aria-current="page"], [aria-pressed="true"], [aria-selected="true"]';
  /** Focus inside one of these keeps the arrow keys for itself (lists, grids, sliders, canvases, menus, Track's cards). */
  const ARROW_OWNERS = '[role="slider"], [role="radiogroup"], [role="listbox"], [role="grid"], [role="tree"], [role="menu"], [role="menubar"], [role="tablist"], [role="application"], [role="spinbutton"], canvas, svg, select, [contenteditable], [data-pipeline]';
  /** Clicks the button after (or before) the active one in `bar`, wrapping; false when there is nothing to step. */
  function stepBar(bar: Element | null | undefined, direction: number) {
    const buttons = [...(bar?.querySelectorAll<HTMLButtonElement>("button") ?? [])].filter(button => !button.disabled && button.getClientRects().length);
    if (buttons.length < 2) return false;
    const at = buttons.findIndex(button => button.matches(ACTIVE_ITEM));
    buttons[at < 0 ? (direction > 0 ? 0 : buttons.length - 1) : (at + direction + buttons.length) % buttons.length].click();
    return true;
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
      tabBar?.close(false);
      try {
        // In a fullscreen canvas (no top bar), Ctrl T starts a new page there instead of a tab.
        if (!await activeTab.pane?.createCanvasPage()) await newTab();
      } catch (cause) { error = (cause as Error).message; }
      return;
    }
    // Ctrl+Alt alone opens Plan when it is released, so chords that start with it
    // (Ctrl+Alt+Shift+S, safe mode) never open Plan on the way.
    planChord = !typing && !event.repeat && !event.metaKey && !event.shiftKey && event.ctrlKey && event.altKey &&
      ["Alt", "Control"].includes(event.key) && !window.getSelection()?.toString() && !event.getModifierState("AltGraph");
    if (planChord) return;
    // Ctrl + X / C / V belong to the clipboard everywhere; groups are in the bar and in search.
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
    // Ctrl + ← / →: the top bar's groups, in its order (in a text field they stay word jumps).
    if (event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey && (event.key === "ArrowLeft" || event.key === "ArrowRight") && !typing) {
      if (stepBar(document.querySelector("nav.workspace-groups"), event.key === "ArrowRight" ? 1 : -1)) event.preventDefault();
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Escape" && typing) { target?.blur(); event.preventDefault(); return; }
    if (typing) return;
    if (event.key === "?") { event.preventDefault(); help = true; return; }
    // ← / →: the page's own tabs (its [data-page-tabs] bar), when nothing focused or open wants the arrows.
    if ((event.key === "ArrowLeft" || event.key === "ArrowRight") && !event.shiftKey && !target?.closest(ARROW_OWNERS) &&
      !document.querySelector('[role="dialog"], :popover-open')) {
      const bar = [...document.querySelectorAll("#main-content [data-page-tabs]")].find(node => node.getClientRects().length);
      if (stepBar(bar, event.key === "ArrowRight" ? 1 : -1)) { event.preventDefault(); return; }
    }
    const control = target?.closest('button,a,summary,input[type="checkbox"],input[type="radio"],input[type="button"],input[type="submit"]');
    if (control && !control.matches("[data-job-id],[data-pipeline]") && ["Enter", " "].includes(event.key)) return;
    if (fills && event.key === "/") { event.preventDefault(); activeTab.pane?.focusSearch(); return; }
    await activeTab.pane?.handleKey(event);
  }
</script>

<svelte:window onpopstate={readRoute} onhashchange={readRoute} onkeydown={keyboard} onkeyup={keyboardUp} />
<ThemeLayer />
{#if background === "petals"}<PetalDrift />{/if}
<svelte:head><title>{title} · rika!</title></svelte:head>
<a href="#main-content" class="skip-link">Skip to content</a>
<div class="workbench" class:atlas-home={fills}>
  <div class="workbench-top" bind:this={topBar}>
  <header class="system-bar" role="group" aria-label="Workspace controls">
    <div class="bar-start">
      <a class="wordmark" class:current-schema={view === "today"} href="#today" aria-label="rika! home" aria-current={view === "today" ? "page" : undefined} title="Home" onclick={(event) => { event.preventDefault(); navigate("today"); }}>
        <span class="brand-mark" aria-hidden="true"><BrandMark size={26} /></span>
        <span class="word">rika<span class="brand-dot">!</span></span>
      </a>
      <!-- Breadcrumb only inside a nested schema (a notebook, a map, a job): the bar names where you are, no more. -->
      {#if schemaNodes.length}<nav class="breadcrumb" aria-label="Schema path">
        <span class="separator" aria-hidden="true">›</span>
        <button class="schema-tab" onclick={schemaRoot} title={`Back to ${schemaLabel}`}>{schemaLabel}</button>
        {#each schemaNodes as crumb, index}
          <span class="separator" aria-hidden="true">›</span>
          {#if crumb.onclick && index < schemaNodes.length - 1}
            <button class="schema-tab" onclick={crumb.onclick} title={crumb.label}>{crumb.label}</button>
          {:else}
            <span class="schema-tab current-schema" aria-current="page" title={crumb.label}>{crumb.label}</span>
          {/if}
        {/each}
      </nav>{/if}
    </div>
    <nav class="workspace-groups" aria-label="Workspace groups">
      {#if brainView}<button data-tutorial="brain" aria-current={view === "brain" ? "page" : undefined}
        onclick={() => navigate("brain")}><span class="module-icon"><Icon name="atlas" size={17} /></span>Brain</button>{/if}
      {#each barGroups as group}<button aria-current={sectionGroup === group.label && view !== "brain" && view !== "today" ? "page" : undefined}
        title={group.shortcut ? `${group.label} (${group.shortcut})` : group.label}
        onclick={() => navigate(groupLanding(group))}><span class="module-icon"><Icon name={GROUP_ICONS[group.label] ?? "atlas"} size={17} /></span>{group.label}</button>{/each}
    </nav>
    <div class="bar-end">
      {#if studyTools}<StudyFocusBar {revision} demo={workspace?.demo ?? true} />{/if}
      <TabSwitcher bind:this={tabBar} tabs={tabLabels} {activeId} extras={menuExtras} onselect={selectTab} onclose={closeTab} onnew={newTab} />
      <button
        class="system-command"
        data-notification-anchor
        onclick={() => (palette = true)}
        aria-label={applyai ? "Search pages, jobs and commands" : "Search pages and commands"}
        title="Search · Ctrl Space"
        ><Icon name="jobs" size={18} /></button>
      <button
        class="system-command"
        onclick={() => openSettings()}
        aria-label="Settings"
        aria-current={view === "settings" ? "page" : undefined}
        title="Settings"
        ><Icon name="settings" size={18} /></button>
    </div>
  </header>
  </div>
  <main id="main-content" tabindex="-1" class:atlas-main={fills}>
    {#if workspace && view !== "settings"}<PairingPrompt onsettings={() => navigate("settings")} />{/if}
    {#if workspace}<EffectsPrompt />{/if}
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
        id={`workspace-pane-${tab.id}`} role="region"
        aria-label={tabLabels.find(label => label.id === tab.id)?.label}
        tabindex="0"
        data-kitty-panel="view" style:--view-icon={viewIcon(tab.view)}
        hidden={tab.id !== activeId} inert={tab.id !== activeId}
      >
        {#if (workspace && viewEnabled(tab.view, enabled, external)) || tab.view === "settings" || tab.view === "ai"}
        <WorkspacePane
          bind:this={tab.pane} tabId={tab.id} view={tab.view}
          active={tab.id === activeId} {workspace} {revision}
          epoch={tab.epoch} initialJobId={tab.initialJobId} notePage={tab.page?.id ?? null}
          onchange={refresh}
          onview={next => { if (tab.id === activeId) navigate(next); }}
          onopenjob={id => { if (tab.id === activeId) openJob(id); }}
          onadd={() => { if (tab.id === activeId && applyai) addJob = true; }}
          onguide={() => { if (tab.id === activeId) showGuide(); }}
          onnotepage={(id, title) => { tab.page = id ? { id, title } : null; }}
          onfocusnotepage={id => focusNotePage(tab.id, id)}
          onnotepagetab={id => void newTab("notes", { id, title: "" })}
        />
        {/if}
        {#if isView(tab.view)}<PageWidgets view={tab.view} idPrefix={`page-${tab.id}`} {revision} editing={pageEditing && tab.id === activeId}
          onview={next => { if (tab.id === activeId) navigate(next); }} />{/if}
      </div>
    {/each}
  </main>
</div>
{#if notifySources}<NotificationToasts onopen={openNotice} /><NotificationCenter onopen={openNotice} />{/if}
{#if workspace}<Rika onnavigate={next => { if (isView(next) && next !== view) navigate(next); }} />{/if}
{#if dockShown}<RikaDock onclose={() => void closeDock()} />{/if}
{#if overview}
  <TabOverview tabs={tabLabels} {activeId} onclose={() => (overview = false)}
    onselect={(id) => { overview = false; void selectTab(id); }} />
{/if}
{#if carousel}
  <ThemeCarousel themes={carouselThemes} current={getTheme(buildStore.build.theme.base).id}
    onpreview={(id) => previewLook({ ...buildStore.build.theme, base: id })}
    onkeep={(id) => { carousel = false; previewLook(null); void setTheme(id as Parameters<typeof setTheme>[0]); }}
    oncancel={() => { carousel = false; previewLook(null); }} />
{/if}
{#if buildStore.editorOpen}<BuildEditor onclose={() => (buildStore.editorOpen = false)} />{/if}
<PageEdit {view} tab={activeId} bind:editing={pageEditing} />
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
    onedit={togglePageEdit}
    onnewcard={studyTools ? () => (quickCard = { deck: null }) : undefined}
    onreload={() => void reload()}
    onterminal={terminal ? () => void openTerminal() : undefined}
    onrika={() => rika.showDrawer()}
    onsettings={openSettings}
    onnotifications={notifySources ? () => (notifier.drawer = true) : undefined}
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
<ModuleDownload />
