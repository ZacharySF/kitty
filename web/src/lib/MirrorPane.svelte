<script lang="ts">
  import { onMount } from "svelte";
  import { safeUrl } from "./api";
  import { age, imageUrl, paragraphs, type Mirror } from "./mirror";
  import { followMirrors, mirrors } from "./mirrorStore.svelte";

  // The Mirror pane beside a note (platform design Q11): the tab the kitty browser
  // extension mirrors, with its latest screenshot, title, URL and readable text. Quoting a
  // passage (a paragraph's Quote button, text selected here, or the selection made in
  // the browser) puts it into the open note.
  let { onquote, onclose }: { onquote: (passage: string, mirror: Mirror) => void; onclose: () => void } = $props();

  const SHOWN = 200;
  let chosen = $state<string | null>(null);
  let all = $state(false);
  let now = $state(Date.now());
  let picked = $state<{ text: string; x: number; y: number } | null>(null);
  let announcement = $state("");
  let reader = $state<HTMLElement>();

  const current = $derived(mirrors.list.find((item) => item.id === chosen) ?? mirrors.list[0] ?? null);
  const currentId = $derived(current?.id ?? null);
  const parts = $derived(current ? paragraphs(current.text) : []);
  const host = $derived.by(() => { try { return current ? new URL(current.url).host : ""; } catch { return ""; } });
  const retention = $derived(mirrors.retention);

  onMount(() => {
    followMirrors();
    const timer = setInterval(() => { now = Date.now(); }, 5000);
    return () => clearInterval(timer);
  });

  $effect(() => {
    void currentId;
    all = false;
    picked = null;
  });

  function quote(passage: string) {
    if (!current || !passage.trim()) return;
    onquote(passage, current);
    picked = null;
    window.getSelection()?.removeAllRanges();
    announcement = "Quoted into the note";
  }

  // Text selected inside the readable text offers a Quote button beside it.
  function pick() {
    const selection = window.getSelection();
    const text = selection?.toString().trim() ?? "";
    if (!selection || !text || !reader || selection.rangeCount === 0 || !reader.contains(selection.getRangeAt(0).commonAncestorContainer)) {
      picked = null;
      return;
    }
    const box = selection.getRangeAt(0).getBoundingClientRect();
    const frame = reader.getBoundingClientRect();
    picked = { text, x: Math.max(0, box.left - frame.left), y: box.bottom - frame.top + reader.scrollTop + 4 };
  }
</script>

<aside class="mirror" aria-label="Mirror">
  <header>
    <h3>Mirror</h3>
    <button class="close" onclick={onclose} aria-label="Close the mirror">Close ×</button>
  </header>
  <p class="sr-only" aria-live="polite">{announcement}</p>
  {#if mirrors.error}<p role="alert">{mirrors.error}</p>{/if}
  {#if mirrors.list.length > 1}
    <div class="tabs" role="group" aria-label="Mirrored tabs">
      {#each mirrors.list as item (item.id)}
        <button aria-pressed={item.id === current?.id} onclick={() => { chosen = item.id; }} title={item.url}>{item.title}</button>
      {/each}
    </div>
  {/if}
  {#if current}
    <div class="source">
      {#if safeUrl(current.url)}
        <a class="title" href={safeUrl(current.url)} target="_blank" rel="noopener noreferrer">{current.title}</a>
      {:else}<strong class="title">{current.title}</strong>{/if}
      <small>{host} · {current.client.name} · updated {age(current.at, now)}</small>
    </div>
    {#if imageUrl(current)}
      <img class="shot" src={imageUrl(current)} alt={`Screenshot of ${current.title}`} width={current.image?.width} height={current.image?.height} />
    {/if}
    {#if current.selection}
      <section class="selection" aria-label="Selected in the browser">
        <blockquote>{current.selection}</blockquote>
        <button class="quote" onclick={() => quote(current.selection)}>Quote the browser selection</button>
      </section>
    {/if}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="reader" bind:this={reader} onmouseup={pick} onkeyup={pick} aria-label="Readable text">
      {#each all ? parts : parts.slice(0, SHOWN) as part, index (index)}
        <div class="passage">
          <p>{part}</p>
          <button class="quote" aria-label={`Quote passage ${index + 1}`} onclick={() => quote(part)}>Quote</button>
        </div>
      {:else}
        <p class="muted">No readable text on this page.</p>
      {/each}
      {#if !all && parts.length > SHOWN}
        <button onclick={() => { all = true; }}>Show all {parts.length} paragraphs</button>
      {/if}
      {#if picked}
        <button class="picked" style:left={`${picked.x}px`} style:top={`${picked.y}px`} onmousedown={(event) => event.preventDefault()}
          onclick={() => picked && quote(picked.text)}>Quote selection</button>
      {/if}
    </div>
  {:else}
    <p class="empty">No tab is being mirrored. In the rika! browser extension, connect to this rika! and switch on <em>Mirror this tab</em>.</p>
  {/if}
  <p class="retention">
    Mirror frames stay in rika!'s memory only: the latest screenshot and text of each mirrored tab
    {#if retention}(at most {retention.max_mirrors} tabs){/if}. They are dropped when mirroring stops, after
    {retention ? Math.round(retention.expiry_seconds / 60 * 10) / 10 : 2.5} minutes without a frame, when the extension
    is revoked, or when rika! stops. Only passages you quote are saved, in the note.
  </p>
</aside>

<style>
  .mirror { width: 360px; flex-shrink: 0; border-left: 1px solid var(--border); padding: 12px 16px; max-height: 80vh; overflow: auto; background: var(--chrome); display: flex; flex-direction: column; gap: 10px; }
  header { display: flex; align-items: center; justify-content: space-between; }
  h3 { font-size: 14px; margin: 0; }
  button { font: inherit; color: inherit; cursor: pointer; background: none; border: 0; border-radius: 4px; padding: 4px 6px; }
  button:hover { background: var(--surface-raised); }
  .close { font-size: 11px; }
  .tabs { display: flex; flex-wrap: wrap; gap: 4px; }
  .tabs button { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; border: 1px solid var(--border); }
  .tabs button[aria-pressed="true"] { border-color: var(--accent); color: var(--accent); }
  .source { display: flex; flex-direction: column; gap: 2px; }
  .title { font-weight: 600; color: var(--accent); overflow-wrap: anywhere; }
  .source small, .muted, .retention, .empty { color: var(--muted); font-size: 12px; }
  .shot { width: 100%; height: auto; border: 1px solid var(--border); border-radius: 4px; }
  .selection blockquote { margin: 0; padding: 6px 10px; border-left: 3px solid var(--accent); white-space: pre-wrap; font-size: 13px; }
  .reader { position: relative; font-size: 13px; line-height: 1.6; }
  .passage { position: relative; padding-right: 52px; }
  .passage p { margin: 0 0 10px; white-space: pre-wrap; overflow-wrap: anywhere; }
  .passage .quote { position: absolute; top: 0; right: 0; font-size: 11px; color: var(--accent); opacity: 0.5; }
  .passage:hover .quote, .passage .quote:focus-visible { opacity: 1; }
  .quote { color: var(--accent); font-size: 12px; }
  .picked { position: absolute; z-index: 2; background: var(--accent); color: var(--surface, #fff); font-size: 11px; box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25); }
  .picked:hover { background: var(--accent); }
  .retention { border-top: 1px solid var(--border); padding-top: 8px; margin: 4px 0 0; line-height: 1.5; }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  @media (max-width: 900px) { .mirror { width: 280px; } }
  @media (max-width: 650px) { .mirror { width: auto; border-left: 0; border-top: 1px solid var(--border); max-height: none; } }
</style>
