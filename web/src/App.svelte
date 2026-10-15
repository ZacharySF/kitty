<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api, type Bootstrap } from "./lib/api";
  import { navigation, navigationGroups, type View } from "./lib/workspace";
  import { Badge, Button } from "@nunera/bracket";
  import Icon from "./lib/Icon.svelte";
  import JobBrowser from "./lib/JobBrowser.svelte";
  import GettingStarted from "./lib/GettingStarted.svelte";
  import Pipeline from "./lib/Pipeline.svelte";
  import Reminders from "./lib/Reminders.svelte";
  import Library from "./lib/Library.svelte";
  import Sources from "./lib/Sources.svelte";
  import Profile from "./lib/Profile.svelte";
  import Insights from "./lib/Insights.svelte";
  import Questions from "./lib/Questions.svelte";
  import AddJob from "./lib/AddJob.svelte";
  import CommandPalette from "./lib/CommandPalette.svelte";
  import KeyboardHelp from "./lib/KeyboardHelp.svelte";
  import Calendar from "./lib/Calendar.svelte";
  import CalendarReminders from "./lib/CalendarReminders.svelte";
  import Opportunities from "./lib/Opportunities.svelte";
  import Study from "./lib/Study.svelte";
  import StudyFocusBar from "./lib/StudyFocusBar.svelte";
  import Flashcards from "./lib/Flashcards.svelte";
  import Notes from "./lib/Notes.svelte";
  import AI from "./lib/AI.svelte";
  import GraphNavigator from "./lib/GraphNavigator.svelte";
  import SchemaMaps from "./lib/SchemaMaps.svelte";
  import Kanban from "./lib/Kanban.svelte";

  const headings: Record<
    Exclude<View, "today">,
    { title: string; subtitle: string }
  > = {
    calendar: {
      title: "A little space for everything.",
      subtitle:
        "Plan events and tasks locally. Connect Google only when you choose to.",
    },
    kanban: {
      title: "See the next step.",
      subtitle:
        "Organize tasks into boards. Move cards, keep checklists, and choose what to work on next.",
    },
    opportunities: {
      title: "Find your next possibility.",
      subtitle:
        "Monitor opportunities, scholarships, competitions, and projects. Keep the ones that matter.",
    },
    study: {
      title: "One focused session at a time.",
      subtitle:
        "Turn subjects, goals, and deadlines into a study plan you can actually follow.",
    },
    flashcards: {
      title: "Make knowledge stick.",
      subtitle:
        "Create cards, review what is due, and build a lasting learning habit.",
    },
    notes: {
      title: "Room to think out loud.",
      subtitle: "Write notes and connect your ideas on an infinite canvas.",
    },
    schema: {
      title: "See how things connect.",
      subtitle:
        "Link classes, topics, ideas, jobs, and skills in study, general, or job-focused maps.",
    },
    ai: {
      title: "AI, on your terms.",
      subtitle:
        "Configure OpenRouter, review what is shared, and approve costs before any external request.",
    },
    inbox: {
      title: "New jobs",
      subtitle:
        "Jobs discovered in the last 14 days that you haven’t reviewed yet. Open one to see the details, or save it for later.",
    },
    jobs: {
      title: "Find a job worth applying to.",
      subtitle:
        "Search by role or company. Open a job to learn more, or select several to save them together.",
    },
    queue: {
      title: "Prepare your applications.",
      subtitle:
        "Choose jobs below. kitty! fills what it knows in Firefox; you check the answers and press Submit.",
    },
    pipeline: {
      title: "My applications",
      subtitle:
        "See where each application stands. Open a card to record a reply, add a note, or plan a follow-up.",
    },
    applications: {
      title: "Application list",
      subtitle:
        "Search the applications you’ve already submitted and update their progress.",
    },
    favorites: {
      title: "Your saved jobs",
      subtitle:
        "Your shortlist. Choose the jobs you want, then prepare their application forms.",
    },
    reminders: {
      title: "Reminders & follow-ups",
      subtitle:
        "Keep track of deadlines and when to check back with an employer.",
    },
    resumes: {
      title: "My resumes",
      subtitle:
        "Upload a PDF you’re happy to send. You’ll review which resume is used before any form is filled.",
    },
    sources: {
      title: "Where your jobs come from",
      subtitle:
        "Check your job boards and collect new postings. No applications are sent from this screen.",
    },
    profile: {
      title: "Your application profile.",
      subtitle:
        "Save your real details once, then reuse them on application forms. Leave anything you’re unsure about blank.",
    },
    finder: {
      title: "Find more companies",
      subtitle:
        "Discover employers, review their websites, and choose which ones to follow for new jobs.",
    },
    reports: {
      title: "Reports & exports",
      subtitle: "See your application results or download your data.",
    },
    mail: {
      title: "Review email updates",
      subtitle:
        "Match employer replies to your applications. Nothing changes until you approve the suggested update.",
    },
    triage: {
      title: "Rank your jobs",
      subtitle:
        "Sort jobs by how well they fit your profile. Paid AI ranking always needs your approval and a spending limit.",
    },
    questions: {
      title: "Application questions",
      subtitle:
        "Save answers in your own words so kitty! can reuse them. Unanswered questions stay blank.",
    },
  };
  let view = $state<View>("today");
  let workspace = $state<Bootstrap | null>(null);
  let error = $state("");
  let loading = $state(true);
  let palette = $state(false);
  let help = $state(false);
  let guide = $state(false);
  let addJob = $state(false);
  let revision = $state(0);
  let epoch = $state(0);
  let initialJobId = $state<number | null>(null);
  let guideDialog = $state<HTMLDialogElement>();
  let atlas: { focusSearch: () => void } | undefined = $state();
  let jobBrowser:
    { handleKey: (event: KeyboardEvent) => Promise<boolean> } | undefined =
    $state();
  let pipeline:
    { handleKey: (event: KeyboardEvent) => Promise<boolean> } | undefined =
    $state();
  let study:
    { handleKey: (event: KeyboardEvent) => Promise<boolean> } | undefined =
    $state();
  let profile: { hasUnsavedChanges: () => boolean } | undefined = $state();
  function mayLeaveProfile() {
    return (
      view !== "profile" ||
      !profile?.hasUnsavedChanges() ||
      window.confirm(
        "You have unsaved profile changes. Leave this page and discard them?",
      )
    );
  }
  const title = $derived(
    navigation.find((item) => item.id === view)?.label || "Workspace",
  );
  const heading = $derived(view === "today" ? null : headings[view]);
  const date = new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  }).format(new Date());
  const sectionGroup = $derived(
    navigationGroups.find((group) => group.views.includes(view))?.label ??
      "Workspace",
  );
  function studyText(event: Event) {
    const detail = (event as CustomEvent).detail;
    const next: View =
      detail?.purpose === "flashcards" ? "flashcards" : "study";
    if (view === next || detail?.routed || !navigate(next)) return;
    void tick().then(() =>
      window.dispatchEvent(
        new CustomEvent("kitty-study-text", {
          detail: { ...detail, routed: true },
        }),
      ),
    );
  }

  function showGuide() {
    guide = true;
  }
  $effect(() => {
    if (guide && guideDialog && !guideDialog.open) guideDialog.showModal();
  });

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
    } finally {
      if (request === bootstrapRequest) loading = false;
    }
  }
  async function reload() {
    if (!mayLeaveProfile()) return;
    loading = true;
    try {
      await api("/reload", {});
      await refresh();
      epoch += 1;
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      loading = false;
    }
  }
  function navigate(next: View) {
    if (!mayLeaveProfile()) return false;
    view = next;
    initialJobId = null;
    epoch += 1;
    window.history.pushState(null, "", `#${next}`);
    void refresh();
    void tick().then(() => {
      document.getElementById("main-content")?.focus({ preventScroll: true });
      window.scrollTo(0, 0);
    });
    return true;
  }
  function openJob(id: number) {
    if (navigate("jobs")) initialJobId = id;
  }
  function readRoute() {
    const route = window.location.hash.slice(1);
    const next = navigation.find((item) => item.id === route)?.id || "today";
    if (!mayLeaveProfile()) {
      window.history.pushState(null, "", `#${view}`);
      return;
    }
    view = next;
    initialJobId = null;
    epoch += 1;
    void refresh();
  }
  onMount(() => {
    readRoute();
    window.addEventListener("kitty-study-text", studyText);
    return () => window.removeEventListener("kitty-study-text", studyText);
  });

  async function keyboard(event: KeyboardEvent) {
    if (
      event.defaultPrevented ||
      document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')
    )
      return;
    const target = event.target as HTMLElement | null;
    const typing = !!target?.closest(
      'input,textarea,select,[contenteditable="true"]',
    );
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      palette = true;
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
      if (
        typing &&
        !["inbox", "jobs", "queue", "applications", "favorites"].includes(view)
      )
        return;
      event.preventDefault();
      if (
        !["inbox", "jobs", "queue", "applications", "favorites"].includes(view)
      ) {
        navigate("jobs");
        await tick();
      }
      await jobBrowser?.handleKey(event);
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Escape" && typing) {
      target?.blur();
      event.preventDefault();
      return;
    }
    if (typing) return;
    const destination = navigation.find(
      (item) => item.key === event.key && item.key,
    );
    if (destination) {
      event.preventDefault();
      navigate(destination.id);
      return;
    }
    if (event.key === "?") {
      event.preventDefault();
      help = true;
      return;
    }
    const control = target?.closest("button,a,summary");
    if (
      control &&
      !control.matches(".job-row,[data-pipeline]") &&
      ["Enter", " "].includes(event.key)
    )
      return;
    if (view === "today") {
      if (event.key === "/") {
        event.preventDefault();
        atlas?.focusSearch();
      }
      return;
    }
    if (view === "pipeline") {
      await pipeline?.handleKey(event);
      return;
    }
    if (view === "study") {
      await study?.handleKey(event);
      return;
    }
    if (["inbox", "jobs", "queue", "applications", "favorites"].includes(view))
      await jobBrowser?.handleKey(event);
  }
</script>

<svelte:window onpopstate={readRoute} onkeydown={keyboard} />
<svelte:head><title>{title} · kitty!</title></svelte:head>
<a href="#main-content" class="skip-link">Skip to content</a>
<div class="workbench" class:atlas-home={view === "today"}>
  <header class="system-bar">
    <span class="wordmark"
      ><span class="brand-mark" aria-hidden="true"
        ><Icon name="kitty" size={22} /></span
      ><span>kitty<span class="brand-dot">!</span></span></span
    >
    <a
      href="#today"
      class="atlas-link"
      aria-current={view === "today" ? "page" : undefined}
      title="Atlas home (F1)"
      onclick={(event) => {
        event.preventDefault();
        navigate("today");
      }}><span aria-hidden="true">◉</span> atlas</a
    >
    <nav class="breadcrumb" aria-label="Breadcrumb">
      <span class="separator" aria-hidden="true">/</span>
      {#if view === "today"}<span>overview</span>{:else}<span
          >{sectionGroup}</span
        ><span class="separator" aria-hidden="true">/</span><strong
          aria-current="page">{title}</strong
        >{/if}
    </nav>
    <div class="system-state">
      <Badge
        label={workspace?.demo ? "DEMO WORKSPACE" : "LOCAL WORKSPACE"}
        variant="dot"
        intent="success"
        size="sm"
      />
    </div>
    <button
      class="system-command"
      onclick={() => (palette = true)}
      aria-label="Search pages, jobs and commands"
      ><Icon name="jobs" size={15} /><span>Search anything</span><kbd
        >Ctrl K</kbd
      ></button
    >
    <button class="text-button help-link" onclick={showGuide}
      >How it works</button
    >
    <span class="system-date">{date}</span>
    <Button
      variant="ghost"
      size="sm"
      aria-label="Refresh workspace and configuration"
      title="Reload configuration and refresh"
      disabled={loading}
      onclick={reload}><Icon name="refresh" size={17} /></Button
    >
  </header>
  <StudyFocusBar {revision} demo={workspace?.demo ?? true} />
  <CalendarReminders />
  <main id="main-content" tabindex="-1" class:atlas-main={view === "today"}>
    {#if workspace?.demo}<div class="demo-notice">
        <Badge label="DEMO" intent="warning" size="sm" />Synthetic workspace.
        Local changes work; external actions are disabled.
      </div>{/if}
    {#if error}<div class="error-banner" role="alert">
        {error}<button class="button" onclick={refresh}>Try again</button>
      </div>{/if}
    {#if view === "today"}
      <GraphNavigator
        bind:this={atlas}
        {workspace}
        {revision}
        onview={navigate}
        onopenjob={openJob}
        onguide={showGuide}
      />
    {:else if heading}
      <header class="page-heading">
        <div>
          <p class="eyebrow">{sectionGroup}</p>
          <h1 id="page-title">{heading.title}</h1>
          <p class="muted">{heading.subtitle}</p>
        </div>
        {#if view === "applications"}<a
            class="button"
            href="/api/reports/export?format=csv&dataset=applications"
            download><Icon name="download" size={16} />Export CSV</a
          >{/if}
      </header>
      {#key `${view}:${epoch}:${initialJobId}`}
        {#if view === "calendar"}<Calendar
            {revision}
            demo={workspace?.demo ?? true}
          />
        {:else if view === "kanban"}<Kanban
            {revision}
            demo={workspace?.demo ?? true}
          />
        {:else if view === "schema"}<SchemaMaps
            {revision}
            demo={workspace?.demo ?? true}
          />
        {:else if view === "opportunities"}<Opportunities
            {revision}
            demo={workspace?.demo ?? true}
          />
        {:else if view === "study"}<Study
            bind:this={study}
            {revision}
            demo={workspace?.demo ?? true}
          />
        {:else if view === "flashcards"}<Flashcards
            {revision}
            demo={workspace?.demo ?? true}
          />
        {:else if view === "notes"}<Notes
            {revision}
            demo={workspace?.demo ?? true}
          />
        {:else if view === "ai"}<AI {revision} demo={workspace?.demo ?? true} />
        {:else if view === "inbox" || view === "jobs" || view === "queue" || view === "applications" || view === "favorites"}<JobBrowser
            bind:this={jobBrowser}
            {view}
            {workspace}
            {initialJobId}
            onchange={refresh}
            onadd={() => (addJob = true)}
            onview={navigate}
          />
        {:else if view === "pipeline"}<Pipeline
            bind:this={pipeline}
            {workspace}
            onchange={refresh}
            onmail={() => navigate("mail")}
          />
        {:else if view === "reminders"}<Reminders
            onopenjob={openJob}
            onchange={refresh}
          />
        {:else if view === "resumes"}<Library
            demo={workspace?.demo ?? true}
            onopenjob={openJob}
          />
        {:else if view === "sources"}<Sources demo={workspace?.demo ?? true} />
        {:else if view === "profile"}<Profile
            bind:this={profile}
            demo={workspace?.demo ?? true}
            onresume={() => navigate("resumes")}
          />
        {:else if view === "finder" || view === "reports" || view === "mail" || view === "triage"}<Insights
            section={view}
            demo={workspace?.demo ?? true}
          />
        {:else if view === "questions"}<Questions
            demo={workspace?.demo ?? true}
          />{/if}
      {/key}
      <footer class="page-footer">
        <span
          ><span class="status-dot"></span>{workspace?.demo
            ? "Temporary demo data"
            : "Stored locally, on your terms"}</span
        ><span>Need a shortcut? Press <kbd>?</kbd> for keyboard help.</span>
      </footer>
    {/if}
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
