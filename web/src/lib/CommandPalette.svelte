<script lang="ts">
  import { onMount } from "svelte";
  import { Button } from "@nunera/bracket";
  import { api, type Job } from "./api";
  import { navigationGroups, visibleNavigation, type View } from "./workspace";
  import { buildStore } from "./build.svelte";
  let {
    enabled,
    onclose,
    onview,
    onopenjob,
    onadd,
    onhelp,
    onreload,
  }: {
    enabled: readonly string[];
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
  const applyai = $derived(enabled.includes("applyai"));
  const actions = $derived([
    ...(applyai ? [{ label: "Add job", run: () => onadd() }] : []),
    { label: "Keyboard shortcuts", run: () => onhelp() },
    { label: "Reload configuration", run: () => onreload() },
    { label: "Edit build", run: () => { buildStore.editorOpen = true; } },
  ]);
  const choices = $derived(
    [
      ...visibleNavigation(enabled).map((item) => ({
        label: item.label,
        meta: navigationGroups.find((group) => group.views.includes(item.id))?.label || "Section",
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
      if (!text || !applyai) {
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
    <h2 id="command-title">Search</h2>
    <Button
      variant="ghost"
      size="sm"
      onclick={onclose}
      aria-label="Close command palette">×</Button
    >
  </header>
  <input
    type="search"
    bind:this={searchField}
    aria-label={applyai ? "Search workspace sections, commands and jobs" : "Search workspace sections and commands"}
    placeholder={applyai ? "Sections, commands, jobs…" : "Sections, commands…"}
    bind:value={query}
  />{#if error}<p class="error-banner" role="alert">{error}</p>{/if}
  <div class="command-list">
    {#each choices as item, index}<Button
        data-command={index}
        data-selected={selected === index}
        onfocus={() => (selected = index)}
        onclick={() => run(index)}
        ><span>{item.label}</span><small>{item.meta}</small></Button
      >{/each}{#if !choices.length}<p class="muted">
        No results.
      </p>{/if}
  </div>
  <footer><span><kbd>↑</kbd> <kbd>↓</kbd> Navigate</span><span><kbd>Enter</kbd> Open</span><span><kbd>Esc</kbd> Close</span></footer>
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
  .command-list :global(.br-btn) {
    width: 100%;
    padding: 12px;
    text-align: left;
    justify-content: stretch;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    font-size: 13px;
    white-space: normal;
  }
  .command-list :global(.contents) {
    width: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
  }
  .command-list :global(.br-btn[data-selected="true"]),
  .command-list :global(.br-btn:hover) {
    background: var(--surface-raised);
    border-color: var(--border);
  }
  .command-list small {
    color: var(--muted);
    white-space: nowrap;
    font-size: 11px;
  }
  footer {
    color: var(--muted);
    font-size: 11px;
    flex-wrap: wrap;
    justify-content: flex-start;
    gap: 16px;
    border-top: 1px solid var(--border);
    padding-top: 14px;
  }
</style>
