<script lang="ts">
  // A page's version history (platform design Q15): editing sessions per device, a slider
  // over every kept transaction (and the daily snapshots older history folds into), a
  // read-only preview of the page at that moment, and Restore, which appends a new
  // transaction and never rewrites the past.
  import { onMount } from "svelte";
  import { api } from "./api";
  import { liveDevice, onBlockChange } from "./live";
  import { PageDoc, visibleRows, type BlockData, type Row } from "./blockDoc";
  import { marksHTML, marksOf } from "./blockText";
  import { pageQuery, type PageRef } from "./BlockEditor.svelte";

  type Moment = { seq: number; at: string; session: number | null };
  type Session = { device_id: string; start: string; end: string; first_seq: number; last_seq: number; transactions: number; ops: Record<string, number>; moments: { seq: number; at: string }[] };
  type History = { page_id: string; horizon_seq: number; sessions: Session[]; snapshots: { day: string; seq: number }[]; devices?: Record<string, string> };
  type Preview = { page_id: string; seq: number; blocks: (BlockData & { deleted_at: string | null })[] };

  let { page, title = "", onclose }: { page: PageRef; title?: string; onclose: () => void } = $props();
  let dialog: HTMLDialogElement;
  let history = $state<History | null>(null);
  let index = $state(0);
  let preview = $state<{ seq: number; title: string; rows: Row[]; others: number } | { seq: number; gone: true } | null>(null);
  let error = $state("");
  let status = $state("");
  let restoring = $state(false);

  /** Every moment that can be previewed, oldest first: daily snapshots, then each kept transaction. */
  const moments = $derived.by((): Moment[] => {
    if (!history) return [];
    const kept = history.sessions.flatMap((session, number) => session.moments.map((moment) => ({ ...moment, session: number })));
    const snapshots = history.snapshots.map((snapshot) => ({ seq: snapshot.seq, at: snapshot.day, session: null }));
    return [...snapshots, ...kept].sort((a, b) => a.seq - b.seq);
  });
  const current = $derived(moments[index]);

  const time = (value: string) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return /^\d{4}-\d{2}-\d{2}$/.test(value)
      ? date.toLocaleDateString(undefined, { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" })
      : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });
  };
  // Merged changes are logged under the device that made them; paired devices go by their names.
  const device = (id: string) => (id === liveDevice() ? "This device" : history?.devices?.[id] ?? `Device ${id.slice(0, 8)}`);
  const summary = (ops: Record<string, number>) => Object.entries(ops).filter(([kind]) => kind !== "set" || Object.keys(ops).length === 1)
    .map(([kind, count]) => `${count} ${kind}`).join(" · ");

  async function load(selectLatest = true) {
    try {
      history = await api<History>(`/blocks/history?${pageQuery(page)}`);
      error = "";
      if (selectLatest) index = Math.max(0, moments.length - 1);
      await show();
    } catch (cause) {
      error = (cause as Error).message;
    }
  }

  async function show() {
    const moment = moments[index];
    if (!history || !moment) return;
    try {
      const result = await api<Preview>(`/blocks/history/preview?page=${history.page_id}&seq=${moment.seq}`);
      if (moments[index]?.seq !== moment.seq) return;
      const root = result.blocks.find((block) => block.id === result.page_id);
      if (!root) throw new Error("This version has no page");
      const live = result.blocks.filter((block) => block.id !== root.id && !block.deleted_at);
      const doc = PageDoc.from({ page_id: result.page_id, seq: moment.seq, revision: 0, root, blocks: live });
      const rows = visibleRows(doc);
      preview = { seq: moment.seq, title: root.text, rows, others: live.length - rows.length };
    } catch (cause) {
      if (cause instanceof Error && "status" in cause && cause.status === 410) preview = { seq: moment.seq, gone: true };
      else error = (cause as Error).message;
    }
  }

  async function restore() {
    const moment = current;
    if (!history || !moment || restoring) return;
    restoring = true;
    try {
      await api(`/blocks/history/restore`, { page: history.page_id, seq: moment.seq });
      status = `Restored the page as it was at ${time(moment.at)}. The restore is a new step in its history.`;
      await load();
    } catch (cause) {
      status = "";
      error = (cause as Error).message;
    } finally {
      restoring = false;
    }
  }

  onMount(() => {
    dialog.showModal();
    void load();
    // Edits made while the history is open become new moments.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stop = onBlockChange((change) => {
      if (!history || (change && change.page_id !== history.page_id)) return;
      clearTimeout(timer);
      timer = setTimeout(() => void load(index === moments.length - 1), 400);
    });
    return () => { clearTimeout(timer); stop(); };
  });
</script>

<dialog bind:this={dialog} class="workspace-dialog block-history" aria-labelledby="history-title" oncancel={onclose}>
  <header>
    <div>
      <span class="eyebrow">VERSION HISTORY · 30 DAYS OF EVERY EDIT</span>
      <h2 id="history-title">History{title ? ` — ${title}` : ""}</h2>
    </div>
    <button class="icon-button" onclick={onclose} aria-label="Close history">×</button>
  </header>
  {#if error}<p class="problem" role="alert">{error}</p>{/if}
  {#if status}<p class="done" role="status">{status}</p>{/if}
  {#if history}
    <div class="layout">
      <nav class="sessions" aria-label="Editing sessions">
        {#each history.sessions as session, number}
          <button class:active={current?.session === number} onclick={() => { index = moments.reduce((found, moment, at) => (moment.session === number ? at : found), index); void show(); }}>
            <strong>{time(session.start)}{session.end !== session.start ? ` – ${new Date(session.end).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}` : ""}</strong>
            <span>{device(session.device_id)} · {session.transactions} edit{session.transactions === 1 ? "" : "s"}</span>
            <small>{summary(session.ops)}</small>
          </button>
        {:else}<p class="muted">No edits are kept in the last 30 days.</p>{/each}
        {#if history.snapshots.length}
          <h3>Older (daily snapshots)</h3>
          {#each history.snapshots as snapshot}
            <button class:active={current?.seq === snapshot.seq && current.session === null} onclick={() => { index = moments.findIndex((moment) => moment.seq === snapshot.seq && moment.session === null); void show(); }}>
              <strong>{time(snapshot.day)}</strong><span>End of day</span>
            </button>
          {/each}
        {/if}
      </nav>
      <section class="moment" aria-label="Preview">
        {#if moments.length}
          <label class="slider">
            <span>Moment {index + 1} of {moments.length}{current ? ` · ${time(current.at)}` : ""}</span>
            <input type="range" min="0" max={moments.length - 1} step="1" bind:value={index} oninput={() => void show()} aria-label="Moment in history"
              aria-valuetext={current ? time(current.at) : ""} />
          </label>
        {/if}
        <div class="preview" aria-live="polite">
          {#if preview && "gone" in preview}
            <p class="muted">That moment was compacted. Choose a daily snapshot or a moment in the last 30 days.</p>
          {:else if preview}
            <h3 class="preview-title">{preview.title || "Untitled page"}</h3>
            {#each preview.rows as row (row.block.id)}
              {@const block = row.block}
              <div class="row type-{block.type}" style:--depth={row.depth}>
                {#if block.type === "divider"}<hr />
                {:else if block.type === "to_do"}<span class="mark">{block.properties.checked ? "☑" : "☐"}</span>
                {:else if block.type === "bulleted_list"}<span class="mark">•</span>
                {:else if block.type === "numbered_list"}<span class="mark">{row.number}.</span>
                {:else if block.type === "toggle"}<span class="mark">▾</span>
                {:else if block.type === "page_link"}<span class="mark">↗</span>{/if}
                {#if block.type !== "divider"}<!-- marksHTML escapes the text and emits only formatting tags. -->
                  <span class="text">{@html marksHTML(block.text, marksOf(block))}</span>{/if}
              </div>
            {:else}<p class="muted">The page had no text blocks at this moment.</p>{/each}
            {#if preview.others}<p class="muted">Plus {preview.others} canvas item{preview.others === 1 ? "" : "s"}.</p>{/if}
          {/if}
        </div>
        <footer>
          <button class="button primary" disabled={!current || restoring || index === moments.length - 1 || (preview !== null && "gone" in preview)} onclick={() => void restore()}>
            {restoring ? "Restoring…" : "Restore this version"}
          </button>
          <span class="muted">Restoring keeps every later version in history.</span>
        </footer>
      </section>
    </div>
  {:else if !error}<p class="muted">Loading history…</p>{/if}
</dialog>

<style>
  .block-history { max-width: 1000px; }
  .eyebrow { font-size: 11px; letter-spacing: .06em; color: var(--muted); }
  .layout { display: grid; grid-template-columns: 260px 1fr; gap: 18px; min-height: 420px; }
  .sessions { display: flex; flex-direction: column; gap: 4px; max-height: 62vh; overflow: auto; padding-right: 6px; border-right: 1px solid var(--border); }
  .sessions h3 { font-size: 12px; color: var(--muted); margin: 14px 6px 4px; }
  .sessions button { display: flex; flex-direction: column; gap: 2px; text-align: left; font: inherit; color: inherit; background: none; border: 1px solid transparent; border-radius: 6px; padding: 8px 10px; cursor: pointer; }
  .sessions button:hover { background: var(--surface-raised); }
  .sessions button.active { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); }
  .sessions strong { font-size: 13px; }
  .sessions span, .sessions small { font-size: 11px; color: var(--muted); }
  .moment { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
  .slider { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--muted); }
  .slider input { width: 100%; accent-color: var(--accent); }
  .preview { flex: 1; max-height: 52vh; overflow: auto; border: 1px solid var(--border); border-radius: 8px; padding: 14px 18px; background: var(--canvas, var(--surface)); }
  .preview-title { font-family: var(--heading); font-size: 20px; margin: 0 0 10px; }
  .row { display: flex; gap: 8px; padding-left: calc(var(--depth) * 22px); font-size: 14px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
  .row .mark { color: var(--muted); flex-shrink: 0; }
  .row :global(code) { font-family: var(--font-mono); font-size: .9em; }
  .row :global(a) { color: var(--accent); }
  .row hr { flex: 1; border: 0; border-top: 1px solid var(--border); }
  .type-heading_1 .text { font-size: 22px; font-weight: 700; }
  .type-heading_2 .text { font-size: 18px; font-weight: 650; }
  .type-heading_3 .text { font-size: 16px; font-weight: 650; }
  .type-quote .text { border-left: 3px solid var(--ink); padding-left: 10px; }
  .type-code .text { font-family: var(--font-mono); font-size: 12px; }
  footer { display: flex; align-items: center; gap: 12px; }
  .problem { color: var(--error); }
  .done { color: var(--success); }
  @media (max-width: 760px) { .layout { grid-template-columns: 1fr; } .sessions { border-right: 0; max-height: 30vh; } }
</style>
