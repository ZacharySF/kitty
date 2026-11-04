<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api, type TaskRecord } from "../../api";
  import Select from "../../ui/Select.svelte";
  import "../rika.css";
  import { holdClock, holdLeft, type RikaDraft } from "../rika";
  import { rika } from "../session.svelte";
  import type { MailState } from "./settings";

  /**
   * Q16 B: Settings → Rika → Email. The Gmail connection, one auto-send switch per kind of
   * reply (all off until you confirm one), how long rika! holds a send before it goes, and
   * Rika's drafts: the mail it answers, the editable reply, Send / Undo-send / Discard.
   * Gmail's API cannot delay or unsend, so the hold happens on this machine.
   */
  const uid = $props.id();
  const STATUS = { draft: "Draft", scheduled: "Sending", sent: "Sent", cancelled: "Cancelled", discarded: "Discarded", failed: "Failed" } as const;
  let mail = $state<MailState | null>(null);
  let error = $state("");
  let note = $state("");
  let busy = $state("");
  let asking = $state<MailState["kinds"][number] | null>(null);
  let dialog = $state<HTMLDialogElement>();
  let edits = $state<Record<string, string>>({});
  let now = $state(Date.now());

  /** The page's list, overlaid with what the stream has said since about the same drafts. */
  const drafts = $derived.by(() => {
    const listed = mail?.drafts ?? [];
    const live = new Map(rika.entries.flatMap((entry) => (entry.kind === "mail" ? [[entry.id, entry] as const] : [])));
    const newer = (a: RikaDraft, b: RikaDraft | undefined) => (b && (b.updated_at ?? "") >= (a.updated_at ?? "") ? b : a);
    const known = new Set(listed.map((draft) => draft.id));
    // Drafts written since the list loaded; older history entries stay out of it.
    const since = listed[0]?.created_at ?? "";
    const fresh = [...live.values()].filter((draft) => !known.has(draft.id) && (draft.created_at ?? "") >= since).reverse();
    return [...fresh, ...listed.map((draft) => newer(draft, live.get(draft.id)))].filter((draft) => draft.status !== "discarded");
  });
  const hold = $derived(mail?.mailbox.hold_seconds ?? 30);
  const holding = $derived(drafts.some((draft) => draft.status === "scheduled"));
  const asked = new Set<string>();
  $effect(() => {
    if (!holding) return;
    now = Date.now();
    const timer = setInterval(() => {
      now = Date.now();
      // A send past its time with no word from the stream: ask the server how it went.
      const overdue = drafts.find((draft) => draft.status === "scheduled" && !asked.has(draft.id) && Date.parse(draft.send_after ?? "") + 1500 < now);
      if (overdue) { asked.add(overdue.id); void refresh(); }
    }, 250);
    return () => clearInterval(timer);
  });

  async function run<T>(key: string, work: () => Promise<T>): Promise<T | undefined> {
    busy = key;
    error = "";
    try {
      return await work();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = "";
    }
  }
  async function refresh() {
    try {
      mail = await api<MailState>("/rika/mail");
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  onMount(() => void refresh());

  function flip(kind: MailState["kinds"][number]) {
    if (kind.auto) void run(`kind:${kind.kind}`, async () => (mail = await api<MailState>("/rika/mail/settings", { kind: kind.kind, auto: false })));
    else {
      asking = kind;
      dialog?.showModal();
    }
  }
  function closeAsk() {
    dialog?.close();
    asking = null;
  }
  async function confirmAuto() {
    const kind = asking;
    closeAsk();
    if (kind) await run(`kind:${kind.kind}`, async () => (mail = await api<MailState>("/rika/mail/settings", { kind: kind.kind, auto: true, confirm: true })));
  }
  function setHold(value: string) {
    void run("hold", async () => (mail = await api<MailState>("/rika/mail/settings", { hold_seconds: Number(value) })));
  }
  async function check() {
    note = "";
    const result = await run("check", () => api<{ new: number; drafts: RikaDraft[]; notes?: string[] }>("/rika/mail/check", {}));
    if (!result) return;
    await refresh();
    const found = result.new ? `${result.new} new message${result.new === 1 ? "" : "s"} · ${result.drafts.length} draft${result.drafts.length === 1 ? "" : "s"} written` : "No new mail";
    // Why a reply was not drafted (over today's AI budget, for one).
    note = [found, ...(result.notes ?? [])].join(". ");
  }
  async function connect() {
    note = "";
    await run("connect", async () => {
      const { task_id } = await api<{ task_id: string }>("/rika/mail/connect", {});
      note = "Finish signing in to Google in the browser window that opened.";
      let task = await api<TaskRecord>(`/tasks/${task_id}`);
      while (task.status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        task = await api<TaskRecord>(`/tasks/${task_id}`);
      }
      if (task.status === "failed") throw new Error(task.error || "Gmail did not connect");
      note = "";
      await refresh();
    });
  }

  function adopt(draft: RikaDraft) {
    if (mail) mail = { ...mail, drafts: mail.drafts.map((item) => (item.id === draft.id ? draft : item)) };
  }
  /** Saves an edited reply; returns false when the save failed. */
  async function save(draft: RikaDraft): Promise<boolean> {
    const body = edits[draft.id];
    if (body === undefined || body === draft.body) {
      delete edits[draft.id];
      return true;
    }
    const saved = await run(`save:${draft.id}`, () => api<{ draft: RikaDraft }>(`/rika/mail/drafts/${encodeURIComponent(draft.id)}`, { body }));
    if (!saved) return false;
    delete edits[draft.id];
    adopt(saved.draft);
    return true;
  }
  async function act(draft: RikaDraft, verb: "send" | "undo" | "discard") {
    if (verb === "send" && !(await save(draft))) return;
    busy = `${verb}:${draft.id}`;
    const next = await rika.mailAction(draft.id, verb);
    busy = "";
    if (next) adopt(next);
    else error = rika.error;
  }
  const time = (value: string | null | undefined) => {
    const date = new Date(value ?? "");
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };
</script>

<Panel title="Email" aria-label="Email">
  {#if mail}
    {@const box = mail.mailbox}
    <div class="rows">
      <div class="set-row">
        <span class="set-label">Gmail</span>
        {#if box.demo}
          <span class="set-desc"><span class="dot demo" aria-hidden="true"></span>Demo mailbox — nothing is really sent</span>
        {:else if box.connected}
          <span class="set-desc"><span class="dot" aria-hidden="true"></span>{box.account ? "Connected as " : "Connected"}{#if box.account}<strong>{box.account}</strong>{/if}</span>
        {:else}
          <span class="set-desc">{box.setup || "Not connected. Connect Gmail so Rika can draft replies to your mail."}</span>
        {/if}
        <span class="set-end">
          {#if !box.demo && !box.connected}
            <Button size="sm" intent="primary" disabled={busy === "connect"} onclick={connect}>{busy === "connect" ? "Connecting…" : "Connect"}</Button>
          {:else}
            {#if box.last_check}<span class="when" title="Last checked">{time(box.last_check)}</span>{/if}
            <Button size="sm" disabled={busy === "check"} onclick={check}>{busy === "check" ? "Checking…" : "Check mail now"}</Button>
          {/if}
        </span>
      </div>

      <div class="set-row section">
        <span class="set-label">Auto-send</span>
        <span class="set-desc">Off for every kind until you turn it on. Personal mail, questions and anything new always stay drafts.</span>
      </div>
      {#each mail.kinds as kind (kind.kind)}
        <div class="set-row">
          <button type="button" class="set-switch" role="switch" id={`${uid}-${kind.kind}`} aria-checked={kind.auto} aria-label={`Auto-send ${kind.label}`}
            aria-describedby={`${uid}-${kind.kind}-hint`} disabled={busy === `kind:${kind.kind}`} onclick={() => flip(kind)}><i></i></button>
          <label class="set-label" for={`${uid}-${kind.kind}`}>{kind.label}</label>
          <span class="set-desc" id={`${uid}-${kind.kind}-hint`}>{kind.hint}</span>
          <span class="set-end count">{kind.sent} sent</span>
        </div>
      {/each}
      <div class="set-row">
        <span class="set-label" id={`${uid}-hold`}>Hold each send</span>
        <span class="set-end">
          <Select label="Hold each send" value={String(hold)} options={box.hold_choices.map((seconds) => ({ value: String(seconds), label: `${seconds} s` }))} onchange={setHold} disabled={busy === "hold"} />
        </span>
      </div>
      <p class="set-note honest">Gmail's API can't delay or undo a send, so rika! holds each send on this machine for {hold} s; after that it can't be unsent. If rika! closes during the hold, the mail stays a draft.</p>

      <div class="set-row section">
        <span class="set-label">Drafts</span>
        <span class="set-desc">{drafts.length ? `${drafts.length} from Rika` : "Nothing yet"}</span>
      </div>
      {#if !drafts.length}
        <p class="set-note">No drafts. Rika writes one for each message that needs a reply; Check mail now looks for new mail.</p>
      {/if}
      <ul class="drafts" aria-label="Drafts">
        {#each drafts as draft (draft.id)}
          {@const left = holdLeft(draft, now)}
          {@const editable = draft.status === "draft" || draft.status === "cancelled"}
          <li class="draft" data-status={draft.status} aria-label={`Reply to ${draft.to}: ${draft.subject}`}>
            <header>
              <span class="to">To <strong>{draft.to}</strong></span>
              <span class="tag">{draft.auto ? `${draft.reply_kind} · auto-send` : draft.reply_kind}</span>
              <span class="state">{STATUS[draft.status]}</span>
            </header>
            <strong class="subject">{draft.subject}</strong>
            {#if draft.original}
              <blockquote class="original">
                <span class="from">{draft.original.sender_email ? `${draft.original.sender} <${draft.original.sender_email}>` : draft.original.sender} · {time(draft.original.received_at)}</span>
                <span>{draft.original.snippet}</span>
              </blockquote>
            {/if}
            {#if editable}
              <textarea aria-label={`Reply to ${draft.to}`} rows="4" value={edits[draft.id] ?? draft.body} disabled={busy === `send:${draft.id}`}
                oninput={(event) => (edits[draft.id] = event.currentTarget.value)} onblur={() => save(draft)}></textarea>
            {:else}
              <p class="body">{draft.body}</p>
            {/if}
            <div class="acts">
              {#if draft.status === "scheduled"}
                {#if left}
                  <Button size="sm" disabled={busy === `undo:${draft.id}`} onclick={() => act(draft, "undo")}>Undo send · {holdClock(left)}</Button>
                  <span class="status">Sends in {holdClock(left)}</span>
                {:else}<span class="status">Sending…</span>{/if}
              {:else if draft.status === "sent"}
                <span class="status">{draft.sent_at ? `Sent ${time(draft.sent_at)}` : "Sent"} — Gmail can't unsend it</span>
              {:else if draft.status === "failed"}
                <span class="status failed">{draft.error ? `Failed: ${draft.error}` : "Failed"}</span>
              {:else if editable}
                <Button size="sm" intent="primary" disabled={busy === `send:${draft.id}`} onclick={() => act(draft, "send")}>Send</Button>
                <Button size="sm" variant="ghost" disabled={busy === `discard:${draft.id}`} onclick={() => act(draft, "discard")}>Discard</Button>
                {#if draft.status === "cancelled"}<span class="status">{draft.error || "Send cancelled; still a draft"}</span>{/if}
              {/if}
            </div>
          </li>
        {/each}
      </ul>
    </div>
  {:else if !error}
    <p class="set-note" role="status">Loading email…</p>
  {/if}
  {#if note}<p class="set-note" role="status">{note}</p>{/if}
  {#if error}<p class="set-note" role="alert">{error} <button class="text-button" onclick={refresh}>Retry</button></p>{/if}
</Panel>

<dialog bind:this={dialog} class="workspace-dialog auto-ask" aria-labelledby={`${uid}-ask-title`} oncancel={() => (asking = null)}>
  {#if asking}
    <header><h2 id={`${uid}-ask-title`}>Auto-send {asking.label.toLowerCase()}?</h2></header>
    <p>Rika will send {asking.label.toLowerCase()} replies without asking you first. Each one is held for {hold} s so you can undo it; after that Gmail can't unsend it.</p>
    <footer>
      <Button variant="ghost" onclick={closeAsk}>Cancel</Button>
      <Button intent="primary" onclick={confirmAuto}>Turn on</Button>
    </footer>
  {/if}
</dialog>

<style>
  .dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    margin-right: 7px;
    border-radius: 999px;
    background: var(--success);
    vertical-align: 1px;
  }
  .dot.demo {
    background: var(--warning);
  }
  strong {
    color: var(--ink);
    font-weight: 500;
  }
  .when,
  .count {
    color: var(--muted);
    font: 11px var(--font-mono);
    white-space: nowrap;
  }
  .section .set-label {
    color: var(--muted);
    font: 600 var(--fs-label) var(--font-ui);
  }
  .honest {
    margin: 0 0 8px;
    line-height: 1.5;
  }
  .drafts {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .draft {
    display: grid;
    gap: 6px;
    margin: 0 calc(-1 * var(--br-panel-p));
    padding: 10px var(--br-panel-p);
    border-top: 1px solid var(--hairline);
    font-size: 13px;
  }
  .draft header {
    display: flex;
    align-items: baseline;
    gap: 10px;
    min-width: 0;
  }
  .to {
    min-width: 0;
    overflow: hidden;
    color: var(--muted);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tag,
  .state {
    color: var(--subtle);
    font: 500 9.5px var(--font-mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    white-space: nowrap;
  }
  .state {
    margin-left: auto;
    color: var(--tone, var(--accent));
  }
  .draft[data-status="scheduled"] {
    --tone: var(--c3, var(--violet));
  }
  .draft[data-status="sent"] {
    --tone: var(--success);
  }
  .draft[data-status="cancelled"] {
    --tone: var(--subtle);
  }
  .draft[data-status="failed"] {
    --tone: var(--danger);
  }
  .subject {
    font-weight: 600;
  }
  .original {
    display: grid;
    gap: 2px;
    margin: 0;
    padding: 2px 0 2px 10px;
    border-left: 2px solid var(--hairline);
    color: var(--muted);
    font-size: 12.5px;
  }
  .from {
    color: var(--subtle);
    font: 10.5px var(--font-mono);
  }
  textarea {
    width: 100%;
    min-width: 0;
    resize: vertical;
    /* The whole reply shows where the engine sizes fields to content; elsewhere it scrolls. */
    field-sizing: content;
    min-height: 5.5em;
    font-size: 12.5px;
    line-height: 1.45;
  }
  .body {
    margin: 0;
    color: var(--ink);
    font-size: 12.5px;
    line-height: 1.45;
    white-space: pre-line;
  }
  .acts {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .status {
    color: var(--muted);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }
  .status.failed {
    color: var(--danger);
  }
  .auto-ask {
    max-width: 460px;
  }
  .auto-ask p {
    margin: 0;
    line-height: 1.55;
  }
</style>
