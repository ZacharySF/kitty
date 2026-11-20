<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api } from "../api";
  import { FACETS, chipSize, editLine, evidenceJobs, grouped, skillLine, type Learning, type LearningsState } from "./learnings";

  /**
   * Rika's career memory on her page's Memory tab: the skills the postings the user acts on
   * keep asking for (larger the more they count, solid when a confirmed Resume Workshop fact
   * backs them; open one to see its postings), the jobs they keep choosing, how they review
   * resume edits and the notes they added. Each can be pinned (it leads what Rika sends and
   * outlives its evidence) or forgotten for good. Preferences follow in the Memory panel.
   */
  let memory = $state<LearningsState | null>(null);
  let error = $state("");
  let busy = $state("");
  let open = $state<number | null>(null);
  let adding = $state("");
  const groups = $derived(grouped(memory?.learnings ?? []));
  const heaviest = $derived(Math.max(0, ...groups.skills.map((item) => item.weight)));
  const opened = $derived(groups.skills.find((item) => item.id === open) ?? null);

  async function load() {
    try {
      memory = await api<LearningsState>("/rika/learnings");
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  onMount(() => void load());

  async function run(key: string, work: () => Promise<unknown>) {
    busy = key;
    try {
      await work();
      await load();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = "";
    }
  }
  function pin(item: Learning) {
    void run(`pin:${item.id}`, () => api(`/rika/learnings/${item.id}/pin`, { pinned: !item.pinned }));
  }
  function forget(item: Learning) {
    if (open === item.id) open = null;
    void run(`forget:${item.id}`, () => api(`/rika/learnings/${item.id}/forget`, {}));
  }
  function add(event: SubmitEvent) {
    event.preventDefault();
    const text = adding.trim();
    if (!text) return;
    void run("add", async () => {
      await api("/rika/learnings", { text });
      adding = "";
    });
  }
</script>

{#snippet actions(item: Learning)}
  <span class="actions">
    <button type="button" class="text-button" aria-pressed={item.pinned} disabled={busy !== ""} onclick={() => pin(item)}>{item.pinned ? "Unpin" : "Pin"}</button>
    <button type="button" class="text-button" disabled={busy !== ""} onclick={() => forget(item)} aria-label={`Forget “${item.text}”`}>Forget</button>
  </span>
{/snippet}

<Panel title="Career memory" aria-label="Career memory">
  {#if memory}
    <div class="rows">
      <div class="set-row">
        <span class="set-desc">Learned on this device from the postings you save, queue, apply to or skip, and from your resume reviews. Rika uses it to recommend jobs and tailor resumes: patterns, never facts about you.</span>
      </div>

      <div class="set-row section">
        <span class="set-label">From job postings</span>
        <span class="set-desc">Larger: asked for more often. Solid: a confirmed Workshop fact backs it.</span>
      </div>
      {#if groups.skills.length}
        <ul class="skills" aria-label="Skills postings ask for">
          {#each groups.skills as skill (skill.id)}
            <li>
              <button type="button" class="skill" data-size={chipSize(skill.weight, heaviest)} class:fact={skill.detail.fact} class:pinned={skill.pinned}
                aria-expanded={open === skill.id} title={skillLine(skill)} onclick={() => (open = open === skill.id ? null : skill.id)}>
                {skill.text}<small>{skill.detail.postings ?? 0}</small>
              </button>
            </li>
          {/each}
        </ul>
        {#if opened}
          <div class="detail" aria-label={`${opened.text} in detail`}>
            <div class="head"><strong>{opened.text}</strong><span class="set-desc">{skillLine(opened)}</span>{@render actions(opened)}</div>
            <ul class="jobs">
              {#each evidenceJobs(opened, memory.jobs) as job (job.id)}<li>{job.label}</li>{:else}<li class="none">Its postings are gone; it stays because you pinned it.</li>{/each}
            </ul>
          </div>
        {/if}
      {:else}
        <p class="none">Nothing yet. Save, queue or apply to postings and the skills they ask for show here.</p>
      {/if}
      {#if groups.patterns.length}
        <ul class="list" aria-label="Jobs you keep choosing">
          {#each groups.patterns as pattern (pattern.id)}
            <li>
              <span><em>{FACETS[pattern.detail.facet ?? ""] ?? "Pattern"}</em>{pattern.text}{#if pattern.pinned}<small class="pin">Pinned</small>{/if}</span>
              <small title={evidenceJobs(pattern, memory.jobs).map((job) => job.label).join("\n")}>{pattern.detail.postings} postings</small>
              {@render actions(pattern)}
            </li>
          {/each}
        </ul>
      {/if}

      <div class="set-row section">
        <span class="set-label">How you edit resumes</span>
        <span class="set-desc">From the Workshop edits you accepted or rejected.</span>
      </div>
      {#if groups.accepted.length || groups.rejected.length}
        {#each [{ label: "You keep", items: groups.accepted }, { label: "You turn down", items: groups.rejected }] as habit (habit.label)}
          {#if habit.items.length}
            <ul class="list" aria-label={habit.label}>
              {#each habit.items as edit (edit.id)}
                <li>
                  <span><em>{habit.label}</em>{edit.text}{#if edit.pinned}<small class="pin">Pinned</small>{/if}</span>
                  <small>{editLine(edit)}</small>
                  {@render actions(edit)}
                </li>
              {/each}
            </ul>
          {/if}
        {/each}
      {:else}
        <p class="none">Nothing yet. Review a proposed edit in Resume Workshop and Rika notes what you keep.</p>
      {/if}

      <div class="set-row section">
        <span class="set-label">Notes you added</span>
      </div>
      <ul class="list" aria-label="Notes you added">
        {#each groups.notes as note (note.id)}
          <li><span>{note.text}{#if note.pinned}<small class="pin">Pinned</small>{/if}</span><small></small>{@render actions(note)}</li>
        {:else}
          <li class="none">Tell Rika what matters in your search: teams, places, what to stress.</li>
        {/each}
      </ul>
      <form class="set-row add" onsubmit={add}>
        <input aria-label="Add something to remember" placeholder="Add something to remember" maxlength="500" bind:value={adding} />
        <Button size="sm" type="submit" disabled={!adding.trim() || busy === "add"}>Add</Button>
      </form>
      {#if memory.forgotten}<p class="set-note">{memory.forgotten} forgotten {memory.forgotten === 1 ? "item stays" : "items stay"} out of everything Rika sends.</p>{/if}
    </div>
  {:else if !error}
    <p class="set-note" role="status">Loading career memory…</p>
  {/if}
  {#if error}<p class="set-note" role="alert">{error} <button class="text-button" onclick={load}>Retry</button></p>{/if}
</Panel>

<style>
  .section .set-label {
    color: var(--muted);
    font: 600 var(--fs-label) var(--font-ui);
  }
  ul {
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .skills {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 4px 0 10px;
  }
  /* The Workshop's chips: solid (found) when a confirmed fact backs the skill. */
  .skill {
    display: inline-flex;
    align-items: baseline;
    gap: 6px;
    padding: 2px 10px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    box-shadow: inset 0 0 0 1px var(--line);
    color: var(--ink);
    font: inherit;
    cursor: pointer;
  }
  .skill[data-size="1"] { font-size: 11.5px; }
  .skill[data-size="2"] { font-size: 12.5px; }
  .skill[data-size="3"] { font-size: 14px; }
  .skill[data-size="4"] { font-size: 15.5px; font-weight: 500; }
  .skill.fact {
    background: color-mix(in srgb, var(--success) 14%, transparent);
    box-shadow: none;
  }
  .skill.pinned,
  .skill[aria-expanded="true"] {
    box-shadow: inset 0 0 0 1.5px var(--accent);
  }
  .skill small {
    color: var(--muted);
    font-size: 10.5px;
    font-variant-numeric: tabular-nums;
  }
  .detail {
    margin: 0 0 10px;
    padding: 8px 10px;
    border-radius: var(--r-item);
    background: var(--soft);
  }
  .head {
    display: flex;
    align-items: baseline;
    gap: 10px;
    flex-wrap: wrap;
  }
  strong {
    color: var(--ink);
    font-weight: 500;
  }
  .jobs {
    display: grid;
    gap: 2px;
    padding-top: 6px;
    font-size: 12.5px;
  }
  .list {
    display: grid;
    padding: 0 0 8px;
  }
  .list li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    align-items: baseline;
    column-gap: 12px;
    padding: 5px 0;
    border-bottom: 1px solid color-mix(in srgb, var(--line) 60%, transparent);
    font-size: 12.5px;
  }
  em {
    margin-right: 8px;
    color: var(--muted);
    font: 500 9.5px var(--font-ui);
    font-style: normal;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  small {
    color: var(--muted);
    font-size: 11px;
  }
  .pin {
    margin-left: 8px;
    color: var(--accent);
  }
  .actions {
    display: inline-flex;
    gap: 10px;
    margin-left: auto;
    font-size: 12px;
  }
  .none,
  .list li.none {
    display: block;
    margin: 0;
    padding: 4px 0 8px;
    border: 0;
    color: var(--muted);
    font-size: 12.5px;
  }
  .add {
    gap: 8px;
  }
  .add input {
    flex: 1;
    min-width: 0;
  }
</style>
