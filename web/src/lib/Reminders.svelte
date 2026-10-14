<script lang="ts">
  import { onMount } from "svelte";
  import { api, dateLabel } from "./api";
  let {
    onopenjob,
    onchange,
  }: { onopenjob: (id: number) => void; onchange: () => Promise<void> } =
    $props();
  type Reminder = {
    id: number;
    kind: string;
    due_at: string;
    note: string | null;
    job_id: number | null;
    label: string;
  };
  let data = $state<{ due: Reminder[]; upcoming: Reminder[] }>({
    due: [],
    upcoming: [],
  });
  let loading = $state(true);
  let busy = $state(false);
  let error = $state("");
  let date = $state("");
  let note = $state("");
  async function load() {
    loading = true;
    try {
      data = await api("/reminders");
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      loading = false;
    }
  }
  onMount(() => {
    void load();
  });
  async function create(event: SubmitEvent) {
    event.preventDefault();
    busy = true;
    error = "";
    try {
      await api("/reminders", { date, note });
      date = "";
      note = "";
      await load();
      await onchange();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
  async function complete(id: number) {
    busy = true;
    error = "";
    try {
      await api(`/reminders/${id}/complete`, {});
      await load();
      await onchange();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<section class="panel">
  <header class="panel-heading">
    <div>
      <span class="eyebrow">THE NEXT SMALL STEP</span>
      <h2>Reminders & follow-ups</h2>
    </div>
    <button class="button" disabled={loading} onclick={load}>Refresh</button>
  </header>
  <div class="reminders-body">
    {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
    <form onsubmit={create}>
      <label
        >When<input
          required
          bind:value={date}
          placeholder="YYYY-MM-DD, tomorrow, +7"
        /></label
      ><label
        >Reminder<input
          required
          bind:value={note}
          maxlength="20000"
          placeholder="A next step worth remembering"
        /></label
      ><button class="button primary" disabled={busy}>Add reminder</button>
    </form>
    {#if loading}<p role="status" class="empty-state">
        Loading reminders…
      </p>{:else}{#each [{ title: "Due now", items: data.due }, { title: "Coming up", items: data.upcoming }] as group}<section
          class="reminder-group"
        >
          <h3>{group.title}<span class="badge">{group.items.length}</span></h3>
          {#if !group.items.length}<p class="muted">
              Nothing here right now.
            </p>{/if}{#each group.items as reminder}<article>
              <div>
                <span class="badge">{reminder.kind}</span><strong
                  >{reminder.label}</strong
                >
                <p class="muted">
                  {dateLabel(
                    reminder.due_at,
                  )}{#if reminder.note && reminder.note !== reminder.label}
                    · {reminder.note}{/if}
                </p>
              </div>
              <div class="actions">
                {#if reminder.job_id}<button
                    class="button"
                    onclick={() => onopenjob(reminder.job_id!)}>Open job</button
                  >{/if}<button
                  class="button"
                  disabled={busy}
                  onclick={() => complete(reminder.id)}>Complete</button
                >
              </div>
            </article>{/each}
        </section>{/each}{/if}
  </div>
</section>

<style>
  .reminders-body {
    padding: 22px;
  }
  form {
    display: flex;
    align-items: end;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 28px;
  }
  label {
    display: grid;
    gap: 8px;
    font-size: 12px;
    color: var(--muted);
    flex: 1;
    min-width: 180px;
  }
  .reminder-group {
    margin-top: 28px;
  }
  .reminder-group h3 {
    font-size: 16px;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .reminder-group > p {
    font-size: 13px;
  }
  article {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    padding: 18px 0;
    border-top: 1px solid var(--border);
  }
  article strong {
    display: block;
    font-size: 13px;
    margin: 8px 0;
  }
  article p {
    font-size: 12px;
    line-height: 1.7;
    margin: 0;
  }
  .actions {
    display: flex;
    gap: 8px;
  }
  @media (max-width: 650px) {
    .reminders-body {
      padding: 16px;
    }
    article {
      align-items: start;
      flex-direction: column;
    }
  }
</style>
