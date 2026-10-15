<script lang="ts">
  import { api, type Bootstrap } from "./api";
  import type { View } from "./workspace";
  import Icon from "./Icon.svelte";

  let {
    revision,
    workspace,
    onview,
  }: {
    revision: number;
    workspace: Bootstrap | null;
    onview: (view: View) => void;
  } = $props();
  let checked = $state(false);
  let hasDetails = $state(false);
  let resumeCount = $state(0);
  let error = $state("");
  $effect(() => {
    void revision;
    let cancelled = false;
    error = "";
    void Promise.all([
      api<{ profile: Record<string, unknown> }>("/profile"),
      api<{ resumes: { available: boolean }[] }>("/resumes/workflow"),
    ])
      .then(([profile, library]) => {
        if (cancelled) return;
        hasDetails = [profile.profile.name, profile.profile.email].every(
          (value) => typeof value === "string" && value.trim().length > 0,
        );
        resumeCount = library.resumes.filter(
          (resume) => resume.available,
        ).length;
        checked = true;
      })
      .catch((cause: Error) => {
        if (!cancelled) error = cause.message;
      });
    return () => {
      cancelled = true;
    };
  });
</script>

<section
  id="getting-started"
  class="getting-started"
  tabindex="-1"
  aria-labelledby="guide-title"
>
  <header class="guide-heading">
    <div>
      <p class="eyebrow">START HERE</p>
      <h2 id="guide-title">Three steps. One application at a time.</h2>
    </div>
    <span class="guide-reassurance"
      ><Icon name="shield" size={16} /> Nothing is sent without you.</span
    >
  </header>
  <div class="journey-grid">
    <article class="journey-card">
      <span class="step-number" aria-hidden="true">01</span>
      <h3>Set up your details</h3>
      <p>
        Add your contact details and a resume. kitty! reuses these to fill
        forms—no made-up answers.
      </p>
      <div class="setup-status" aria-live="polite">
        {#if checked}
          <span class:complete={hasDetails}
            ><span class="status-dot"></span>{hasDetails
              ? "Name & email saved"
              : "Add your name & email"}</span
          >
          <span class:complete={resumeCount > 0}
            ><span class="status-dot"></span>{resumeCount
              ? `${resumeCount} resume PDF${resumeCount === 1 ? "" : "s"} available`
              : "Upload a resume PDF"}</span
          >
        {:else if !error}<span>Checking your saved details…</span>{/if}
      </div>
      <div class="step-actions">
        <button class="button" onclick={() => onview("profile")}
          >{hasDetails ? "Review my profile" : "Set up my profile"}<Icon
            name="arrow"
            size={15}
          /></button
        >
        <button class="text-button" onclick={() => onview("resumes")}
          >{resumeCount ? "My resumes" : "Upload resume"}</button
        >
      </div>
    </article>
    <article class="journey-card">
      <span class="step-number" aria-hidden="true">02</span>
      <h3>Find & save jobs</h3>
      <p>
        Search for a role or company. Open a job to read it, or select several
        and save them together.
      </p>
      <div class="step-tip">
        <kbd>Ctrl F</kbd> Search · Select rows · Right-click to save
      </div>
      <div class="step-actions">
        <button class="button" onclick={() => onview("jobs")}
          >Browse jobs<Icon name="arrow" size={15} /></button
        >
        <button class="text-button" onclick={() => onview("favorites")}
          >Saved jobs{workspace
            ? ` (${workspace.counts.favorites})`
            : ""}</button
        >
      </div>
    </article>
    <article class="journey-card">
      <span class="step-number" aria-hidden="true">03</span>
      <h3>Prepare, review, apply</h3>
      <p>
        Choose your jobs and review the plan. kitty! fills supported forms in
        Firefox. Check every answer, then submit yourself.
      </p>
      <div class="step-tip">
        Missing answers, sign-ins and final submission stay with you.
      </div>
      <div class="step-actions">
        <button class="button" onclick={() => onview("queue")}
          >Prepare applications<Icon name="arrow" size={15} /></button
        >
        <button class="text-button" onclick={() => onview("pipeline")}
          >Track my applications</button
        >
      </div>
    </article>
  </div>
  {#if error}<p class="error-banner" role="alert">
      Couldn’t check your setup: {error} You can still open My profile and My resumes
      to review your details.
    </p>{/if}
</section>

<style>
  .getting-started {
    margin: 30px 0;
    scroll-margin-top: 24px;
  }
  .getting-started:focus {
    outline: 1px solid var(--accent);
    outline-offset: 8px;
  }
  .guide-heading {
    display: flex;
    align-items: end;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 18px;
  }
  .eyebrow {
    margin-bottom: 6px;
  }
  h2 {
    margin: 0;
    font-size: 26px;
    font-weight: 400;
  }
  .guide-reassurance {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font-size: 12px;
    white-space: nowrap;
    padding-bottom: 4px;
  }
  .journey-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px;
  }
  .journey-card {
    position: relative;
    display: flex;
    flex-direction: column;
    padding: 24px;
    background: linear-gradient(
      150deg,
      var(--surface-raised),
      var(--surface) 60%
    );
    border: 1px solid var(--border);
    border-top-color: var(--gold-border);
    border-radius: 3px;
  }
  .step-number {
    font:
      24px/1 Georgia,
      serif;
    color: var(--accent);
    margin-bottom: 18px;
  }
  .step-number::after {
    content: "";
    display: inline-block;
    width: 30px;
    height: 1px;
    margin: 0 0 7px 12px;
    background: var(--gold-border);
  }
  h3 {
    font-size: 23px;
    font-weight: 400;
    margin-bottom: 10px;
  }
  p {
    color: var(--muted);
    font-size: 13px;
    line-height: 1.8;
    margin-bottom: 14px;
  }
  .setup-status {
    display: grid;
    gap: 9px;
    font-size: 12px;
    color: var(--muted);
    margin: 2px 0 16px;
  }
  .setup-status > span {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .setup-status .status-dot {
    background: var(--warning);
  }
  .setup-status .complete .status-dot {
    background: var(--success);
  }
  .step-tip {
    font-size: 12px;
    color: var(--subtle);
    line-height: 1.8;
    margin-bottom: 16px;
  }
  .step-actions {
    display: flex;
    align-items: start;
    flex-direction: column;
    gap: 6px;
    margin-top: auto;
  }
  .step-actions .button {
    justify-content: space-between;
    width: 100%;
    text-align: left;
  }
  @media (max-width: 1150px) {
    .guide-heading {
      align-items: start;
      flex-direction: column;
      gap: 10px;
    }
    .journey-card {
      padding: 20px;
    }
  }
  @media (max-width: 950px) {
    .journey-grid {
      grid-template-columns: 1fr;
    }
    .journey-card {
      padding: 22px;
    }
    .step-number {
      position: absolute;
      right: 22px;
      top: 24px;
      opacity: 0.7;
    }
    .step-number::after {
      display: none;
    }
    h3 {
      padding-right: 32px;
    }
    .step-actions {
      flex-direction: row;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
    }
    .step-actions .button {
      width: auto;
      gap: 20px;
    }
    .setup-status {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  @media (max-width: 480px) {
    h2 {
      font-size: 24px;
    }
    .guide-reassurance {
      font-size: 11px;
      white-space: normal;
    }
    .setup-status {
      grid-template-columns: 1fr;
    }
  }
</style>
