<script lang="ts">
  import { tick } from "svelte";
  import BrainCloud from "./BrainCloud.svelte";
  import { megabytes, percent } from "./modulePicker";
  import { moduleStore } from "./modulePicker.svelte";

  const id = $props.id();
  let dialog = $state<HTMLDialogElement>();
  let line = $state<HTMLParagraphElement>();
  /** The control focused last outside the cover: the one that started a download, even once disabled and blurred. */
  let last: HTMLElement | null = null;
  const downloading = $derived(moduleStore.phase === "downloading" || !!moduleStore.addon);
  const progress = $derived(moduleStore.progress);
  const label = $derived(moduleStore.addon || progress?.label || "");
  const measured = $derived(progress !== null && progress.total > 0);
  const amount = $derived(measured && progress ? `${megabytes(progress.received)} of ${megabytes(progress.total)}` : "");

  $effect(() => {
    const modal = dialog, title = line;
    if (!downloading || !modal || !title) return;
    const trigger = last, parent = trigger?.closest("dialog") ?? null;
    modal.showModal();
    title.focus({ preventScroll: true });
    return () => {
      modal.close();
      void tick().then(() => {
        if (document.querySelector("dialog.module-download[open]")) return;
        if (trigger?.isConnected) trigger.focus({ preventScroll: true });
        if (document.activeElement !== trigger || trigger === document.body) {
          const form = trigger?.isConnected ? trigger.closest("form") : null;
          const scope = parent?.open ? parent : (form ?? document.querySelector("dialog[open]"));
          const fallback = scope?.querySelector<HTMLElement>("button:not(:disabled), [href], input:not(:disabled), [tabindex='0']")
            ?? document.getElementById("main-content");
          fallback?.focus({ preventScroll: true });
        }
      });
    };
  });
</script>

<svelte:document onfocusin={(event) => { if (!downloading && event.target instanceof HTMLElement) last = event.target; }} />

<dialog bind:this={dialog} class="module-download" aria-modal="true" aria-busy={downloading ? "true" : undefined}
  aria-labelledby={`${id}-title`} aria-describedby={amount ? `${id}-amount` : undefined} oncancel={(event) => event.preventDefault()}
  onkeydown={(event) => { if (event.key === "Tab") { event.preventDefault(); line?.focus({ preventScroll: true }); } }}>
  {#if downloading}
  <div class="download-content">
    <BrainCloud size={192} />
    <p bind:this={line} class="line" id={`${id}-title`} tabindex="-1" role="status" aria-atomic="true">Downloading{label ? ` ${label}` : ""}…</p>
    {#if !moduleStore.addon}
      <div class="download-progress" role="progressbar" aria-label="Total download progress" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={measured && progress ? percent(progress) : undefined} aria-valuetext={amount || undefined}>
        {#if measured && progress}<span style:width={`${percent(progress)}%`}></span>{/if}
      </div>
    {/if}
    {#if amount}<p id={`${id}-amount`}>{amount}</p>{/if}
  </div>
  {/if}
</dialog>

<style>
  /* The native top layer covers both the title bar and an already-open onboarding dialog. */
  :global(:root body) dialog.module-download {
    position: fixed;
    inset: 0;
    margin: 0;
    width: 100vw;
    height: 100dvh;
    max-width: none;
    max-height: none;
    box-sizing: border-box;
    padding: 24px;
    border: 0;
    border-radius: 0;
    background: var(--canvas);
    color: var(--ink);
    box-shadow: none;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }
  .module-download[open] { display: grid; place-items: center; }
  .module-download::backdrop { background: var(--canvas); backdrop-filter: none; -webkit-backdrop-filter: none; }
  .download-content { display: grid; justify-items: center; gap: 16px; width: min(100%, 320px); text-align: center; }
  p { margin: 0; font-size: 13px; color: var(--muted); font-variant-numeric: tabular-nums; }
  p.line { font-size: 18px; color: var(--ink); overflow-wrap: anywhere; outline: none; }
  .download-progress { width: 100%; height: 2px; background: var(--hairline); overflow: hidden; }
  .download-progress span { display: block; height: 100%; background: var(--ink); }
</style>
