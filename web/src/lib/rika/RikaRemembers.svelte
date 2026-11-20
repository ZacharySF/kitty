<script lang="ts">
  import type { Snippet } from "svelte";
  import { onMount } from "svelte";
  import { api } from "../api";
  import { skillLine, topSkills, type Learning, type LearningsState } from "./learnings";
  import { requestMemory } from "./memoryRequest.svelte";

  /**
   * The "Rika remembers" strip (the Workshop's, Career's Overview): the five skills the
   * postings the user acts on ask for most, each marked when a confirmed fact backs it, and a
   * way into Rika's Memory where every learning can be pinned or forgotten. Nothing shows
   * until there is one; `empty`, when given, shows once the answer holds none.
   */
  let { onopen, empty }: { onopen: () => void; empty?: Snippet } = $props();
  let skills = $state<Learning[]>([]);
  let loaded = $state(false);
  onMount(() => {
    void api<LearningsState>("/rika/learnings").then((state) => (skills = topSkills(state.learnings))).catch(() => (skills = []))
      .finally(() => (loaded = true));
  });
</script>

{#if skills.length}
  <div class="remembers" role="group" aria-label="Rika remembers">
    <span class="label">Rika remembers</span>
    <ul>
      {#each skills as skill (skill.id)}
        <li class:fact={skill.detail.fact} title={skillLine(skill)}>{skill.text}</li>
      {/each}
    </ul>
    <button class="text-button" onclick={() => { requestMemory(); onopen(); }}>Memory</button>
  </div>
{:else if loaded && empty}
  {@render empty()}
{/if}

<style>
  .remembers {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    padding: 0 0 12px;
    margin-bottom: 12px;
    border-bottom: 1px solid var(--line);
  }
  .label {
    color: var(--muted);
    font: 600 var(--fs-label) var(--font-ui);
  }
  ul {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  /* The Check tab's chips: a confirmed fact reads as found, the rest as a question. */
  li {
    padding: 2px 9px;
    border-radius: 999px;
    box-shadow: inset 0 0 0 1px var(--line);
    color: var(--ink);
    font-size: 11.5px;
  }
  li.fact {
    background: color-mix(in srgb, var(--success) 14%, transparent);
    box-shadow: none;
  }
  button {
    margin-left: auto;
  }
</style>
