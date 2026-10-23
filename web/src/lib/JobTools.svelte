<script lang="ts">
  import { tick } from "svelte";
  import { api, dateLabel, safeUrl, type Bootstrap } from "./api";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  let {
    id,
    revision = 0,
    initialCommand = "",
    onchange,
  }: {
    id: number;
    revision?: number;
    initialCommand?: string;
    onchange: () => Promise<void>;
  } = $props();
  const pane = useWorkspaceContext();
  const fieldId = (name: string) => `${pane.id}-job-${id}-${name}`;
  type Reminder = {
    id: number;
    due_at: string;
    note: string | null;
    done_at: string | null;
  };
  type Workflow = {
    tier: string | null;
    queue_state: string;
    deadline_at: string | null;
    tracks: string[];
    eligibility: string;
    apply_url: string;
    sources: {
      source: string;
      source_url: string | null;
      first_seen_at: string;
      last_seen_at: string;
    }[];
    evidence: Record<string, unknown>;
    freshness: string;
    application: {
      next_action_at: string | null;
      next_action_note: string | null;
      applied_at: string;
      track: string | null;
    } | null;
    can_undo: boolean;
    followups: Reminder[];
  };
  let data = $state<Workflow | null>(null);
  let tracks = $state<NonNullable<Bootstrap["tracks"]>>([]);
  let tier = $state("");
  let queueState = $state("new");
  let deadline = $state("");
  let nextDate = $state("");
  let nextNote = $state("");
  let chosenTracks = $state<string[]>([]);
  let error = $state("");
  let message = $state("");
  let busy = $state(false);
  let handledInitialCommand = false;
  let toolsDisclosure: HTMLDetailsElement | undefined = $state();
  const progressLabels: Record<string, string> = {
    new: "Not reviewed",
    queued: "In preparation queue",
    later: "Review later",
    skipped: "Skipped",
    not_eligible: "Not eligible",
  };
  $effect(() => {
    const jobId = id;
    void revision;
    let cancelled = false;
    void Promise.all([
      api<Workflow>(`/job-tools/${jobId}`),
      api<Bootstrap>("/bootstrap"),
    ])
      .then(async ([workflow, workspace]) => {
        if (cancelled) return;
        data = workflow;
        tracks = workspace.tracks ?? [];
        tier = workflow.tier || "";
        queueState = workflow.queue_state;
        deadline = workflow.deadline_at?.slice(0, 10) || "";
        nextDate = workflow.application?.next_action_at?.slice(0, 10) || "";
        nextNote = workflow.application?.next_action_note || "";
        chosenTracks = workflow.tracks;
        if (!handledInitialCommand && initialCommand) {
          handledInitialCommand = true;
          await tick();
          const fields: Record<string, string> = {
            t: "job-tier",
            k: "job-state",
            d: "job-deadline",
            n: "next-action-date",
          };
          if (initialCommand === "u") await undo();
          else if (fields[initialCommand]) {
            if (toolsDisclosure) toolsDisclosure.open = true;
            await tick();
            if (pane.active) document.getElementById(fieldId(fields[initialCommand]))?.focus();
          }
        }
      })
      .catch((cause) => {
        if (!cancelled) error = cause.message;
      });
    return () => {
      cancelled = true;
    };
  });
  async function save(action: string, value?: unknown, note?: string) {
    busy = true;
    error = "";
    message = "";
    try {
      if (["tier", "state", "not_eligible"].includes(action))
        await api("/jobs/bulk", { ids: [id], action, value });
      else await api(`/job-tools/${id}`, { action, value, note });
      await onchange();
      message = "Job details saved.";
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
  export async function undo() {
    if (toolsDisclosure) toolsDisclosure.open = true;
    await save("undo");
  }
</script>

<details class="job-tools" bind:this={toolsDisclosure}>
  <summary>Job settings, dates &amp; source details</summary>
  {#if error}<p role="alert" class="error-banner">{error}</p>{/if}
  {#if message}<p role="status" class="muted">{message}</p>{/if}
  {#if data}
    <h3>Your review decisions</h3>
    <form
      onsubmit={(event) => {
        event.preventDefault();
        void save("tier", tier || null);
      }}
    >
      <label for={fieldId("job-tier")}>Priority rank <kbd>t</kbd></label>
      <div class="inline">
        <select id={fieldId("job-tier")} bind:value={tier}
          ><option value="">Not ranked</option><option value="A"
            >A — Highest priority</option
          ><option value="B">B — Worth considering</option><option value="C"
            >C — Lower priority / filtered out</option
          ></select
        ><button class="button" disabled={busy}>Save rank</button>
      </div>
      <small class="muted"
        >Ranks guide what to review first, not whether you are guaranteed to
        qualify. Check the posting requirements.</small
      >
    </form>
    {#if !data.application}<form
        onsubmit={(event) => {
          event.preventDefault();
          void save("state", queueState);
        }}
      >
        <label for={fieldId("job-state")}>Preparation progress <kbd>k</kbd></label>
        <div class="inline">
          <select id={fieldId("job-state")} bind:value={queueState}
            >{#each ["new", "queued", "later", "skipped", "not_eligible"] as item}<option
                value={item}>{progressLabels[item]}</option
              >{/each}</select
          ><button class="button" disabled={busy}>Move</button>
        </div>
      </form>
      <button
        class="button"
        disabled={busy}
        onclick={() => save("not_eligible")}>Mark not eligible</button
      >{/if}
    <form
      onsubmit={(event) => {
        event.preventDefault();
        void save("deadline", deadline);
      }}
    >
      <label for={fieldId("job-deadline")}>Your deadline <kbd>d</kbd></label>
      <div class="inline">
        <input
          id={fieldId("job-deadline")}
          bind:value={deadline}
          placeholder="YYYY-MM-DD, +7, or blank"
        /><button class="button" disabled={busy}>Save</button>
      </div>
      <small class="muted"
        >Leave blank to use the posting’s deadline, if available.</small
      >
    </form>
    <form
      onsubmit={(event) => {
        event.preventDefault();
        void save("tracks", chosenTracks);
      }}
    >
      <fieldset>
        <legend>Job categories</legend>{#each tracks as track}<label
            class="check"
            ><input
              type="checkbox"
              bind:group={chosenTracks}
              value={track.name}
            />{track.label}</label
          >{/each}
      </fieldset>
      <button class="button" disabled={busy}>Save categories</button>
    </form>
    {#if data.application}
      <h3>Next action</h3>
      <p class="muted">
        Submitted {dateLabel(data.application.applied_at)} · {data.application
          .track || "No application category"}
      </p>
      <form
        onsubmit={(event) => {
          event.preventDefault();
          void save("next_action", nextDate, nextNote);
        }}
      >
        <label for={fieldId("next-action-date")}>When <kbd>n</kbd></label><input
          id={fieldId("next-action-date")}
          bind:value={nextDate}
          placeholder="YYYY-MM-DD, +7, or blank"
        /><label for={fieldId("next-action-note")}>What needs doing</label><input
          id={fieldId("next-action-note")}
          bind:value={nextNote}
          placeholder="Follow up with recruiter"
        /><button class="button" disabled={busy}>Save next action</button>
      </form>
      <button class="button" disabled={busy || !data.can_undo} onclick={undo}
        >Undo latest status <kbd>u</kbd></button
      >
      {#if data.followups.length}<h3>Scheduled follow-ups</h3>
        <ul>
          {#each data.followups as item}<li>
              <span>{dateLabel(item.due_at)} · {item.note}</span
              >{#if item.done_at}<span class="badge">Done</span>{:else}<button
                  class="button"
                  disabled={busy}
                  onclick={async () => {
                    busy = true;
                    try {
                      await api(`/reminders/${item.id}/complete`, {});
                      await onchange();
                    } catch (cause) {
                      error = (cause as Error).message;
                    } finally {
                      busy = false;
                    }
                  }}>Complete</button
                >{/if}
            </li>{/each}
        </ul>{/if}
    {/if}
    <details>
      <summary>Where this posting came from &amp; verification details</summary>
      <p>Freshness: <span class="badge">{data.freshness}</span></p>
      {#if safeUrl(data.apply_url)}<a
          href={safeUrl(data.apply_url)}
          target="_blank"
          rel="noopener noreferrer">Application destination ↗</a
        >{/if}
      <dl>
        {#each Object.entries(data.evidence).filter(([, value]) => value !== null && value !== "" && (!Array.isArray(value) || value.length)) as [field, value]}<div
          >
            <dt>{field.replaceAll("_", " ")}</dt>
            <dd>{Array.isArray(value) ? value.join(" · ") : String(value)}</dd>
          </div>{/each}
      </dl>
      {#each data.sources as source}<article>
          <strong>{source.source}</strong>
          <p class="muted">
            First seen {dateLabel(source.first_seen_at)} · last seen {dateLabel(
              source.last_seen_at,
            )}
          </p>
          {#if source.source_url && safeUrl(source.source_url)}<a
              href={safeUrl(source.source_url)}
              target="_blank"
              rel="noopener noreferrer">Source posting ↗</a
            >{/if}
        </article>{/each}
    </details>
  {/if}
</details>

<style>
  .job-tools {
    border-top: 1px solid var(--border);
    margin-top: 24px;
    padding-top: 24px;
    font-size: 12px;
  }
  h3 {
    font-size: 14px;
    margin: 22px 0 12px;
  }
  h3:first-child {
    margin-top: 0;
  }
  form {
    display: grid;
    gap: 8px;
    margin: 14px 0;
  }
  label {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    color: var(--muted);
  }
  .inline {
    display: flex;
    gap: 8px;
  }
  .inline input,
  .inline select {
    flex: 1;
    min-width: 0;
  }
  input {
    min-width: 0;
    width: 100%;
  }
  fieldset {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    border: 1px solid var(--border);
    border-radius: 7px;
    padding: 12px;
  }
  legend {
    color: var(--muted);
  }
  .check {
    justify-content: start;
    align-items: center;
  }
  .check input {
    width: auto;
  }
  small {
    line-height: 1.6;
  }
  details {
    margin-top: 24px;
    border-top: 1px solid var(--border);
    padding-top: 18px;
  }
  summary {
    cursor: pointer;
    margin-bottom: 16px;
  }
  ul {
    list-style: none;
    padding: 0;
  }
  li {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 8px 0;
  }
  dl {
    display: grid;
    gap: 12px;
  }
  dt {
    color: var(--muted);
    text-transform: capitalize;
  }
  dd {
    margin: 4px 0 0;
    overflow-wrap: anywhere;
    line-height: 1.7;
  }
  article {
    border-top: 1px solid var(--border);
    padding: 12px 0;
  }
  article p {
    margin: 8px 0;
  }
</style>
