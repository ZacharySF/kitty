<script lang="ts">
  import { tick } from "svelte";
  import BriefChip from "../goals/BriefChip.svelte";
  import Select from "../ui/Select.svelte";
  import RikaAgents from "./RikaAgents.svelte";
  import RikaPlan from "./RikaPlan.svelte";
  import RikaMark from "./RikaMark.svelte";
  import { activeModules } from "../modules.svelte";
  import { costLabel, entryKey, holdClock, holdLeft, mailLine, talkClock, usageReadout, voiceEnabled } from "./rika";
  import { rika, type DrawerSection } from "./session.svelte";

  /**
   * Q2 D, Q7 A, Q13, Q16 B, Q19 A, Q20 A: what the home panel and the drawer show.
   * Conversation holds every request and reply (with its tokens and cost readout) and the
   * actions they ran (Undo), the approvals they wait on (Approve / Reject, with what will
   * happen and what it may cost), brain-dump plans and every reply email Rika drafted or
   * sent (a held send counts down with Undo send); Agents holds the live worker cards. A
   * brain dump's live transcript runs at the end until Make plan. `brief` shows the morning
   * brief's chip (only on the copy in view, so Ctrl K opens one popover).
   */
  let { brief = false }: { brief?: boolean } = $props();
  const STATUS = { done: "Done", undone: "Undone", failed: "Failed", approved: "Approved", rejected: "Rejected", expired: "Expired", pending: "Needs you" } as const;
  let list = $state<HTMLElement>();
  let transcript = $state<HTMLElement>();
  const dumping = $derived(rika.listening && rika.voice.mode === "dump");
  const voiceOn = $derived(voiceEnabled(activeModules));
  const time = (value: string | undefined) => {
    const date = new Date(value ?? "");
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };
  // New entries scroll into view, so an approval or plan that arrived is in sight, and a
  // brain dump's transcript follows what is being said.
  $effect(() => {
    void rika.entries.length;
    void rika.voice.segments.length;
    void rika.voice.partial;
    if (rika.drawer.section !== "conversation") return;
    void tick().then(() => (transcript ?? list?.lastElementChild)?.scrollIntoView({ block: "end" }));
  });
  // Q16: a held send counts down while it can still be undone.
  const MAIL_STATUS = { draft: "Draft", scheduled: "Sending", sent: "Sent", cancelled: "Cancelled", discarded: "Discarded", failed: "Failed" } as const;
  // A discarded draft leaves the conversation (the stream still says so, for the Email page).
  const entries = $derived(rika.entries.filter((entry) => entry.kind !== "mail" || entry.status !== "discarded"));
  let now = $state(Date.now());
  const holding = $derived(rika.entries.some((entry) => entry.kind === "mail" && entry.status === "scheduled"));
  $effect(() => {
    if (!holding) return;
    now = Date.now();
    const timer = setInterval(() => (now = Date.now()), 250);
    return () => clearInterval(timer);
  });
</script>

<div class="top">
  <Select label="Rika sections" kind="segments" value={rika.drawer.section}
    options={[{ value: "conversation", label: "Conversation" }, { value: "agents", label: "Agents", count: rika.agents.length }]}
    onchange={(section) => rika.showDrawer(section as DrawerSection)} />
  {#if brief}<BriefChip />{/if}
</div>
{#if rika.error}<p class="error" role="alert">{rika.error}</p>{/if}
{#if rika.drawer.section === "agents"}
  <RikaAgents />
{:else}
  <section class="conversation" aria-label="Conversation">
    {#if rika.hasMore}<button class="pill earlier" onclick={() => rika.loadHistory(true)}>Load earlier</button>{/if}
    {#if !rika.entries.length && !dumping}<p class="note">{voiceOn ? "Ask anything below. Hold Space to talk." : "Ask anything below."}</p>{/if}
    <ol bind:this={list}>
      {#each entries as entry (entryKey(entry))}
        <li data-entry={entry.kind}>
          {#if entry.kind === "message" && entry.role === "user"}
            <div class="you" class:dump={entry.source === "dump"}>
              <span class="meta-line"><time>{time(entry.created_at)}</time>{#if entry.source === "dump"}<span>brain dump</span>{:else if entry.source === "voice"}<span>spoken</span>{/if}</span>
              <p>{entry.text}</p>
            </div>
          {:else if entry.kind === "message"}
            {@const readout = usageReadout(entry)}
            <div class="said">
              <span class="mark"><RikaMark size={12} active={!entry.text} /></span>
              <div>
                <p class:thinking={!entry.text}>{entry.text || "Thinking…"}</p>
                <span class="meta-line">
                  {#if readout}
                    <span class="rika-chip" data-tier={readout.tier} title={entry.model}>{readout.label}</span>
                    <span class="readout">{readout.detail}</span>
                    <time>{time(entry.created_at)}</time>
                  {:else}
                    {#if entry.tier}<span class="rika-chip" data-tier={entry.tier} title={entry.model}>{entry.tier}</span>{/if}
                    <time>{time(entry.created_at)}</time>
                    {#if costLabel(entry.cost_usd)}<span>{costLabel(entry.cost_usd)}</span>{/if}
                  {/if}
                  {#if entry.status === "failed"}<span class="failed">Failed</span>{/if}
                </span>
              </div>
            </div>
          {:else if entry.kind === "action"}
            <article class="card" data-status={entry.status} aria-label={entry.summary}>
              <header><span class="kind">{entry.tool.replaceAll("_", " ")}</span><span class="state">{STATUS[entry.status]}</span></header>
              <p>{entry.summary}</p>
              {#if entry.reversible && entry.status === "done"}<div class="decide"><button class="pill" onclick={() => rika.undo(entry.id)}>Undo</button></div>{/if}
            </article>
          {:else if entry.kind === "plan"}
            <RikaPlan plan={entry} />
          {:else if entry.kind === "mail"}
            {@const left = holdLeft(entry, now)}
            <article class="card mail" data-status={entry.status} aria-label={`Reply to ${entry.to}: ${entry.subject}`}>
              <header><span class="kind">{mailLine(entry)}</span><span class="state">{MAIL_STATUS[entry.status]}</span></header>
              <p class="to">To <b>{entry.to}</b></p>
              <p class="subject">{entry.subject}</p>
              <p class="snippet">{entry.body}</p>
              {#if entry.status === "scheduled"}
                <div class="decide">
                  {#if left}<button class="pill" onclick={() => rika.mailAction(entry.id, "undo")}>Undo send {holdClock(left)}</button>{:else}<span class="hold">Sending…</span>{/if}
                </div>
              {:else if entry.status === "sent"}
                <p class="sent">Sent. Gmail can't unsend it.</p>
              {:else if entry.status === "draft" || entry.status === "cancelled"}
                {#if entry.status === "cancelled" && entry.error}<p class="detail">{entry.error}</p>{/if}
                <div class="decide">
                  <button class="pill" onclick={() => rika.mailAction(entry.id, "discard")}>Discard</button>
                  <button class="pill primary" onclick={() => rika.mailAction(entry.id, "send")}>Send</button>
                </div>
              {:else if entry.status === "failed" && entry.error}
                <p class="failed">{entry.error}</p>
              {/if}
            </article>
          {:else}
            {@const status = entry.status ?? "pending"}
            <article class="card ask" data-status={status} aria-label={entry.summary}>
              <header><span class="kind">{entry.tool.replaceAll("_", " ")}</span><span class="state">{STATUS[status]}</span></header>
              <p>{entry.summary}</p>
              {#if entry.detail}<p class="detail">{entry.detail}</p>{/if}
              {#if costLabel(entry.cost_usd)}<p class="cost">Estimated cost <b>{costLabel(entry.cost_usd)}</b></p>{/if}
              {#if status === "pending"}
                <div class="decide">
                  <button class="pill" onclick={() => rika.decide(entry.id, "reject")}>Reject</button>
                  <button class="pill primary" onclick={() => rika.decide(entry.id, "approve")}>Approve</button>
                </div>
              {/if}
            </article>
          {/if}
        </li>
      {/each}
    </ol>
    {#if dumping}
      <section class="transcript" bind:this={transcript} aria-label="Brain dump transcript" aria-live="polite">
        <header>
          <span class="kind">Brain dump</span>
          <span class="state">{rika.voice.phase === "finishing" ? "Finishing" : rika.voice.phase === "listening" ? "Listening" : "Starting"}</span>
        </header>
        <p class="detail">Nothing changes until you approve the plan.</p>
        <ol>
          {#each rika.voice.segments as segment, index (index)}
            <li><time>{talkClock(segment.start)}</time><span>{segment.text}</span></li>
          {/each}
          {#if rika.voice.partial}<li class="now"><time>{talkClock(rika.voice.segments.at(-1)?.end ?? 0)}</time><span>{rika.voice.partial}</span></li>{/if}
        </ol>
        <div class="decide">
          <button class="pill" onclick={() => rika.cancel()}>Cancel</button>
          <button class="pill primary" disabled={rika.voice.phase === "finishing"} onclick={() => rika.finish()}>Make plan</button>
        </div>
      </section>
    {/if}
  </section>
{/if}

<style>
  .top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .error {
    margin: 0;
    color: var(--danger);
    font-size: 12px;
  }
  .note {
    margin: 0;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.5;
  }
  .conversation {
    display: grid;
    gap: 12px;
  }
  .earlier {
    justify-self: center;
    min-height: 24px;
    font-size: 11px;
  }
  ol {
    display: grid;
    gap: 12px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  p {
    margin: 0;
    font-size: 13px;
    line-height: 1.45;
    overflow-wrap: anywhere;
  }
  time,
  .meta-line {
    color: var(--subtle);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .you {
    display: grid;
    justify-items: end;
    gap: 3px;
    margin-left: 48px;
  }
  .you p {
    padding: 7px 11px;
    border-radius: 12px 12px 4px 12px;
    background: color-mix(in srgb, var(--ink) 9%, transparent);
  }
  .said {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 9px;
    margin-right: 24px;
  }
  .said > div {
    display: grid;
    gap: 4px;
  }
  .thinking {
    color: var(--muted);
  }
  .mark {
    display: grid;
    margin-top: 3px;
  }
  .meta-line {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .failed {
    color: var(--danger);
  }
  .card {
    --tone: var(--success);
    display: grid;
    gap: 4px;
    margin-left: 21px;
    padding: 8px 10px;
    border: 1px solid var(--hairline);
    border-radius: 10px;
    background: color-mix(in srgb, var(--solid-raised) 55%, transparent);
  }
  .card[data-status="undone"],
  .card[data-status="rejected"],
  .card[data-status="expired"] {
    --tone: var(--subtle);
  }
  .card[data-status="failed"] {
    --tone: var(--danger);
  }
  .card.ask[data-status="pending"] {
    --tone: var(--warning);
    border-color: color-mix(in srgb, var(--warning) 45%, var(--hairline));
  }
  .card header,
  .transcript header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
  }
  .kind {
    color: var(--muted);
    font-size: 12px;
  }
  .kind::first-letter {
    text-transform: uppercase;
  }
  .state {
    color: var(--tone);
    font-size: 12px;
    font-weight: 500;
  }
  .detail {
    color: var(--muted);
  }
  .cost {
    color: var(--muted);
    font-size: 12px;
  }
  .cost b {
    color: var(--ink);
    font-weight: 600;
  }
  .decide {
    display: flex;
    justify-content: end;
    gap: 6px;
    margin-top: 4px;
  }
  .decide .pill {
    min-height: 26px;
    padding: 3px 12px;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }
  .readout {
    overflow-wrap: anywhere;
  }
  /* Q16: a reply draft; violet while held, green once sent, grey once cancelled. */
  .card.mail {
    --tone: var(--accent);
  }
  .card.mail[data-status="scheduled"] {
    --tone: var(--c3, var(--violet));
  }
  .card.mail[data-status="sent"] {
    --tone: var(--success);
  }
  .card.mail:is([data-status="cancelled"], [data-status="discarded"]) {
    --tone: var(--subtle);
  }
  .card.mail[data-status="failed"] {
    --tone: var(--danger);
  }
  .to {
    color: var(--muted);
    font-size: 12px;
  }
  .to b {
    color: var(--ink);
    font-weight: 500;
  }
  .subject {
    font-weight: 600;
  }
  .snippet {
    display: -webkit-box;
    overflow: hidden;
    color: var(--muted);
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
  }
  .hold,
  .sent {
    color: var(--muted);
    font-size: 12px;
  }
  /* A dump's whole transcript is in its note; the conversation shows its start. */
  .you.dump p {
    display: -webkit-box;
    overflow: hidden;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 4;
    line-clamp: 4;
  }
  .transcript {
    --tone: var(--accent);
    display: grid;
    gap: 8px;
    padding: 10px 12px;
    border: 1px solid color-mix(in srgb, var(--accent) 45%, var(--hairline));
    border-radius: 10px;
    background: color-mix(in srgb, var(--solid-raised) 55%, transparent);
  }
  .transcript ol {
    gap: 5px;
  }
  .transcript li {
    display: grid;
    grid-template-columns: 38px minmax(0, 1fr);
    gap: 8px;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.45;
  }
  .transcript li.now {
    color: var(--ink);
  }
</style>
