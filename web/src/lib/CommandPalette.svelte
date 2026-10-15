<script lang="ts">
  import { onMount } from "svelte";
  import { api, type Job } from "./api";
  import { navigation, navigationGroups, type View } from "./workspace";
  let {
    onclose,
    onview,
    onopenjob,
    onadd,
    onhelp,
    onreload,
  }: {
    onclose: () => void;
    onview: (view: View) => void;
    onopenjob: (id: number) => void;
    onadd: () => void;
    onhelp: () => void;
    onreload: () => void;
  } = $props();
  let dialog: HTMLDialogElement;
  let searchField = $state<HTMLInputElement>();
  let query = $state("");
  let jobs = $state<Job[]>([]);
  let selected = $state(0);
  let error = $state("");
  const actions = [
    { label: "Add a job posting", run: () => onadd() },
    { label: "Keyboard shortcuts", run: () => onhelp() },
    { label: "Reload configuration from disk", run: () => onreload() },
  ];
  const choices = $derived(
    [
      ...navigation.map((item) => ({
        label: item.label,
        meta: item.key || navigationGroups.find((group) => group.views.includes(item.id))?.label || "Section",
        run: () => onview(item.id),
      })),
      ...actions.map((item) => ({ ...item, meta: "Action" })),
    ]
      .filter((item) => item.label.toLowerCase().includes(query.toLowerCase()))
      .concat(
        jobs.map((job) => ({
          label: `${job.company} · ${job.title}`,
          meta: `Job #${job.id}`,
          run: () => onopenjob(job.id),
        })),
      ),
  );
  onMount(() => {
    dialog.showModal();
    searchField?.focus();
  });
  $effect(() => {
    const text = query.trim();
    selected = 0;
    let cancelled = false;
    const timer = setTimeout(() => {
      if (!text) {
        jobs = [];
        return;
      }
      void api<{ jobs: Job[] }>(
        `/jobs?search=${encodeURIComponent(text)}&limit=12&view=jobs&closed=1`,
      )
        .then((result) => {
          if (!cancelled) jobs = result.jobs;
        })
        .catch((cause) => {
          if (!cancelled) error = cause.message;
        });
    }, 160);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  });
  function run(index: number) {
    const action = choices[index];
    if (!action) return;
    onclose();
    action.run();
  }
  function key(event: KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      selected = Math.max(
        0,
        Math.min(
          choices.length - 1,
          selected + (event.key === "ArrowDown" ? 1 : -1),
        ),
      );
      dialog
        .querySelector(`[data-command="${selected}"]`)
        ?.scrollIntoView({ block: "nearest" });
    } else if (
      event.key === "Enter" &&
      !(event.target as HTMLElement)?.closest("button")
    ) {
      event.preventDefault();
      run(selected);
    }
  }
</script>

<dialog
  bind:this={dialog}
  class="workspace-dialog palette"
  aria-labelledby="command-title"
  oncancel={onclose}
  onkeydown={key}
>
  <header>
    <h2 id="command-title">Go somewhere. Do something.</h2>
    <button
      class="icon-button"
      onclick={onclose}
      aria-label="Close command palette">×</button
    >
  </header>
  <input
    bind:this={searchField}
    aria-label="Search workspace sections, commands and jobs"
    placeholder="Calendar, study, notes, jobs, or an action…"
    bind:value={query}
  />{#if error}<p class="error-banner" role="alert">{error}</p>{/if}
  <div class="command-list">
    {#each choices as item, index}<button
        data-command={index}
        class:chosen={selected === index}
        onfocus={() => (selected = index)}
        onclick={() => run(index)}
        ><span>{item.label}</span><small>{item.meta}</small></button
      >{/each}{#if !choices.length}<p class="muted">
        No matching sections, commands, or jobs.
      </p>{/if}
  </div>
  <footer><span>↑ ↓ to navigate · Enter to open · Esc to close</span></footer>
</dialog>

<style>
  .palette {
    max-width: 620px;
  }
  .palette > input {
    width: 100%;
    font-size: 14px;
  }
  .command-list {
    max-height: 50vh;
    overflow: auto;
    display: grid;
    gap: 4px;
    margin-top: 16px;
  }
  .command-list button {
    padding: 13px 12px;
    text-align: left;
    display: flex;
    justify-content: space-between;
    gap: 20px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    font-size: 13px;
  }
  .command-list button.chosen,
  .command-list button:hover {
    background: var(--surface-raised);
    border-color: var(--border);
  }
  .command-list small {
    color: var(--muted);
    white-space: nowrap;
    font-size: 10px;
  }
  footer {
    color: var(--muted);
    font-size: 10px;
    border-top: 1px solid var(--border);
    padding-top: 16px;
  }
</style>
