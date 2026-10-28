<script lang="ts">
  import { onMount } from "svelte";
  import {
    TINTS, createQuickNote, deleteQuickNote, getQuickNote, isConflict, noteTitle, popOutQuickNote,
    saveQuickNote, type QuickNote, type QuickNotePatch, type Tint,
  } from "./quickNotes";

  let {
    note = null,
    variant = "card",
    autofocus = false,
    onsaved,
    ondeleted,
  }: {
    note?: QuickNote | null;
    variant?: "card" | "window";
    autofocus?: boolean;
    onsaved?: (note: QuickNote) => void;
    ondeleted?: (id: number) => void;
  } = $props();

  // svelte-ignore state_referenced_locally
  let current = $state<QuickNote | null>(note);
  // svelte-ignore state_referenced_locally
  let body = $state(note?.body ?? "");
  // svelte-ignore state_referenced_locally
  let tint = $state<Tint>(note?.tint ?? "rose");
  // svelte-ignore state_referenced_locally
  let pinned = $state(note?.pinned ?? false);
  let dirty = false;
  let status = $state<"saved" | "editing" | "saving" | "conflict" | "error">("saved");
  let message = $state("");
  let timer: ReturnType<typeof setTimeout> | undefined;
  let queue: Promise<unknown> = Promise.resolve();
  let editor = $state<HTMLTextAreaElement>();

  function adopt(next: QuickNote) {
    current = next;
    body = next.body;
    tint = next.tint;
    pinned = next.pinned;
    dirty = false;
    status = "saved";
  }
  // Edits from another window arrive through the parent's refresh; never clobber typing.
  $effect(() => {
    if (note && !dirty && status !== "conflict" && (!current || note.revision > current.revision)) adopt(note);
  });

  function schedule() {
    dirty = true;
    status = "editing";
    clearTimeout(timer);
    timer = setTimeout(() => void flush(), 450);
  }

  function flush(patch: QuickNotePatch = {}) {
    clearTimeout(timer);
    queue = queue.then(async () => {
      if (status === "conflict") return;
      const draft = body;
      try {
        if (!current) {
          if (!draft.trim() && !Object.keys(patch).length) return;
          status = "saving";
          current = await createQuickNote(draft, tint);
          if (pinned) current = await saveQuickNote(current, { pinned });
          if (variant === "window") history.replaceState(null, "", `#quick-note/${current.id}`);
        } else {
          const changes: QuickNotePatch = dirty ? { ...patch, body: draft } : patch;
          if (!Object.keys(changes).length) return;
          status = "saving";
          current = await saveQuickNote(current, changes);
        }
        if (body === draft) dirty = false;
        status = dirty ? "editing" : "saved";
        onsaved?.(current);
      } catch (cause) {
        status = isConflict(cause) ? "conflict" : "error";
        message = cause instanceof Error ? cause.message : "Could not save this note.";
      }
    });
    return queue;
  }

  async function loadLatest() {
    if (!current) return;
    try { adopt(await getQuickNote(current.id)); onsaved?.(current!); }
    catch (cause) { status = "error"; message = (cause as Error).message; }
  }
  async function keepMine() {
    if (!current) return;
    try {
      current = await getQuickNote(current.id);
      status = "saving";
      dirty = true;
      await flush({ tint, pinned });
    } catch (cause) { status = "error"; message = (cause as Error).message; }
  }

  function chooseTint(next: Tint) {
    tint = next;
    void flush({ tint: next });
  }
  function togglePin() {
    pinned = !pinned;
    void flush({ pinned });
  }
  async function remove() {
    if (body.trim() && !window.confirm(`Delete “${noteTitle(body)}”?`)) return;
    await flush();
    try {
      if (current) {
        await deleteQuickNote(current);
        ondeleted?.(current.id);
      }
      if (variant === "window") window.close();
    } catch (cause) {
      status = isConflict(cause) ? "conflict" : "error";
      message = (cause as Error).message;
    }
  }
  async function popOut() {
    await flush();
    if (current) popOutQuickNote(current.id);
  }
  async function close() {
    await flush();
    window.close();
  }

  function since(value: string | undefined) {
    if (!value) return "new";
    const minutes = Math.round((Date.now() - Date.parse(value)) / 60000);
    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes}m ago`;
    if (minutes < 1440) return `${Math.round(minutes / 60)}h ago`;
    return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function keydown(event: KeyboardEvent) {
    if (variant === "window" && event.key === "Escape") { event.preventDefault(); void close(); }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); void flush(); }
  }

  onMount(() => {
    if (autofocus) editor?.focus();
    const hide = () => { if (document.visibilityState === "hidden") void flush(); };
    const leave = () => void flush();
    document.addEventListener("visibilitychange", hide);
    window.addEventListener("pagehide", leave);
    return () => {
      document.removeEventListener("visibilitychange", hide);
      window.removeEventListener("pagehide", leave);
      if (dirty) void flush();
    };
  });
</script>

<article class="quick-note {variant}" data-tint={tint} class:pinned aria-label={`Quick note: ${noteTitle(body)}`}>
  <header>
    <span class="meta">{variant === "window" ? "kitty! · quick note" : pinned ? "pinned" : since(current?.updated_at)}</span>
    <div class="tools">
      <div class="tints" role="radiogroup" aria-label="Note color">
        {#each TINTS as option}
          <button type="button" role="radio" class="tint" data-tint={option} aria-checked={tint === option}
            aria-label={option} title={option} onclick={() => chooseTint(option)}></button>
        {/each}
      </div>
      <button type="button" class="tool" aria-pressed={pinned} title={pinned ? "Unpin" : "Pin to top"} aria-label={pinned ? "Unpin note" : "Pin note"} onclick={togglePin}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l-1 6 4 4H6l4-4ZM12 13v8" /></svg>
      </button>
      {#if variant === "card"}
        <button type="button" class="tool" title="Float in its own window" aria-label="Pop out note" onclick={popOut}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3h7v7m0-7-9 9M10 5H3v16h16v-7" /></svg>
        </button>
      {/if}
      <button type="button" class="tool" title="Delete" aria-label="Delete note" onclick={remove}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14" /></svg>
      </button>
      {#if variant === "window"}
        <button type="button" class="tool" title="Close (Esc) — already saved" aria-label="Close note window" onclick={close}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg>
        </button>
      {/if}
    </div>
  </header>
  <textarea bind:this={editor} bind:value={body} oninput={schedule} onblur={() => void flush()} onkeydown={keydown}
    placeholder="Jot something down…" aria-label="Quick note text" spellcheck="true"></textarea>
  <footer aria-live="polite">
    {#if status === "conflict"}
      <span class="warn">Changed in another window.</span>
      <button type="button" class="link" onclick={loadLatest}>Load latest</button>
      <span>{body.trim() ? `${body.split(/\s+/).filter(word => /[\p{L}\p{N}]/u.test(word)).length} words` : ""}</span>
    {:else if status === "error"}
      <span class="warn">{message}</span>
      <button type="button" class="link" onclick={() => { status = "editing"; void flush(); }}>Retry</button>
    {:else}
      <span>{status === "saved" ? (current ? "saved" : "not saved yet") : status === "saving" ? "saving…" : "editing…"}</span>
      <span>{body.length ? `${body.trim().split(/\s+/).filter(Boolean).length} words` : ""}</span>
    {/if}
  </footer>
</article>

<style>
  .quick-note {
    --tint: #f2a7c3;
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-height: 230px;
    padding: 12px 16px 10px;
    border-radius: 16px;
    color: var(--ink);
    background:
      linear-gradient(155deg, color-mix(in srgb, var(--tint) 17%, transparent), color-mix(in srgb, var(--tint) 4%, transparent) 65%),
      color-mix(in srgb, var(--solid-panel, #1c1c1c) 42%, transparent);
    border: 1px solid color-mix(in srgb, var(--tint) 30%, transparent);
    box-shadow:
      inset 0 1px 0 rgb(255 255 255 / 0.07),
      0 22px 60px -28px color-mix(in srgb, var(--tint) 45%, transparent);
    backdrop-filter: blur(22px) saturate(150%);
    -webkit-backdrop-filter: blur(22px) saturate(150%);
    transition: border-color 160ms ease, box-shadow 160ms ease;
  }
  .quick-note:focus-within {
    border-color: color-mix(in srgb, var(--tint) 58%, transparent);
  }
  [data-tint="rose"] { --tint: #f2a7c3; }
  [data-tint="lilac"] { --tint: #c6b5f0; }
  [data-tint="sky"] { --tint: #9cc7f0; }
  [data-tint="mint"] { --tint: #a8e0c4; }
  [data-tint="honey"] { --tint: #f0d29a; }
  [data-tint="frost"] { --tint: #e6e6e6; }
  /* Browser popups keep a dense fallback over their opaque canvas. */
  .window {
    height: 100dvh;
    min-height: 0;
    border-radius: 18px;
    background:
      linear-gradient(155deg, color-mix(in srgb, var(--tint) 16%, transparent), transparent 70%),
      color-mix(in srgb, var(--solid-panel, #1c1c1c) 76%, transparent);
  }
  /* Native floating notes reveal the desktop; retain only the top color wash. */
  :global(:root[data-window="transparent"][data-surface="quick-note"]) .window {
    background:
      linear-gradient(155deg, color-mix(in srgb, var(--tint) 16%, transparent), transparent 70%);
  }
  :global(:root[data-window="transparent"][data-surface="quick-note"]) .window :is(header, footer) {
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 76%, transparent);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--solid-panel, #1c1c1c) 76%, transparent);
    border-radius: 4px;
  }
  :global(:root[data-window="transparent"][data-surface="quick-note"]) .window textarea {
    text-shadow: 0 1px 2px var(--solid-void, #141414), 0 0 2px var(--solid-void, #141414);
  }
  header, footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    font: 10.5px/1.4 var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: color-mix(in srgb, var(--ink) 68%, transparent);
  }
  .window header { cursor: default; user-select: none; }
  .meta { color: color-mix(in srgb, var(--tint) 75%, var(--ink)); white-space: nowrap; }
  .tools { display: flex; align-items: center; gap: 2px; opacity: 0.55; transition: opacity 140ms ease; }
  .quick-note:hover .tools, .quick-note:focus-within .tools, .window .tools { opacity: 1; }
  .tints { display: flex; gap: 4px; margin-right: 6px; }
  .tint {
    width: 11px; height: 11px; padding: 0; border-radius: 50%;
    background: var(--tint);
    border: 1px solid rgb(255 255 255 / 0.18);
    opacity: 0.55;
  }
  .tint[aria-checked="true"] { opacity: 1; box-shadow: 0 0 0 2px color-mix(in srgb, var(--tint) 40%, transparent); }
  .tint:hover { opacity: 1; }
  .tool {
    display: inline-grid; place-items: center;
    width: 26px; height: 26px; padding: 0;
    border-radius: 999px;
    background: transparent;
    color: color-mix(in srgb, var(--ink) 70%, transparent);
  }
  .tool:hover, .tool[aria-pressed="true"] { color: var(--ink); background: rgb(255 255 255 / 0.07); }
  .tool[aria-pressed="true"] { color: var(--tint); }
  .tool svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
  .quick-note textarea {
    flex: 1;
    min-height: 140px;
    width: 100%;
    margin: 0;
    padding: 0;
    resize: none;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: var(--ink);
    font: 15px/1.65 var(--font-ui);
    caret-color: var(--tint);
  }
  .quick-note textarea:focus-visible { outline: none; }
  .quick-note textarea::placeholder { color: color-mix(in srgb, var(--ink) 38%, transparent); }
  .warn { color: var(--warning); text-transform: none; letter-spacing: 0; font-family: var(--font-ui); font-size: 12px; }
  .link {
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--tint) 40%, transparent);
    background: transparent;
    color: var(--ink);
    font: inherit;
  }
</style>
