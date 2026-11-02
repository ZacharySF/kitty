<script lang="ts">
  /**
   * The build editor (Q9): config/build.json in a pane docked at the right of the
   * workbench. Every pause in typing is checked by the server and its look shown in
   * this window at once; valid text saves itself. Problems are marked at their lines.
   */
  import { onMount, untrack } from "svelte";
  import { api, ApiError, type Problem } from "./api";
  import { buildStore, previewLook, reloadBuild } from "./build.svelte";
  import { lineOffset, problemLines, type Build } from "./buildCore";

  let { onclose }: { onclose: () => void } = $props();

  const LINE = 20;
  const PAD = 8;
  const CHECK_MS = 250;
  const SAVE_MS = 600;
  const MIN_WIDTH = 320;

  let text = $state(buildStore.text);
  /** The file text this editor last loaded or saved, and its revision (`base` for saves). */
  let saved = $state(buildStore.text);
  let base = $state(buildStore.revision);
  let problems = $state.raw<Problem[]>(buildStore.problems);
  let status = $state("");
  let conflict = $state(false);
  let saving = $state(false);
  /** The text of the save in flight. */
  let sending: string | null = null;
  let scrollTop = $state(0);
  let scrollLeft = $state(0);
  let width = $state(Math.min(560, Math.max(MIN_WIDTH, Math.round(window.innerWidth * 0.4))));
  let field = $state<HTMLTextAreaElement>();
  let checkTimer: ReturnType<typeof setTimeout> | undefined;
  let saveTimer: ReturnType<typeof setTimeout> | undefined;
  /** The last check: its text and whether that text is a valid build. */
  let checked: { text: string; ok: boolean } | null = null;

  const dirty = $derived(text !== saved);
  const lineCount = $derived(text.split("\n").length);
  const marks = $derived(problemLines(problems, lineCount));
  const errors = $derived(problems.filter((problem) => problem.severity === "error").length);

  async function check(snapshot: string): Promise<boolean> {
    if (checked?.text === snapshot) return checked.ok;
    try {
      const result = await api<{ ok: boolean; build: Build | null; problems: Problem[] }>("/build/check", { text: snapshot });
      if (snapshot !== text) return result.ok;
      checked = { text: snapshot, ok: result.ok };
      problems = result.problems;
      previewLook(result.ok && result.build ? result.build.theme : null);
      status = result.ok ? (dirty ? "Valid · not saved yet" : status) : `${result.problems.length} problem${result.problems.length === 1 ? "" : "s"} · not saved; the last good build stays applied`;
      return result.ok;
    } catch (cause) {
      status = `Could not check: ${(cause as Error).message}`;
      return false;
    }
  }

  async function revisionOf(value: string): Promise<string | null> {
    if (!crypto.subtle) return null;
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  async function save() {
    clearTimeout(saveTimer);
    if (saving || conflict || !dirty) return;
    const snapshot = text;
    if (!(await check(snapshot)) || snapshot !== text) return;
    saving = true;
    sending = snapshot;
    status = "Saving…";
    try {
      await api("/build/save", { text: snapshot, base });
      saved = snapshot;
      base = (await revisionOf(snapshot)) ?? base;
      status = "Saved · every window updated";
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        conflict = true;
        status = "";
      } else {
        if (cause instanceof ApiError && cause.problems.length) problems = cause.problems;
        status = (cause as Error).message;
      }
    } finally {
      saving = false;
      sending = null;
    }
    // Typing during the save is saved next.
    if (text !== saved && !conflict) schedule();
  }

  function schedule() {
    clearTimeout(checkTimer);
    clearTimeout(saveTimer);
    status = "Editing…";
    checkTimer = setTimeout(() => void check(text), CHECK_MS);
    saveTimer = setTimeout(() => void save(), SAVE_MS);
  }

  /** Changed elsewhere: take the file as it is now. */
  function reload() {
    clearTimeout(checkTimer);
    clearTimeout(saveTimer);
    conflict = false;
    text = saved = buildStore.text;
    base = buildStore.revision;
    problems = buildStore.problems;
    checked = null;
    previewLook(null);
    status = "Reloaded";
  }
  /** Changed elsewhere: save this text over it. */
  async function keepMine() {
    try {
      await reloadBuild();
    } catch (cause) {
      status = `Could not reach kitty: ${(cause as Error).message}`;
      return;
    }
    base = buildStore.revision;
    conflict = false;
    await save();
  }

  // A file change from anywhere: adopt it unless this editor has unsaved edits.
  $effect(() => {
    const revision = buildStore.revision;
    const fileText = buildStore.text;
    const fileProblems = buildStore.problems;
    untrack(() => {
      if (revision === base) return;
      // This editor's own save, whether its push or its answer came first.
      if (fileText === text || fileText === sending) {
        base = revision;
        saved = fileText;
        if (fileText === text) problems = fileProblems;
        return;
      }
      if (dirty) {
        conflict = true;
        return;
      }
      text = saved = fileText;
      base = revision;
      problems = fileProblems;
      checked = null;
      previewLook(null);
      status = buildStore.invalid ? "Changed elsewhere with an error · the last good build stays applied" : "Changed elsewhere · reloaded";
    });
  });

  function goTo(problem: Problem) {
    if (!field) return;
    const offset = lineOffset(text, problem.line, problem.column);
    field.focus();
    field.setSelectionRange(offset, offset);
    field.scrollTop = Math.max(0, (problem.line - 1) * LINE - field.clientHeight / 2);
  }

  function keydown(event: KeyboardEvent) {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "s") {
      event.preventDefault();
      void save();
    }
  }

  let drag: { pointer: number; start: number; from: number } | null = null;
  const clampWidth = (value: number) => Math.round(Math.max(MIN_WIDTH, Math.min(value, window.innerWidth - 360)));
  function dragStart(event: PointerEvent) {
    if (event.button !== 0) return;
    event.preventDefault();
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    drag = { pointer: event.pointerId, start: event.clientX, from: width };
  }
  function dragMove(event: PointerEvent) {
    if (drag?.pointer === event.pointerId) width = clampWidth(drag.from + drag.start - event.clientX);
  }
  function dragEnd(event: PointerEvent) {
    if (drag?.pointer === event.pointerId) drag = null;
  }
  function dividerKey(event: KeyboardEvent) {
    const step = { ArrowLeft: 24, ArrowRight: -24 }[event.key];
    if (step === undefined) return;
    event.preventDefault();
    width = clampWidth(width + step);
  }

  // A real split: the workbench narrows by the editor's width.
  $effect(() => {
    document.documentElement.style.setProperty("--build-editor-space", `${width}px`);
  });
  onMount(() => {
    field?.focus();
    return () => {
      clearTimeout(checkTimer);
      clearTimeout(saveTimer);
      previewLook(null);
      document.documentElement.style.removeProperty("--build-editor-space");
    };
  });
</script>

<aside class="build-editor" aria-label="Build editor" style:width={`${width}px`}>
  <!-- A focusable separator with a value is the WAI-ARIA window splitter, an interactive widget. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
  <div class="divider" role="separator" aria-label="Resize build editor" aria-orientation="vertical"
    aria-valuenow={width} aria-valuemin={MIN_WIDTH} tabindex="0"
    onpointerdown={dragStart} onpointermove={dragMove} onpointerup={dragEnd} onpointercancel={dragEnd} onkeydown={dividerKey}></div>
  <header>
    <h2>Build editor</h2>
    <span class="file">config/build.json</span>
    <button class="text-button" onclick={() => void save()} disabled={!dirty || saving || conflict} title="Ctrl+S">Save</button>
    <button class="icon-button" aria-label="Close build editor" onclick={onclose}>×</button>
  </header>
  {#if conflict}
    <div class="conflict" role="alert">
      <span>The build file changed elsewhere (another window or editor saved it).</span>
      <button class="text-button" onclick={reload}>Reload</button>
      <button class="text-button" onclick={() => void keepMine()}>Keep mine</button>
    </div>
  {/if}
  {#if buildStore.restartRequired}<p class="note" role="status">Module changes apply after restarting kitty.</p>{/if}
  <div class="body">
    <div class="gutter" aria-hidden="true">
      <div style:transform={`translateY(${-scrollTop}px)`}>
        {#each { length: lineCount } as _, index}
          {@const line = index + 1}
          <div class="number" class:error={marks.has(line)} title={marks.get(line)?.map((item) => item.message).join("\n")}>{line}</div>
        {/each}
      </div>
    </div>
    <div class="text">
      <div class="marks" aria-hidden="true" style:transform={`translate(${-scrollLeft}px, ${-scrollTop}px)`}>
        {#each [...marks] as [line, items] (line)}
          <div class="mark" class:warning={items.every((item) => item.severity === "warning")} data-problem-line={line} style:top={`${PAD + (line - 1) * LINE}px`}></div>
        {/each}
      </div>
      <textarea
        bind:this={field}
        bind:value={text}
        aria-label="Build file (JSON)"
        aria-invalid={errors > 0}
        spellcheck="false"
        autocomplete="off"
        wrap="off"
        oninput={schedule}
        onkeydown={keydown}
        onscroll={(event) => { scrollTop = event.currentTarget.scrollTop; scrollLeft = event.currentTarget.scrollLeft; }}
      ></textarea>
    </div>
  </div>
  <footer>
    {#if status}<p class="status" role="status">{status}</p>{/if}
    {#if problems.length}
      <ul class="problems" aria-label="Problems">
        {#each problems as problem}
          <li><button class:warning={problem.severity === "warning"} onclick={() => goTo(problem)}>
            <strong>Line {problem.line}{#if problem.column}:{problem.column}{/if}</strong> {problem.message}{#if problem.path} <code>{problem.path}</code>{/if}
          </button></li>
        {/each}
      </ul>
    {/if}
  </footer>
</aside>

<style>
  .build-editor {
    position: fixed;
    top: 0;
    right: 0;
    bottom: var(--dock-space, 0px);
    z-index: 35;
    display: flex;
    flex-direction: column;
    min-width: 0;
    border-left: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--solid-void, #141414) 92%, transparent);
    backdrop-filter: blur(20px) saturate(140%);
  }
  .divider {
    position: absolute;
    left: -4px;
    top: 0;
    bottom: 0;
    width: 8px;
    cursor: ew-resize;
    touch-action: none;
  }
  .divider:hover,
  .divider:focus-visible {
    background: color-mix(in srgb, var(--br-structure, #deb2c3) 35%, transparent);
    outline: none;
  }
  header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 12px 8px;
    border-bottom: 1px solid var(--hairline);
  }
  h2 { margin: 0; font-size: 0.95rem; }
  .file { flex: 1; color: var(--muted); font: 11px var(--font-mono); }
  .conflict, .note {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin: 8px 12px 0;
    padding: 6px 10px;
    border: 1px solid var(--warning, var(--border));
    border-radius: 8px;
    font-size: 0.8rem;
  }
  .body {
    flex: 1;
    min-height: 0;
    display: flex;
    margin: 8px 12px;
    border: 1px solid var(--border-fine, var(--border));
    border-radius: 8px;
    overflow: hidden;
    background: var(--solid-inset, #111111);
    font: 12.5px/20px var(--font-mono);
  }
  .gutter {
    flex: none;
    min-width: 3.2em;
    padding: 8px 6px 8px 0;
    overflow: hidden;
    text-align: right;
    color: var(--muted);
    border-right: 1px solid var(--border-fine, var(--border));
    user-select: none;
  }
  .number { height: 20px; padding-left: 6px; }
  .number.error { color: var(--danger, #ee928c); font-weight: 700; box-shadow: inset 3px 0 var(--danger, #ee928c); }
  .text { position: relative; flex: 1; min-width: 0; }
  .marks { position: absolute; inset: 0; pointer-events: none; }
  .mark {
    position: absolute;
    left: 0;
    width: 100000px;
    height: 20px;
    background: color-mix(in srgb, var(--danger, #ee928c) 22%, transparent);
    border-bottom: 1px dashed var(--danger, #ee928c);
  }
  .mark.warning { background: color-mix(in srgb, var(--caution, #e8c872) 18%, transparent); border-bottom-color: var(--caution, #e8c872); }
  textarea {
    position: relative;
    display: block;
    width: 100%;
    height: 100%;
    margin: 0;
    padding: 8px;
    border: 0;
    outline: none;
    resize: none;
    background: transparent;
    color: var(--ink);
    font: inherit;
    white-space: pre;
    overflow: auto;
    tab-size: 2;
  }
  footer { padding: 0 12px 10px; max-height: 35%; overflow: auto; }
  .status { margin: 0 0 6px; color: var(--muted); font-size: 0.8rem; }
  .problems { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }
  .problems button {
    width: 100%;
    text-align: left;
    padding: 4px 8px;
    border: 1px solid color-mix(in srgb, var(--danger, #ee928c) 60%, transparent);
    border-radius: 6px;
    background: transparent;
    color: var(--ink);
    font-size: 0.8rem;
    cursor: pointer;
  }
  .problems button.warning { border-color: color-mix(in srgb, var(--caution, #e8c872) 60%, transparent); }
  .problems code { color: var(--muted); font-size: 0.75rem; }
</style>
