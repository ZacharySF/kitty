<script lang="ts">
  // A page's version history (platform design Q15): editing sessions per device and actor (the
  // person, an agent session, a paired app, rika! itself, a paired device), filterable by actor, a
  // slider over every kept transaction (and the daily snapshots older history folds into), a
  // read-only preview of the page at that moment, and Restore, which appends a new transaction and
  // never rewrites the past. An agent's session can be undone as a whole: one new transaction that
  // reverts what it wrote, keeping (and listing) what someone else changed since.
  import { onMount } from "svelte";
  import { api } from "./api";
  import { liveDevice, onBlockChange, type Actor } from "./live";
  import { PageDoc, visibleRows, type BlockData, type Row } from "./blockDoc";
  import { marksHTML, marksOf } from "./blockText";
  import { pageQuery, type PageRef } from "./BlockEditor.svelte";

  type Moment = { seq: number; at: string; session: number | null };
  type Session = { device_id: string; actor?: Actor; start: string; end: string; first_seq: number; last_seq: number; transactions: number; ops: Record<string, number>; moments: { seq: number; at: string }[] };
  type History = { page_id: string; horizon_seq: number; sessions: Session[]; snapshots: { day: string; seq: number }[]; devices?: Record<string, string> };
  type Preview = { page_id: string; seq: number; blocks: (BlockData & { deleted_at: string | null })[] };
  type Item = { id?: string; seq?: number; page_id: string | null; text?: string; fields?: string[]; reasons?: string[] };
  type Undo = { session: string; actor: Actor | null; transactions: number; applied: boolean; dry_run: boolean; reverted_count: number; conflict_count: number; reverted: Item[]; conflicts: Item[] };

  let { page, title = "", onclose }: { page: PageRef; title?: string; onclose: () => void } = $props();
  let dialog: HTMLDialogElement;
  let history = $state<History | null>(null);
  let index = $state(0);
  let preview = $state<{ seq: number; title: string; rows: Row[]; others: number } | { seq: number; gone: true } | null>(null);
  let error = $state("");
  let status = $state("");
  let restoring = $state(false);
  /** The actor filter: "" shows everyone, otherwise an actorKey. */
  let filter = $state("");
  /** The plan of an agent-session undo waiting for confirmation. */
  let undo = $state<Undo | null>(null);
  let undoing = $state(false);

  const USER: Actor = { kind: "user", name: null, session: null };
  const actorOf = (session: Session): Actor => session.actor ?? USER;
  /** The agent session a history session belongs to (a merge counts when it carried one agent session only). */
  const agentSession = (session: Session): string | null => {
    const actor = actorOf(session);
    if (actor.kind === "agent" && actor.session) return actor.session;
    const carried = actor.kind === "peer" ? actor.via ?? [] : [];
    return carried.length === 1 && carried[0].kind === "agent" && carried[0].session ? carried[0].session : null;
  };
  const actorKey = (session: Session) => {
    const actor = actorOf(session);
    return actor.kind === "peer" ? `peer:${session.device_id}` : `${actor.kind}:${actor.session ?? actor.name ?? ""}`;
  };
  // Merged changes are logged under the device that made them; paired devices go by their names.
  const device = (id: string) => (id === liveDevice() ? "This device" : history?.devices?.[id] ?? `Device ${id.slice(0, 8)}`);
  const named = (actor: Actor): string => {
    if (actor.kind === "user") return "You";
    if (actor.kind === "agent") return `Agent · ${actor.name ?? "unnamed"}`;
    if (actor.kind === "client") return `${actor.name ?? "Connected app"} (connected app)`;
    if (actor.kind === "system") return `rika! · ${actor.name ?? "system"}`;
    return actor.name ?? "Paired device";
  };
  /** Who made a session, with the device when it is not this one. */
  const who = (session: Session): string => {
    const actor = actorOf(session);
    if (actor.kind === "peer") {
      const carried = (actor.via ?? []).map(named).join(", ");
      return `${device(session.device_id)}${carried ? ` · ${carried}` : ""}`;
    }
    return session.device_id === liveDevice() || !liveDevice() ? named(actor) : `${named(actor)} · ${device(session.device_id)}`;
  };
  const shown = (session: Session) => !filter || actorKey(session) === filter;
  /** The actors in this page's history, for the filter (first appearance order, newest first). */
  const actors = $derived.by(() => {
    const found = new Map<string, string>();
    for (const session of history?.sessions ?? []) if (!found.has(actorKey(session))) found.set(actorKey(session), who(session));
    return [...found];
  });

  /** Every moment that can be previewed, oldest first: daily snapshots, then each kept transaction (of the filtered actor). */
  const moments = $derived.by((): Moment[] => {
    if (!history) return [];
    const kept = history.sessions.flatMap((session, number) => shown(session) ? session.moments.map((moment) => ({ ...moment, session: number })) : []);
    const snapshots = history.snapshots.map((snapshot) => ({ seq: snapshot.seq, at: snapshot.day, session: null }));
    return [...snapshots, ...kept].sort((a, b) => a.seq - b.seq);
  });
  const current = $derived(moments[index]);
  const currentSession = $derived(current && current.session !== null ? history?.sessions[current.session] ?? null : null);

  const time = (value: string) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return /^\d{4}-\d{2}-\d{2}$/.test(value)
      ? date.toLocaleDateString(undefined, { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" })
      : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });
  };
  const summary = (ops: Record<string, number>) => Object.entries(ops).filter(([kind]) => kind !== "set" || Object.keys(ops).length === 1)
    .map(([kind, count]) => `${count} ${kind}`).join(" · ");
  const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;
  const label = (item: Item) => item.text?.trim() ? `“${item.text.slice(0, 80)}”` : item.seq ? `Merged change ${item.seq}` : "A block without text";

  async function load(selectLatest = true) {
    try {
      history = await api<History>(`/blocks/history?${pageQuery(page)}`);
      error = "";
      if (filter && !history.sessions.some((session) => actorKey(session) === filter)) filter = "";
      if (selectLatest || index >= moments.length) index = Math.max(0, moments.length - 1);
      await show();
    } catch (cause) {
      error = (cause as Error).message;
    }
  }

  async function show() {
    const moment = moments[index];
    if (!history || !moment) { preview = null; return; }
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

  function filterBy(value: string) {
    filter = value;
    index = Math.max(0, moments.length - 1);
    void show();
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

  /** Ask what undoing the agent session would do (nothing is written yet). */
  async function planUndo(session: Session) {
    const id = agentSession(session);
    if (!id || undoing) return;
    undoing = true;
    try {
      undo = await api<Undo>(`/blocks/sessions/undo`, { session: id, dry_run: true });
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      undoing = false;
    }
  }

  async function confirmUndo() {
    const plan = undo;
    if (!plan || undoing) return;
    undoing = true;
    try {
      const done = await api<Undo>(`/blocks/sessions/undo`, { session: plan.session });
      const kept = done.conflict_count ? ` Kept ${plural(done.conflict_count, "change")} someone else made since.` : "";
      status = done.applied
        ? `Undid the agent session: ${plural(done.reverted_count, "block")} reverted in one new step.${kept}`
        : `Nothing left to undo in that agent session.${kept}`;
      undo = null;
      await load();
    } catch (cause) {
      status = "";
      error = (cause as Error).message;
    } finally {
      undoing = false;
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
        {#if actors.length > 1}
          <label class="filter"><span>Show</span>
            <select aria-label="Filter by actor" value={filter} onchange={(event) => filterBy(event.currentTarget.value)}>
              <option value="">Everyone</option>
              {#each actors as [key, name] (key)}<option value={key}>{name}</option>{/each}
            </select>
          </label>
        {/if}
        {#each history.sessions as session, number}
          {#if shown(session)}
            {@const actor = actorOf(session)}
            <div class="session actor-{actor.kind}">
              <button class:active={current?.session === number} onclick={() => { index = moments.reduce((found, moment, at) => (moment.session === number ? at : found), index); void show(); }}>
                <strong>{time(session.start)}{session.end !== session.start ? ` – ${new Date(session.end).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}` : ""}</strong>
                <span class="who">{who(session)}</span>
                <span>{session.transactions} edit{session.transactions === 1 ? "" : "s"}</span>
                <small>{summary(session.ops)}</small>
              </button>
              {#if agentSession(session)}
                <button class="undo-session" disabled={undoing} onclick={() => void planUndo(session)}
                  aria-label={`Undo agent session ${named(actor.kind === "peer" ? actor.via?.[0] ?? actor : actor)}`}>Undo this agent session…</button>
              {/if}
            </div>
          {/if}
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
        {#if undo}
          <section class="undo-plan" aria-label="Undo agent session">
            <h3>Undo {undo.actor ? named(undo.actor) : "the agent session"}</h3>
            <p>{undo.reverted_count
              ? `Reverts ${plural(undo.reverted_count, "block")} the agent changed in ${plural(undo.transactions, "transaction")}, on every page it touched, as one new step in history.`
              : "Nothing the agent wrote is left to revert."}</p>
            {#if undo.reverted.length}
              <ul class="reverted">{#each undo.reverted as item (item.id)}<li>{label(item)} <small>{(item.fields ?? []).join(", ")}</small></li>{/each}</ul>
            {/if}
            {#if undo.conflict_count}
              <p class="kept">{plural(undo.conflict_count, "block")} changed by someone else since stay as they are:</p>
              <ul class="conflicts">{#each undo.conflicts as item, at (item.id ?? `seq-${item.seq}-${at}`)}<li>{label(item)} <small>{(item.reasons ?? []).join("; ")}</small></li>{/each}</ul>
            {/if}
            <div class="actions">
              <button class="button primary" disabled={undoing || !undo.reverted_count} onclick={() => void confirmUndo()}>{undoing ? "Undoing…" : "Undo agent session"}</button>
              <button class="button" disabled={undoing} onclick={() => (undo = null)}>Cancel</button>
            </div>
          </section>
        {/if}
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
  .sessions .session { display: flex; flex-direction: column; }
  .sessions .session > button:first-child { width: 100%; }
  .sessions .who { color: var(--text, inherit); font-weight: 550; }
  .sessions .actor-agent .who, .sessions .actor-client .who { color: var(--accent); }
  .sessions .undo-session { align-self: flex-start; margin: -2px 0 4px 10px; padding: 2px 8px; font-size: 11px; color: var(--accent); border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent); border-radius: 999px; }
  .filter { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); margin: 0 6px 6px; }
  .filter select { flex: 1; min-width: 0; font: inherit; color: inherit; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 4px 6px; }
  .undo-plan { border: 1px solid var(--accent); border-radius: 8px; padding: 12px 14px; background: color-mix(in srgb, var(--accent) 6%, var(--surface)); font-size: 13px; }
  .undo-plan h3 { margin: 0 0 6px; font-size: 14px; }
  .undo-plan p { margin: 4px 0; }
  .undo-plan ul { margin: 4px 0 8px; padding-left: 18px; max-height: 120px; overflow: auto; }
  .undo-plan small { color: var(--muted); }
  .undo-plan .kept { color: var(--warning, var(--muted)); }
  .undo-plan .actions { display: flex; gap: 8px; }
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
