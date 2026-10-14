<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api, type Bootstrap } from "./lib/api";
  import { navigation, type View } from "./lib/workspace";
  import Icon from "./lib/Icon.svelte";
  import JobBrowser from "./lib/JobBrowser.svelte";
  import Dashboard from "./lib/Dashboard.svelte";
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

  const headings: Record<View, { title: string; subtitle: string }> = {
    today: {
      title: "Your next role starts here.",
      subtitle:
        "Find jobs you want. Prepare the forms with your saved details. Review and submit when you’re ready.",
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
        "Choose jobs below. ApplyAI fills what it knows in Firefox; you check the answers and press Submit.",
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
      title: "Tell ApplyAI about yourself.",
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
        "Save answers in your own words so ApplyAI can reuse them. Unanswered questions stay blank.",
    },
  };
  const primaryViews: View[] = [
    "today",
    "jobs",
    "favorites",
    "queue",
    "pipeline",
  ];
  const setupViews: View[] = ["profile", "resumes"];
  const primaryNav = primaryViews.map((id) =>
    navigation.find((item) => item.id === id)!,
  );
  const setupNav = setupViews.map((id) =>
    navigation.find((item) => item.id === id)!,
  );
  const toolNav = navigation.filter(
    (item) => !primaryViews.includes(item.id) && !setupViews.includes(item.id),
  );
  let toolsOpen = $state(false);
  let view = $state<View>("today");
  let workspace = $state<Bootstrap | null>(null);
  let error = $state("");
  let loading = $state(true);
  let mobileNav = $state(false);
  let palette = $state(false);
  let help = $state(false);
  let addJob = $state(false);
  let revision = $state(0);
  let epoch = $state(0);
  let initialJobId = $state<number | null>(null);
  let jobBrowser:
    { handleKey: (event: KeyboardEvent) => Promise<boolean> } | undefined =
    $state();
  let pipeline:
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
  const date = new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(new Date());
  $effect(() => {
    if (toolNav.some((item) => item.id === view)) toolsOpen = true;
  });

  async function showGuide() {
    if (!navigate("today")) return;
    await tick();
    document.getElementById("getting-started")?.focus();
    document
      .getElementById("getting-started")
      ?.scrollIntoView({ block: "start" });
  }

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
    mobileNav = false;
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
    mobileNav = false;
    epoch += 1;
    void refresh();
  }
  onMount(() => {
    readRoute();
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
    if (event.key === "Escape" && mobileNav) {
      mobileNav = false;
      return;
    }
    const control = target?.closest("button,a,summary");
    if (
      control &&
      !control.matches(".job-row,[data-pipeline]") &&
      ["Enter", " "].includes(event.key)
    )
      return;
    if (view === "pipeline") {
      await pipeline?.handleKey(event);
      return;
    }
    if (
      ["today", "inbox", "jobs", "queue", "applications", "favorites"].includes(
        view,
      )
    ) {
      if (view === "today" && event.key === "/") {
        event.preventDefault();
        navigate("jobs");
        await tick();
      }
      await jobBrowser?.handleKey(event);
    }
  }
</script>

<svelte:window onpopstate={readRoute} onkeydown={keyboard} />
<svelte:head><title>{title} · ApplyAI</title></svelte:head>
<a href="#main-content" class="skip-link">Skip to content</a>
<div class="workbench">
  <header class="system-bar">
    <a
      href="#today"
      class="wordmark"
      onclick={(event) => {
        event.preventDefault();
        navigate("today");
      }}
      aria-label="ApplyAI home"
      ><span class="brand-mark">a<span>i</span></span><span
        >apply<span class="brand-light">ai</span><span class="brand-dot">.</span
        ></span
      ></a
    >
    <div class="system-location">
      <span class="separator">/</span> your job search
    </div>
    <div class="system-state">
      <span class="status-dot"></span>{workspace?.demo
        ? "DEMO WORKSPACE"
        : "LOCAL WORKSPACE"}
    </div>
    <button
      class="system-command"
      onclick={() => (palette = true)}
      aria-label="Search pages, jobs and commands"
      ><Icon name="jobs" size={15} /><span>Search anything</span><kbd
        >Ctrl K</kbd
      ></button
    >
  </header>
  <aside
    class:mobile-open={mobileNav}
    class="sidebar"
    id="workspace-nav"
    aria-label="Workspace navigation"
  >
    <div class="workspace-label">
      <span class="workspace-monogram"><Icon name="moon" size={19} /></span>
      <div>
        <strong>Your next chapter</strong><small>One step at a time.</small>
      </div>
    </div>
    <nav aria-label="Main navigation">
      <p class="nav-heading">YOUR JOB SEARCH</p>
      {#each primaryNav as item}
        <a
          href={`#${item.id}`}
          class:active={view === item.id}
          aria-current={view === item.id ? "page" : undefined}
          onclick={(event) => {
            event.preventDefault();
            navigate(item.id);
          }}
        >
          <Icon name={item.icon} size={18} /><span>{item.label}</span>
          {#if item.id === "favorites" && workspace?.counts.favorites}<span
              class="nav-count">{workspace.counts.favorites}</span
            >{/if}
        </a>
      {/each}
      <p class="nav-heading">SET UP ONCE</p>
      {#each setupNav as item}
        <a
          href={`#${item.id}`}
          class:active={view === item.id}
          aria-current={view === item.id ? "page" : undefined}
          onclick={(event) => {
            event.preventDefault();
            navigate(item.id);
          }}
        >
          <Icon name={item.icon} size={18} /><span>{item.label}</span>
        </a>
      {/each}
      <details class="more-tools" bind:open={toolsOpen}>
        <summary>More tools <span>Reports, sources & settings</span></summary>
        {#each toolNav as item}
          <a
            href={`#${item.id}`}
            class:active={view === item.id}
            aria-current={view === item.id ? "page" : undefined}
            onclick={(event) => {
              event.preventDefault();
              navigate(item.id);
            }}
          >
            <Icon name={item.icon} size={18} /><span>{item.label}</span>
          </a>
        {/each}
      </details>
    </nav>
    <div class="sidebar-bottom">
      <button class="shortcut-button guide-button" onclick={showGuide}>
        <Icon name="questions" size={17} /> How to apply
      </button>
      <button class="shortcut-button" onclick={() => (help = true)}
        ><kbd>?</kbd> Keyboard shortcuts</button
      >
      <div class="privacy-note">
        <Icon name="shield" size={18} />
        <div>
          <strong>You’re in control.</strong>
          <p>ApplyAI fills the forms.<br />Only you can submit them.</p>
        </div>
      </div>
    </div>
  </aside>
  <div class="main-workspace">
    <div class="topbar">
      <div class="breadcrumb">
        <button
          class="button mobile-menu"
          aria-label="Toggle navigation"
          aria-expanded={mobileNav}
          aria-controls="workspace-nav"
          onclick={() => (mobileNav = !mobileNav)}
          ><Icon name="menu" size={17} />Menu</button
        ><span>Workspace</span><span class="separator">/</span><strong
          >{title}</strong
        >
      </div>
      <div class="topbar-right">
        <button class="text-button help-link" onclick={showGuide}
          >How it works</button
        ><span>{date}</span><button
          class="icon-button"
          aria-label="Refresh workspace and configuration"
          title="Reload configuration and refresh"
          disabled={loading}
          onclick={reload}><Icon name="refresh" size={17} /></button
        >
      </div>
    </div>
    <main id="main-content" tabindex="-1">
      {#if workspace?.demo}<div class="demo-notice">
          <span class="badge">DEMO</span>Synthetic opportunities. Changes stay
          in this temporary workspace; external actions are disabled.
        </div>{/if}
      {#if view === "today"}<section
          class="hero panel"
          aria-labelledby="page-title"
        >
          <div class="hero-art" aria-hidden="true"></div>
          <div class="hero-content">
            <p class="eyebrow">
              <span class="small-line"></span>YOUR JOB SEARCH, SIMPLIFIED
            </p>
            <h1 id="page-title">
              Your next role<br />starts here<span>.</span>
            </h1>
            <p>{headings.today.subtitle}</p>
            <button class="button primary" onclick={() => navigate("jobs")}
              >Find jobs <Icon name="arrow" size={17} /></button
            >
            <button class="hero-help text-button" onclick={showGuide}
              >First time? Start here</button
            >
          </div>
        </section>{:else}<header class="page-heading">
          <div>
            <p class="eyebrow">
              {setupViews.includes(view) ? "SET UP ONCE" : "YOUR JOB SEARCH"}
            </p>
            <h1 id="page-title">{headings[view].title}</h1>
            <p class="muted">{headings[view].subtitle}</p>
          </div>
          {#if view === "applications"}<a
              class="button"
              href="/api/reports/export?format=csv&dataset=applications"
              download><Icon name="download" size={16} />Export CSV</a
            >{/if}
        </header>{/if}
      {#if view === "today"}<GettingStarted
          {revision}
          {workspace}
          onview={navigate}
        />{/if}
      {#if workspace && view === "today"}<section
          class="stats-grid"
          aria-label="Workspace totals"
        >
          {#each [{ label: "Jobs to explore", value: workspace.counts.jobs, icon: "jobs", next: "jobs" as View, note: "Browse open roles" }, { label: "In your queue", value: workspace.counts.queued, icon: "queue", next: "queue" as View, note: "Review before applying" }, { label: "Applications", value: workspace.counts.applications, icon: "applications", next: "pipeline" as View, note: `${workspace.weekly_total} sent this week` }, { label: "Saved jobs", value: workspace.counts.favorites, icon: "favorites", next: "favorites" as View, note: "Return to your shortlist" }] as stat}<button
              class="stat-card panel"
              onclick={() => navigate(stat.next)}
              ><span class="stat-label"
                >{stat.label}<Icon name={stat.icon} size={18} /></span
              ><strong>{stat.value.toLocaleString()}</strong><span
                class="stat-note"
                >{stat.note}<Icon name="arrow" size={14} /></span
              ></button
            >{/each}
        </section>{/if}
      {#if error}<div class="error-banner" role="alert">
          {error}<button class="button" onclick={refresh}>Try again</button>
        </div>{/if}
      {#key `${view}:${epoch}:${initialJobId}`}
        {#if view === "today"}<div class="today-layout">
            <JobBrowser
              bind:this={jobBrowser}
              {view}
              {workspace}
              onchange={refresh}
              onadd={() => (addJob = true)}
              onview={navigate}
              onopenjob={openJob}
            /><Dashboard {revision} onopenjob={openJob} onview={navigate} />
          </div>
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
    </main>
  </div>
</div>
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
