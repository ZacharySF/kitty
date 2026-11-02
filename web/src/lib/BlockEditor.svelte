<script module lang="ts">
  /** A page as GET /api/blocks/page names it. */
  export type PageRef = { page: string } | { type: "page" | "document" | "quick_note"; number: number };
  /** What a block drag carries, so other drop targets (the Notes sidebar, page tabs) can take blocks. */
  export const BLOCK_DRAG_TYPE = "application/x-kitty-block-drag";
  const CLIPBOARD_TYPE = "application/x-kitty-blocks";
  export const BLOCK_TYPES: { type: string; label: string; hint: string; keywords: string }[] = [
    { type: "text", label: "Text", hint: "", keywords: "paragraph plain" },
    { type: "heading_1", label: "Heading 1", hint: "#", keywords: "h1 title big" },
    { type: "heading_2", label: "Heading 2", hint: "##", keywords: "h2 subtitle" },
    { type: "heading_3", label: "Heading 3", hint: "###", keywords: "h3 small" },
    { type: "bulleted_list", label: "Bulleted list", hint: "-", keywords: "bullet ul unordered" },
    { type: "numbered_list", label: "Numbered list", hint: "1.", keywords: "number ol ordered" },
    { type: "to_do", label: "To-do", hint: "[]", keywords: "todo task checkbox check" },
    { type: "toggle", label: "Toggle", hint: "", keywords: "collapse fold details" },
    { type: "quote", label: "Quote", hint: ">", keywords: "blockquote citation" },
    { type: "callout", label: "Callout", hint: "", keywords: "note tip info" },
    { type: "code", label: "Code", hint: "```", keywords: "snippet pre" },
    { type: "divider", label: "Divider", hint: "---", keywords: "rule hr line separator" },
  ];
  export function pageQuery(ref: PageRef): string {
    return "page" in ref ? `page=${encodeURIComponent(ref.page)}` : `type=${ref.type}&number=${ref.number}`;
  }
</script>

<script lang="ts">
  import { onMount, tick, untrack, type Snippet } from "svelte";
  import { api } from "./api";
  import { onBlockChange, transact, whenLive, type BlockChange } from "./live";
  import { isTextType, visibleRows, type BlockData, type Op, type PageState } from "./blockDoc";
  import { PageSync, type Ack } from "./blockSync";
  import {
    ATOMIC, appendKeys, deleteBlocks, dropBlocks, firstBlock, indent, insertAfter, insertSpecs, mergeBackward, mergeForward,
    moveBlocks, outdent, pasteAfter, split, topBlocks, turnInto, type Edit,
  } from "./blockEdits";
  import { keyCommand, type Command } from "./blockKeymap";
  import { blocksMarkdown, concatMarks, marksHTML, marksOf, normalizeMarks, parseMarkdown, shortcut, sliceMarks, toggleMark, type Mark, type Spec } from "./blockText";
  import { caretLines, caretOffsets, readEditable, setCaret, textRange } from "./blockDom";
  import {
    acceptOps, attachedTags, liveSuggestions, mergeSuggestions, rejectOps, suggestionsAt, unnamedTags, type SuggestionItem, type Suggestions,
  } from "./blockTags";
  import { quoteEdit } from "./mirror";

  let {
    target, label = "Page blocks", pages = [], extras, focusBlock = "",
    onrevision, onerror, onopenpage, oncreatepage, ontag,
  }: {
    target: PageRef;
    label?: string;
    /** Pages a `/` link can point to. */
    pages?: { number: number; title: string }[];
    /** Rendered under each block (references, citations). */
    extras?: Snippet<[BlockData]>;
    /** Reveal and focus this block. */
    focusBlock?: string;
    onrevision?: (from: number, to: number, own: boolean) => void;
    onerror?: (message: string) => void;
    onopenpage?: (pageBlockId: string) => void;
    /** Create a new page for a `/` sub-page; returns its page block. */
    oncreatepage?: () => Promise<{ page: string; title: string } | null>;
    /** Open a tag's page (a tag chip under a block was clicked). */
    ontag?: (tagId: string) => void;
  } = $props();

  let root = $state<HTMLDivElement>();
  let sync = $state.raw<PageSync | null>(null);
  let version = $state(0);
  let loadError = $state("");
  let error = $state("");
  let selected = $state<string[]>([]);
  let anchor = "";
  let head = "";
  let announcement = $state("");
  let composing: HTMLElement | null = null;
  let pressed: string | null = null;
  let drag: { ids: string[] } | null = null;
  let drop = $state<{ id: string; where: "before" | "after" | "inside" } | null>(null);
  type Slash = { id: string; start: number; query: string; index: number; x: number; y: number; stage: "types" | "pages" };
  let slash = $state<Slash | null>(null);
  let menu = $state<{ id: string; x: number; y: number } | null>(null);
  let link = $state<{ id: string; start: number; end: number; url: string; x: number; y: number } | null>(null);
  // Auto-tags (Q13): the server's keyword suggestions per block, tag names, and where the caret is.
  type TaggedState = PageState & { suggestions?: Suggestions; tags?: Record<string, string> };
  let suggestions = $state.raw<Suggestions>({});
  let tagNames = $state.raw<Record<string, string>>({});
  let caretAt = $state<{ id: string; start: number; end: number } | null>(null);
  /** The block the caret was last in (or the last quote): where a Mirror pane quote goes. */
  let quoteAnchor: string | null = null;
  let hintBox = $state<{ x: number; y: number } | null>(null);
  const lookedUp = new Set<string>();
  let namesLoading = false;
  let ownRanges: Range[] = [];
  const HIGHLIGHT = "kitty-tag-suggestion";

  const rows = $derived.by(() => {
    void version;
    // The page document edits blocks in place; fresh row objects let Svelte see each change.
    return sync ? visibleRows(sync.local).map((row) => ({ ...row, block: { ...row.block, properties: { ...row.block.properties } } })) : [];
  });
  const chosen = $derived(new Set(selected));
  const newId = () => crypto.randomUUID();

  onMount(() => {
    let disposed = false;
    let current: PageSync | null = null;
    const early: BlockChange[] = [];
    const stop = onBlockChange((change) => {
      if (current) current.receive(change);
      else if (change) early.push(change);
    });
    void (async () => {
      try {
        // Load after the socket is live, so every change after the loaded state is pushed here.
        await Promise.race([whenLive(), new Promise((resolve) => setTimeout(resolve, 5000))]);
        const state = await api<TaggedState>(`/blocks/page?${pageQuery(target)}`);
        if (disposed) return;
        adoptTags(state, true);
        current = new PageSync({
          state,
          send: transact,
          load: () => api<TaggedState>(`/blocks/page?page=${state.page_id}`).then((next) => { adoptTags(next, true); return next; }),
          onupdate: () => { version += 1; },
          onrevision: (from, to, own) => onrevision?.(from, to, own),
          onack: (ack: Ack) => adoptTags(ack),
          onerror: (message) => { error = message; onerror?.(message); },
          debounce: 250,
        });
        for (const change of early) current.receive(change);
        sync = current;
      } catch (cause) {
        if (!disposed) loadError = (cause as Error).message;
      }
    })();
    return () => {
      disposed = true;
      stop();
      current?.dispose();
      paintSuggestions(true);
    };
  });

  // Auto-tags ----------------------------------------------------------------------------

  function adoptTags(state: { suggestions?: Suggestions; tags?: Record<string, string> }, replace = false) {
    if (state.tags && Object.keys(state.tags).length) tagNames = { ...tagNames, ...state.tags };
    if (replace) suggestions = mergeSuggestions({}, state.suggestions);
    else if (state.suggestions) suggestions = mergeSuggestions(suggestions, state.suggestions);
  }

  /** Faint underlines under suggested keywords (CSS Custom Highlights: the editable's DOM is untouched). */
  function paintSuggestions(clear = false) {
    const registry = (globalThis.CSS as { highlights?: Map<string, Highlight> } | undefined)?.highlights;
    if (!registry || typeof Highlight === "undefined") return;
    let highlight = registry.get(HIGHLIGHT);
    if (!highlight) registry.set(HIGHLIGHT, (highlight = new Highlight()));
    for (const range of ownRanges) highlight.delete(range);
    ownRanges = [];
    const current = sync;
    if (clear || !current) return;
    for (const [id, entry] of Object.entries(suggestions)) {
      const element = current.local.has(id) ? editableOf(id) : null;
      if (!element || composing === element) continue;
      for (const item of liveSuggestions(current.local.get(id), entry)) {
        const range = textRange(element, item.start, item.end);
        highlight.add(range);
        ownRanges.push(range);
      }
    }
  }

  $effect(() => {
    void version;
    void suggestions;
    void sync;
    void tick().then(() => paintSuggestions());
  });

  /** Suggestions under the caret of block `id`, if any. */
  function suggestionsHere(id: string, start: number, end: number): SuggestionItem[] {
    const current = sync;
    if (!current?.local.has(id)) return [];
    return suggestionsAt(liveSuggestions(current.local.get(id), suggestions[id]), start, end);
  }

  const hint = $derived.by(() => {
    void version;
    const at = caretAt;
    const items = at ? suggestionsHere(at.id, at.start, at.end) : [];
    return at && items.length ? { id: at.id, items } : null;
  });

  $effect(() => {
    const current = hint;
    const element = current ? editableOf(current.id) : null;
    if (!current || !element) {
      hintBox = null;
      return;
    }
    const box = textRange(element, current.items[0].start, current.items[0].end).getBoundingClientRect();
    hintBox = { x: box.left, y: box.bottom + 4 };
  });

  function trackCaret() {
    const active = root?.ownerDocument.activeElement as HTMLElement | null | undefined;
    const id = active?.dataset?.editable;
    const caret = active && id && root?.contains(active) ? caretOffsets(active) : null;
    const next = caret && id ? { id, ...caret } : null;
    if (next?.id !== caretAt?.id || next?.start !== caretAt?.start || next?.end !== caretAt?.end) caretAt = next;
    if (next) quoteAnchor = next.id;
  }

  function acceptTag(id: string, item: SuggestionItem) {
    sync?.commit(acceptOps(id, item.tag));
    tagNames = { ...tagNames, [item.tag]: item.name };
    announcement = `Tagged #${item.name}`;
  }

  /** Ignore a suggestion or remove a tag: remembered for this block, so it never comes back. */
  function rejectTag(id: string, tag: string, name: string) {
    sync?.commit(rejectOps(id, tag));
    announcement = `#${name} will not be suggested or attached here again`;
  }

  // Tag chips need names; tags another window attached may be new to this one.
  $effect(() => {
    const missing = unnamedTags(rows.map((row) => row.block), tagNames).filter((tag) => !lookedUp.has(tag));
    if (!missing.length || namesLoading) return;
    untrack(() => {
      namesLoading = true;
      for (const tag of missing) lookedUp.add(tag);
      api<{ tags: { id: string; name: string }[] }>("/tags")
        .then((result) => { tagNames = { ...tagNames, ...Object.fromEntries(result.tags.map((tag) => [tag.id, tag.name])) }; })
        .catch(() => {})
        .finally(() => { namesLoading = false; });
    });
  });

  /** Send everything typed here and wait for the server to confirm it. */
  export function flush(): Promise<void> {
    return sync?.flush() ?? Promise.resolve();
  }
  /** Edits typed here that the server has not confirmed yet. */
  export function pending(): boolean {
    return sync?.pending ?? false;
  }
  /** Run another writer of this page (an HTTP save) without interleaving editor transactions. */
  export async function exclusive(write: () => Promise<number | null>): Promise<void> {
    if (sync) await sync.exclusive(() => write());
    else await write();
  }
  /** Move blocks (with their children) to the end of another page. */
  export async function moveToPage(ids: string[], page: PageRef): Promise<boolean> {
    const current = sync;
    if (!current) return false;
    const destination = await api<PageState>(`/blocks/page?${pageQuery(page)}`);
    if (destination.page_id === current.pageId) return false;
    const tops = topBlocks(current.local, ids);
    if (!tops.length) return false;
    const keys = appendKeys(destination.blocks.filter((block) => block.parent_id === destination.page_id).map((block) => block.order_key), tops.length);
    const reply = await current.transactNow(tops.map((block, index) => ({ op: "move", id: block.id, parent: destination.page_id, key: keys[index] })));
    selected = [];
    if (reply.type === "ack") announcement = `Moved ${tops.length} block${tops.length === 1 ? "" : "s"} to another page`;
    return reply.type === "ack";
  }

  $effect(() => {
    const id = focusBlock;
    if (id && sync) untrack(() => void reveal(id));
  });

  async function reveal(id: string) {
    const current = sync;
    if (!current?.local.has(id)) return;
    const ops: Op[] = [];
    let parent = current.local.get(id).parent_id;
    while (parent && parent !== current.pageId) {
      const block = current.local.get(parent);
      if (block.properties.collapsed) ops.push({ op: "set", id: parent, props: { collapsed: false } });
      parent = block.parent_id;
    }
    if (ops.length) current.commit(ops);
    await tick();
    focusAt(id, 0);
    root?.querySelector(`[data-block-id="${id}"]`)?.scrollIntoView({ block: "center" });
  }

  // The contenteditable of one block mirrors its text and marks; it is redrawn only when
  // the model differs from what the browser shows (another window's edit, a split),
  // never while an input method is composing in it.
  function editable(node: HTMLElement, value: { text: string; marks: Mark[] }) {
    let last = "";
    const render = (next: { text: string; marks: Mark[] }) => {
      if (composing === node) return;
      const wanted = normalizeMarks(next.marks, next.text.length);
      // Re-rendering the page calls this for every block; read the DOM only when this block's model moved.
      const key = JSON.stringify([next.text, wanted]);
      if (key === last) return;
      last = key;
      const shown = readEditable(node);
      if (shown.text === next.text && JSON.stringify(shown.marks) === JSON.stringify(wanted)) return;
      const caret = node.ownerDocument.activeElement === node ? caretOffsets(node) : null;
      node.innerHTML = marksHTML(next.text, wanted);
      if (caret) {
        // Keep the caret on the same text when an edit elsewhere in the block shifts it.
        let same = 0;
        while (same < shown.text.length && same < next.text.length && shown.text[same] === next.text[same]) same += 1;
        const shift = (offset: number) => Math.min(next.text.length, offset > same ? offset + next.text.length - shown.text.length : offset);
        setCaret(node, shift(caret.start), shift(caret.end));
      }
    };
    render(value);
    return { update: render };
  }

  function editableOf(id: string): HTMLElement | null {
    return root?.querySelector<HTMLElement>(`[data-editable="${id}"]`) ?? null;
  }

  function focusAt(id: string, start: number, end = start) {
    const element = editableOf(id);
    if (!element) {
      if (rows.some((row) => row.block.id === id)) selectRange(id, id);
      return;
    }
    selected = [];
    element.focus({ preventScroll: true });
    const length = sync?.local.has(id) ? sync.local.get(id).text.length : 0;
    setCaret(element, Math.min(start, length), Math.min(end, length));
    element.scrollIntoView({ block: "nearest" });
  }

  async function run(edit: Edit) {
    const current = sync;
    if (!current) return;
    if (edit.ops.length && !current.commit(edit.ops)) return;
    await tick();
    if (edit.select?.length) selectBlocks(edit.select);
    else if (edit.focus) focusAt(edit.focus.id, edit.focus.offset);
  }

  // Block selection (Esc, Shift+arrows, dragging across blocks) -------------------------

  function selectRange(from: string, to: string) {
    const ids = rows.map((row) => row.block.id);
    const [a, b] = [ids.indexOf(from), ids.indexOf(to)];
    if (a < 0 || b < 0) return;
    anchor = from;
    head = to;
    selected = ids.slice(Math.min(a, b), Math.max(a, b) + 1);
    window.getSelection()?.removeAllRanges();
    root?.focus({ preventScroll: true });
    announcement = `${selected.length} block${selected.length === 1 ? "" : "s"} selected`;
  }

  function selectBlocks(ids: string[]) {
    const order = rows.map((row) => row.block.id).filter((id) => ids.includes(id));
    if (order.length) selectRange(order[0], order[order.length - 1]);
  }

  function step(id: string, delta: number): string | null {
    const ids = rows.map((row) => row.block.id);
    return ids[ids.indexOf(id) + delta] ?? null;
  }

  function copyEntries(ids: string[]): { block: BlockData; depth: number }[] {
    const current = sync;
    if (!current) return [];
    const entries: { block: BlockData; depth: number }[] = [];
    for (const top of topBlocks(current.local, ids)) {
      for (const block of current.local.subtree(top.id)) if (isTextType(block.type)) entries.push({ block, depth: current.local.depth(block.id) });
    }
    return entries;
  }

  function writeClipboard(data: DataTransfer, entries: { block: BlockData; depth: number }[]) {
    data.setData("text/plain", blocksMarkdown(entries));
    data.setData(CLIPBOARD_TYPE, JSON.stringify(entries.map(({ block, depth }) => ({ type: block.type, text: block.text, props: block.properties, depth }))));
  }

  function inBlockMode(): boolean {
    return selected.length > 0 && !!root && root.ownerDocument.activeElement === root;
  }

  function onCopy(event: ClipboardEvent) {
    if (!inBlockMode() || !event.clipboardData) return;
    event.preventDefault();
    writeClipboard(event.clipboardData, copyEntries(selected));
  }

  function onCut(event: ClipboardEvent) {
    if (!inBlockMode() || !event.clipboardData || !sync) return;
    onCopy(event);
    void run(deleteBlocks(sync.local, selected));
  }

  function clipboardSpecs(data: DataTransfer): Spec[] {
    const kitty = data.getData(CLIPBOARD_TYPE);
    if (kitty) {
      try {
        const parsed = JSON.parse(kitty) as Spec[];
        return parsed.filter((spec) => isTextType(spec.type) && typeof spec.text === "string" && Number.isInteger(spec.depth))
          .map((spec) => ({ type: spec.type, text: spec.text, props: spec.props && typeof spec.props === "object" ? spec.props : {}, depth: spec.depth }));
      } catch {
        // Fall back to the plain text below.
      }
    }
    return parseMarkdown(data.getData("text/plain"));
  }

  function onDocumentPaste(event: ClipboardEvent) {
    if (!inBlockMode() || !event.clipboardData || !sync) return;
    event.preventDefault();
    const last = topBlocks(sync.local, selected).pop();
    if (last) void run(pasteAfter(sync.local, last.id, clipboardSpecs(event.clipboardData), newId));
  }

  function blockCommand(command: Command | null, event: KeyboardEvent): boolean {
    const current = sync;
    if (!current || !command) return false;
    const doc = current.local;
    switch (command) {
      case "selectNext":
      case "selectPrevious": {
        const next = step(head, command === "selectNext" ? 1 : -1) ?? head;
        selectRange(next, next);
        break;
      }
      case "extendDown":
      case "extendUp": {
        const next = step(head, command === "extendDown" ? 1 : -1) ?? head;
        selectRange(anchor, next);
        break;
      }
      case "moveUp":
      case "moveDown":
        void run(moveBlocks(doc, selected, command === "moveUp" ? -1 : 1));
        break;
      case "indent":
        void run({ ...indent(doc, selected), select: selected });
        break;
      case "outdent":
        void run({ ...outdent(doc, selected), select: selected });
        break;
      case "edit": {
        const block = doc.get(head);
        focusAt(head, block.text.length);
        break;
      }
      case "deleteBlocks":
        void run(deleteBlocks(doc, selected));
        break;
      case "clearSelection":
        selected = [];
        announcement = "Selection cleared";
        break;
      case "selectAll":
        if (rows.length) selectRange(rows[0].block.id, rows[rows.length - 1].block.id);
        break;
      default:
        return false;
    }
    event.preventDefault();
    return true;
  }

  function onRootKeydown(event: KeyboardEvent) {
    if (event.target !== root || !selected.length) return;
    blockCommand(keyCommand(event, { mode: "blocks" }), event);
  }

  // Typing in one block --------------------------------------------------------------

  function commitText(element: HTMLElement, id: string, inputType = "", data: string | null = null) {
    const current = sync;
    if (!current?.local.has(id)) return;
    const block = current.local.get(id);
    const { text, marks } = readEditable(element);
    const caret = caretOffsets(element);
    const typed = shortcut(text, block.type);
    if (typed && inputType === "insertText" && caret && caret.start === typed.strip) {
      const rest = sliceMarks(marks, typed.strip, text.length);
      const ops: Op[] = [
        { op: "text", id, text: typed.text },
        { op: "set", id, type: typed.type, props: { ...typed.props, marks: rest.length ? rest : null } },
      ];
      if (typed.type === "divider") {
        const next = insertAfter(current.local, id, "text", newId);
        void run({ ops: [...ops, ...next.ops], focus: next.focus });
      } else void run({ ops, focus: { id, offset: 0 } });
      slash = null;
      return;
    }
    const ops: Op[] = [];
    if (text !== block.text) ops.push({ op: "text", id, text });
    if (JSON.stringify(marks) !== JSON.stringify(normalizeMarks(marksOf(block), block.text.length))) ops.push({ op: "set", id, props: { marks: marks.length ? marks : null } });
    if (ops.length) current.commit(ops, { typing: true });
    if (inputType === "insertText" && data === "/" && caret) openSlash(id, caret.start - 1, element);
    else if (slash?.id === id) {
      // Typing after `/` filters the menu; in the page picker it filters pages.
      const from = slash.stage === "types" ? slash.start + 1 : slash.start;
      const valid = caret && caret.start >= from && (slash.stage === "pages" || text[slash.start] === "/");
      const query = valid ? text.slice(from, caret.start) : null;
      slash = query === null || query.length > 40 || /\s\s/.test(query) ? null : { ...slash, query, index: 0 };
    }
  }

  function onInput(event: Event, id: string) {
    const input = event as InputEvent;
    if (input.isComposing || composing) return;
    commitText(event.currentTarget as HTMLElement, id, input.inputType, input.data);
  }

  /** Operations replacing [start, end) of a block's text, keeping its inline marks around the edit. */
  function replaceOps(block: BlockData, start: number, end: number, value: string): Op[] {
    const marks = marksOf(block);
    const next = concatMarks(sliceMarks(marks, 0, start), start + value.length, sliceMarks(marks, end, block.text.length));
    return [
      { op: "text", id: block.id, text: block.text.slice(0, start) + value + block.text.slice(end) },
      ...(marks.length ? [{ op: "set" as const, id: block.id, props: { marks: next.length ? next : null } }] : []),
    ];
  }

  /** Replace part of a block's text (Notes links an unlinked mention this way). */
  export function replaceText(id: string, change: (text: string) => { start: number; end: number; text: string } | null): void {
    const current = sync;
    if (!current?.local.has(id)) return;
    const block = current.local.get(id);
    const edit = change(block.text);
    if (edit) current.commit(replaceOps(block, edit.start, edit.end, edit.text));
  }

  /**
   * Write a quote from the Mirror pane (Q11) as one transaction: after the top-level block
   * the caret was last in, else at the end of the page. The caret stays where it is, and
   * the next quote follows this one.
   */
  export function insertQuote(specs: Spec[]): boolean {
    const current = sync;
    if (!current) return false;
    const edit = quoteEdit(current.local, quoteAnchor, specs, newId);
    if (!current.commit(edit.ops)) return false;
    const head = edit.ops[0];
    if (head.op === "insert") quoteAnchor = head.id;
    announcement = "Quote added";
    return true;
  }

  function insertPlain(element: HTMLElement, id: string, value: string) {
    const current = sync;
    if (!current) return;
    const block = current.local.get(id);
    const caret = caretOffsets(element) ?? { start: block.text.length, end: block.text.length };
    void run({ ops: replaceOps(block, caret.start, caret.end, value), focus: { id, offset: caret.start + value.length } });
  }

  function onPaste(event: ClipboardEvent, id: string) {
    const current = sync;
    if (!current || !event.clipboardData) return;
    event.preventDefault();
    const element = event.currentTarget as HTMLElement;
    const block = current.local.get(id);
    const plain = event.clipboardData.getData("text/plain");
    if (block.type === "code" || (!event.clipboardData.getData(CLIPBOARD_TYPE) && !/\n/.test(plain.trim()))) {
      insertPlain(element, id, block.type === "code" ? plain : plain.replace(/\r?\n/g, " "));
      return;
    }
    const caret = caretOffsets(element) ?? { start: block.text.length, end: block.text.length };
    void run(insertSpecs(current.local, id, caret.start, caret.end, clipboardSpecs(event.clipboardData), newId));
  }

  function toggle(element: HTMLElement, id: string, type: "b" | "i" | "c") {
    const current = sync;
    const caret = caretOffsets(element);
    if (!current || !caret || caret.start === caret.end) return;
    const block = current.local.get(id);
    const marks = toggleMark(block.text, marksOf(block), caret.start, caret.end, type);
    current.commit([{ op: "set", id, props: { marks: marks.length ? marks : null } }]);
    void tick().then(() => setCaret(element, caret.start, caret.end));
  }

  function textCommand(command: Command | null, event: KeyboardEvent, element: HTMLElement, id: string): boolean {
    const current = sync;
    if (!current || !command) return false;
    const doc = current.local;
    const caret = caretOffsets(element) ?? { start: 0, end: 0 };
    const block = doc.get(id);
    switch (command) {
      case "split":
        void run(split(doc, id, caret.start, caret.end, newId));
        break;
      case "newline":
        insertPlain(element, id, "\n");
        break;
      case "mergeBackward":
        void run(mergeBackward(doc, id));
        break;
      case "mergeForward":
        void run(mergeForward(doc, id));
        break;
      case "indent":
      case "outdent":
      case "moveUp":
      case "moveDown": {
        const edit = command === "indent" ? indent(doc, [id]) : command === "outdent" ? outdent(doc, [id])
          : moveBlocks(doc, [id], command === "moveUp" ? -1 : 1);
        void run({ ops: edit.ops, focus: { id, offset: caret.start } }).then(() => focusAt(id, caret.start, caret.end));
        break;
      }
      case "selectBlock":
        element.blur();
        selectRange(id, id);
        break;
      case "focusPrevious":
      case "focusPreviousEnd": {
        const previous = step(id, -1);
        if (!previous) return false;
        focusAt(previous, doc.get(previous).text.length);
        break;
      }
      case "focusNext":
      case "focusNextStart": {
        const next = step(id, 1);
        if (!next) return false;
        focusAt(next, command === "focusNext" ? Math.min(caret.start, doc.get(next).text.length) : 0);
        break;
      }
      case "extendUp":
      case "extendDown": {
        element.blur();
        const next = step(id, command === "extendUp" ? -1 : 1);
        selectRange(id, next ?? id);
        break;
      }
      case "bold":
      case "italic":
      case "code":
        toggle(element, id, command === "bold" ? "b" : command === "italic" ? "i" : "c");
        break;
      case "link": {
        if (caret.start === caret.end) return false;
        const existing = marksOf(block).find((mark) => mark.t === "a" && mark.s <= caret.start && mark.e >= caret.end);
        const rect = element.getBoundingClientRect();
        link = { id, start: caret.start, end: caret.end, url: existing?.href ?? "https://", x: rect.left, y: rect.bottom + 4 };
        break;
      }
      default:
        return false;
    }
    event.preventDefault();
    return true;
  }

  function onKeydown(event: KeyboardEvent, id: string) {
    const element = event.currentTarget as HTMLElement;
    if (event.isComposing || event.keyCode === 229) return;
    if (slash?.id === id && slashKey(event)) return;
    const current = sync;
    if (!current?.local.has(id)) return;
    const text = current.local.get(id).text;
    const caret = caretOffsets(element) ?? { start: 0, end: 0 };
    const mod = event.ctrlKey || event.metaKey;
    if (mod && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "a" && text && caret.start === 0 && caret.end === text.length) {
      event.preventDefault();
      element.blur();
      if (rows.length) selectRange(rows[0].block.id, rows[rows.length - 1].block.id);
      return;
    }
    const vertical = event.key === "ArrowUp" || event.key === "ArrowDown";
    const lines = vertical ? caretLines(element) : { first: false, last: false };
    const here = event.key === "Tab" || event.key === "Escape" ? suggestionsHere(id, caret.start, caret.end) : [];
    const command = keyCommand(event, {
      mode: "text", atStart: caret.start === 0, atEnd: caret.end === text.length, collapsed: caret.start === caret.end,
      firstLine: lines.first, lastLine: lines.last, type: current.local.get(id).type, suggestion: here.length > 0,
    });
    if (command === "acceptTag" || command === "rejectTag") {
      event.preventDefault();
      event.stopPropagation();
      if (command === "acceptTag") acceptTag(id, here[0]);
      else rejectTag(id, here[0].tag, here[0].name);
      return;
    }
    textCommand(command, event, element, id);
  }

  function onCompositionEnd(event: CompositionEvent, id: string) {
    const element = event.currentTarget as HTMLElement;
    composing = null;
    commitText(element, id);
  }

  // The `/` menu ----------------------------------------------------------------------

  type SlashItem =
    | { kind: "type"; type: string; label: string; hint: string }
    | { kind: "link"; label: string; hint: string }
    | { kind: "subpage"; label: string; hint: string }
    | { kind: "page"; number: number; label: string; hint: string };

  const slashItems = $derived.by((): SlashItem[] => {
    if (!slash) return [];
    const query = slash.query.trim().toLowerCase();
    if (slash.stage === "pages") {
      return pages.filter((page) => page.title.toLowerCase().includes(query)).slice(0, 12)
        .map((page) => ({ kind: "page", number: page.number, label: page.title || "Untitled page", hint: "" }));
    }
    const items: SlashItem[] = BLOCK_TYPES.map((item) => ({ kind: "type", type: item.type, label: item.label, hint: item.hint }));
    if (pages.length) items.push({ kind: "link", label: "Link to page", hint: "[[" });
    if (oncreatepage) items.push({ kind: "subpage", label: "New sub-page", hint: "" });
    const keywords = (item: SlashItem) => `${item.label} ${item.kind === "type" ? BLOCK_TYPES.find((entry) => entry.type === item.type)?.keywords ?? "" : "page link"}`.toLowerCase();
    return items.filter((item) => !query || keywords(item).includes(query));
  });

  function openSlash(id: string, start: number, element: HTMLElement) {
    const range = window.getSelection()?.rangeCount ? window.getSelection()!.getRangeAt(0).getBoundingClientRect() : null;
    const box = range && range.height ? range : element.getBoundingClientRect();
    slash = { id, start, query: "", index: 0, x: box.left, y: box.bottom + 6, stage: "types" };
  }

  function slashKey(event: KeyboardEvent): boolean {
    if (!slash) return false;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const count = Math.max(1, slashItems.length);
      slash = { ...slash, index: (slash.index + (event.key === "ArrowDown" ? 1 : count - 1)) % count };
    } else if (event.key === "Enter" || event.key === "Tab") {
      const item = slashItems[slash.index];
      if (!item) return false;
      void choose(item);
    } else if (event.key === "Escape") slash = null;
    else return false;
    event.preventDefault();
    return true;
  }

  /** The block's text with the typed `/query` removed (and its marks kept in place). */
  function withoutQuery(block: BlockData, start: number, length: number): { text: string; marks: Mark[] } {
    const marks = marksOf(block);
    return {
      text: block.text.slice(0, start) + block.text.slice(start + length),
      marks: concatMarks(sliceMarks(marks, 0, start), start, sliceMarks(marks, start + length, block.text.length)),
    };
  }

  async function choose(item: SlashItem) {
    const current = sync;
    const state = slash;
    if (!current || !state || !current.local.has(state.id)) return;
    const block = current.local.get(state.id);
    const cut = withoutQuery(block, state.start, (state.stage === "types" ? 1 : 0) + state.query.length);
    const ops: Op[] = [];
    if (cut.text !== block.text) ops.push({ op: "text", id: block.id, text: cut.text }, { op: "set", id: block.id, props: { marks: cut.marks.length ? cut.marks : null } });
    if (item.kind === "link") {
      if (ops.length) current.commit(ops);
      slash = { ...state, stage: "pages", query: "", index: 0 };
      return;
    }
    slash = null;
    const type = item.kind === "type" ? item.type : "page_link";
    let props: Record<string, unknown> = {};
    let text = "";
    if (item.kind === "page" || item.kind === "subpage") {
      try {
        const page = item.kind === "subpage" ? await oncreatepage?.()
          : await api<PageState>(`/blocks/page?type=page&number=${item.number}`).then((found) => ({ page: found.page_id, title: found.root.text }));
        if (!page) return;
        props = { page: page.page };
        text = page.title;
      } catch (cause) {
        error = (cause as Error).message;
        return;
      }
    }
    if (!cut.text && type !== "page_link") {
      const next = type === "divider" ? insertAfter(current.local, block.id, "text", newId) : null;
      // `insertAfter` reads the page before this edit; the divider keeps its id and key.
      void run({ ops: [...ops, { op: "set", id: block.id, type }, ...(next?.ops ?? [])], focus: next?.focus ?? { id: block.id, offset: 0 } });
      return;
    }
    if (!cut.text) {
      void run({ ops: [...ops, { op: "set", id: block.id, type, props }, { op: "text", id: block.id, text }], focus: { id: block.id, offset: 0 } });
      return;
    }
    const added = insertAfter(current.local, block.id, type, newId, props, text);
    void run({ ops: [...ops, ...added.ops], focus: added.focus });
  }

  // Links, menus and drag and drop -------------------------------------------------------

  function applyLink(remove = false) {
    const current = sync;
    const state = link;
    if (!current || !state) return;
    link = null;
    const block = current.local.get(state.id);
    const url = state.url.trim();
    const without = marksOf(block).flatMap((mark) => mark.t !== "a" || mark.e <= state.start || mark.s >= state.end ? [mark]
      : [...(mark.s < state.start ? [{ ...mark, e: state.start }] : []), ...(mark.e > state.end ? [{ ...mark, s: state.end }] : [])]);
    const marks = remove || !url ? normalizeMarks(without, block.text.length) : toggleMark(block.text, without, state.start, state.end, "a", url);
    void run({ ops: [{ op: "set", id: state.id, props: { marks: marks.length ? marks : null } }], focus: { id: state.id, offset: state.end } });
  }

  function onEditableClick(event: MouseEvent) {
    const anchorElement = (event.target as HTMLElement).closest("a");
    if (anchorElement && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      window.open(anchorElement.href, "_blank", "noopener,noreferrer");
    }
  }

  function setChecked(id: string, checked: boolean) {
    sync?.commit([{ op: "set", id, props: { checked } }]);
  }

  function setCollapsed(id: string, collapsed: boolean) {
    sync?.commit([{ op: "set", id, props: { collapsed } }]);
  }

  function onDragStart(event: DragEvent, id: string) {
    const current = sync;
    if (!current || !event.dataTransfer) return;
    const ids = chosen.has(id) ? selected : [id];
    drag = { ids };
    menu = null;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData(BLOCK_DRAG_TYPE, JSON.stringify({ page: current.pageId, ids }));
    writeClipboard(event.dataTransfer, copyEntries(ids));
    const row = (event.currentTarget as HTMLElement).closest(".block-row");
    if (row) event.dataTransfer.setDragImage(row, 16, 12);
  }

  function onDragOver(event: DragEvent, id: string) {
    if (!drag || drag.ids.includes(id)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    const row = event.currentTarget as HTMLElement;
    const box = row.getBoundingClientRect();
    const body = row.querySelector(".block-body")?.getBoundingClientRect() ?? box;
    const below = event.clientY > box.top + box.height / 2;
    const where = below && event.clientX > body.left + 48 ? "inside" : below ? "after" : "before";
    if (drop?.id !== id || drop.where !== where) drop = { id, where };
  }

  function onDrop(event: DragEvent) {
    const current = sync;
    event.preventDefault();
    if (current && drag && drop) void run(dropBlocks(current.local, drag.ids, drop.id, drop.where));
    drag = null;
    drop = null;
  }

  function onRootPointerMove(event: PointerEvent) {
    if (!pressed || !(event.buttons & 1)) return;
    const over = (event.target as HTMLElement).closest<HTMLElement>("[data-block-id]")?.dataset.blockId;
    if (over && over !== pressed && root?.contains(event.target as Node)) {
      // A drag from one block into another selects whole blocks, as Notion does.
      selectRange(pressed, over);
    }
  }

  async function startWriting() {
    const current = sync;
    if (current) await run(firstBlock(current.local, newId));
  }

  async function clickTail() {
    const current = sync;
    const last = rows[rows.length - 1]?.block;
    if (!current) return;
    if (last && last.type === "text" && !last.text && last.parent_id === current.pageId) focusAt(last.id, 0);
    else await run(firstBlock(current.local, newId));
  }

  function openMenu(event: MouseEvent, id: string) {
    event.stopPropagation();
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    menu = { id, x: box.left, y: box.bottom + 4 };
  }

  function menuTargets(id: string): string[] {
    return chosen.has(id) ? selected : [id];
  }

  const placeholder: Record<string, string> = {
    heading_1: "Heading 1", heading_2: "Heading 2", heading_3: "Heading 3", to_do: "To-do", bulleted_list: "List",
    numbered_list: "List", quote: "Quote", callout: "Callout", code: "Code", toggle: "Toggle",
  };
  const bullets = ["•", "◦", "▪"];
</script>

<svelte:document oncopy={onCopy} oncut={onCut} onpaste={onDocumentPaste} onselectionchange={trackCaret} />
<svelte:window
  onpointerup={() => { pressed = null; }}
  onclick={() => { menu = null; }}
  onkeydown={(event) => { if (event.key === "Escape") { menu = null; link = null; } }}
/>

{#if loadError}<p class="editor-error" role="alert">{loadError}</p>{/if}
{#if error}<p class="editor-error" role="alert">{error} <button onclick={() => (error = "")}>Dismiss</button></p>{/if}
<!-- svelte-ignore a11y_no_noninteractive_element_interactions (the page's blocks take keyboard commands while blocks are selected) -->
<div
  class="block-editor"
  bind:this={root}
  tabindex="-1"
  role="group"
  aria-label={label}
  aria-busy={!sync && !loadError}
  onkeydown={onRootKeydown}
  onpointermove={onRootPointerMove}
  onfocusout={(event) => { if (!root?.contains(event.relatedTarget as Node)) slash = null; }}
>
  {#each rows as row, index (row.block.id)}
    {@const block = row.block}
    {@const tagged = attachedTags(block, tagNames)}
    <div
      class="block-row type-{block.type}"
      class:selected={chosen.has(block.id)}
      class:focused={focusBlock === block.id}
      class:drop-before={drop?.id === block.id && drop.where === "before"}
      class:drop-after={drop?.id === block.id && drop.where === "after"}
      class:drop-inside={drop?.id === block.id && drop.where === "inside"}
      data-block-id={block.id}
      data-selected={chosen.has(block.id) ? "true" : undefined}
      style:--depth={row.depth}
      role="presentation"
      ondragover={(event) => onDragOver(event, block.id)}
      ondragleave={() => { if (drop?.id === block.id) drop = null; }}
      ondrop={onDrop}
    >
      <div class="gutter">
        <button
          class="handle"
          draggable="true"
          title="Drag to move · click for options"
          aria-label={`Block ${index + 1} options`}
          ondragstart={(event) => onDragStart(event, block.id)}
          ondragend={() => { drag = null; drop = null; }}
          onclick={(event) => openMenu(event, block.id)}
        >⋮⋮</button>
        {#if row.hasChildren && block.type !== "toggle"}
          <button class="fold" aria-label={block.properties.collapsed ? "Expand block" : "Collapse block"} aria-expanded={!block.properties.collapsed}
            onclick={() => setCollapsed(block.id, !block.properties.collapsed)}>{block.properties.collapsed ? "▸" : "▾"}</button>
        {/if}
      </div>
      <div class="block-body">
        {#if block.type === "divider"}
          <button class="divider" aria-label={`Divider, block ${index + 1}`} onclick={() => selectRange(block.id, block.id)}><hr /></button>
        {:else if block.type === "page_link"}
          <button class="page-link" onclick={() => onopenpage?.(String(block.properties.page ?? ""))}><span aria-hidden="true">↗</span> {block.text || "Untitled page"}</button>
        {:else}
          <div class="line">
            {#if block.type === "to_do"}
              <input type="checkbox" checked={!!block.properties.checked} aria-label={`Done: block ${index + 1}`} onchange={(event) => setChecked(block.id, event.currentTarget.checked)} />
            {:else if block.type === "bulleted_list"}
              <span class="marker" aria-hidden="true">{bullets[row.depth % bullets.length]}</span>
            {:else if block.type === "numbered_list"}
              <span class="marker number" aria-hidden="true">{row.number}.</span>
            {:else if block.type === "toggle"}
              <button class="marker toggle-arrow" aria-label={block.properties.collapsed ? "Open toggle" : "Close toggle"} aria-expanded={!block.properties.collapsed}
                onclick={() => setCollapsed(block.id, !block.properties.collapsed)}>{block.properties.collapsed ? "▸" : "▾"}</button>
            {:else if block.type === "callout"}
              <span class="marker callout-icon" aria-hidden="true">{String(block.properties.icon ?? "💡")}</span>
            {/if}
            <div
              class="editable"
              class:checked={block.type === "to_do" && !!block.properties.checked}
              contenteditable="true"
              role="textbox"
              tabindex="0"
              aria-multiline="true"
              aria-label={`Block ${index + 1}`}
              data-editable={block.id}
              data-placeholder={placeholder[block.type] ?? "Type '/' for commands"}
              spellcheck={block.type !== "code"}
              use:editable={{ text: block.text, marks: marksOf(block) }}
              oninput={(event) => onInput(event, block.id)}
              onkeydown={(event) => onKeydown(event, block.id)}
              onpaste={(event) => onPaste(event, block.id)}
              oncompositionstart={(event) => { composing = event.currentTarget; }}
              oncompositionend={(event) => onCompositionEnd(event, block.id)}
              onfocus={() => { if (selected.length) selected = []; }}
              onpointerdown={(event) => { if (event.shiftKey && selected.length) { event.preventDefault(); selectRange(anchor, block.id); } else pressed = block.id; }}
              onclick={onEditableClick}
            ></div>
          </div>
          {#if block.type === "code"}
            <input class="language" aria-label={`Code language, block ${index + 1}`} placeholder="language" value={String(block.properties.language ?? "")}
              onchange={(event) => sync?.commit([{ op: "set", id: block.id, props: { language: event.currentTarget.value.trim() || null } }])} />
          {/if}
          {#if tagged.length}
            <div class="block-tags" role="list" aria-label={`Tags of block ${index + 1}`}>
              {#each tagged as item (item.tag)}
                <span class="tag-chip" class:auto={item.state === "auto"} role="listitem"
                  title={item.state === "auto" ? "Attached automatically" : item.state === "accepted" ? "Attached from a suggestion" : "Attached by hand"}>
                  <button class="tag-open" onclick={() => ontag?.(item.tag)}>#{item.name}</button>
                  <button class="tag-remove" aria-label={`Remove tag ${item.name}`} onclick={() => rejectTag(block.id, item.tag, item.name)}>×</button>
                </span>
              {/each}
            </div>
          {/if}
        {/if}
        {@render extras?.(block)}
      </div>
    </div>
  {/each}
  {#if sync && !rows.length}
    <button class="empty" onclick={() => void startWriting()}>Start writing…</button>
  {:else if sync}
    <button class="tail" aria-label="Add a block at the end" onclick={() => void clickTail()}></button>
  {/if}
</div>
<div class="sr-only" aria-live="polite">{announcement}</div>

{#if slash && slashItems.length}
  <div class="popover slash" role="listbox" aria-label={slash.stage === "pages" ? "Link to page" : "Turn into"} style:left={`${Math.min(slash.x, innerWidth - 260)}px`} style:top={`${Math.min(slash.y, innerHeight - 320)}px`}>
    {#each slashItems as item, index}
      <button role="option" aria-selected={index === slash.index} class:active={index === slash.index}
        onpointerdown={(event) => { event.preventDefault(); void choose(item); }}>
        <span>{item.label}</span>{#if item.hint}<kbd>{item.hint}</kbd>{/if}
      </button>
    {/each}
  </div>
{/if}

{#if menu}
  {@const targets = menuTargets(menu.id)}
  <div class="popover block-menu" role="menu" tabindex="-1" style:left={`${Math.min(menu.x, innerWidth - 220)}px`} style:top={`${Math.min(menu.y, innerHeight - 380)}px`}
    onclick={(event) => event.stopPropagation()} onkeydown={(event) => { if (event.key === "Escape") menu = null; }}>
    <p class="menu-title">Turn into</p>
    {#each BLOCK_TYPES as item}
      <button role="menuitem" onclick={() => { const ids = targets; menu = null; if (sync) void run({ ...turnInto(sync.local, ids, item.type), select: ids }); }}>{item.label}</button>
    {/each}
    <hr />
    <button role="menuitem" class="danger" onclick={() => { const ids = targets; menu = null; if (sync) void run(deleteBlocks(sync.local, ids)); }}>Delete{targets.length > 1 ? ` ${targets.length} blocks` : ""}</button>
  </div>
{/if}

{#if hint && hintBox}
  {@const current = hint}
  <div class="popover tag-hint" role="group" aria-label="Suggested tags" style:left={`${Math.min(hintBox.x, innerWidth - 240)}px`} style:top={`${Math.min(hintBox.y, innerHeight - 120)}px`}>
    {#each current.items as item (item.tag)}
      <div class="tag-hint-row">
        <button class="accept" onpointerdown={(event) => event.preventDefault()} onclick={() => acceptTag(current.id, item)}>Tag #{item.name}</button>
        <button class="ignore" onpointerdown={(event) => event.preventDefault()} onclick={() => rejectTag(current.id, item.tag, item.name)}>Ignore</button>
      </div>
    {/each}
    <p class="tag-hint-keys"><kbd>Tab</kbd> tag · <kbd>Esc</kbd> ignore</p>
  </div>
{/if}

{#if link}
  <form class="popover link-form" style:left={`${Math.min(link.x, innerWidth - 320)}px`} style:top={`${link.y}px`} onsubmit={(event) => { event.preventDefault(); applyLink(); }}>
    <!-- svelte-ignore a11y_autofocus -->
    <input aria-label="Link address" bind:value={link.url} autofocus />
    <button type="submit">Link</button>
    <button type="button" onclick={() => applyLink(true)}>Remove</button>
  </form>
{/if}

<style>
  .block-editor { outline: none; padding: 4px 0 24px; min-height: 120px; }
  .block-row { --indent: calc(var(--depth) * 26px); position: relative; display: flex; align-items: flex-start; padding-left: var(--indent); border-radius: 4px; }
  .block-row.selected { background: color-mix(in srgb, var(--accent) 16%, transparent); }
  .block-row.focused { background: color-mix(in srgb, var(--accent) 8%, transparent); }
  .block-row.drop-before { box-shadow: inset 0 2px 0 var(--accent); }
  .block-row.drop-after { box-shadow: inset 0 -2px 0 var(--accent); }
  .block-row.drop-inside { box-shadow: inset 26px -2px 0 -24px var(--accent); }
  .gutter { display: flex; width: 40px; flex-shrink: 0; justify-content: flex-end; gap: 2px; padding-top: 4px; opacity: 0; transition: opacity .12s; }
  .block-row:hover .gutter, .block-row:focus-within .gutter, .block-row.selected .gutter { opacity: 1; }
  .gutter button { font: inherit; font-size: 12px; line-height: 1; color: var(--muted); background: none; border: 0; border-radius: 4px; padding: 4px 3px; cursor: pointer; }
  .gutter button:hover, .gutter button:focus-visible { background: var(--surface-raised); color: var(--ink); }
  .handle { cursor: grab; letter-spacing: -2px; }
  .block-body { flex: 1; min-width: 0; padding: 1px 4px; }
  .line { display: flex; align-items: flex-start; gap: 6px; }
  .marker { flex-shrink: 0; min-width: 1.2em; color: var(--muted); font-size: 15px; line-height: 1.8; text-align: center; user-select: none; }
  .marker.number { min-width: 1.6em; text-align: right; }
  .toggle-arrow { background: none; border: 0; padding: 0; cursor: pointer; font: inherit; color: var(--muted); }
  .line input[type="checkbox"] { margin: .45em 2px 0 0; width: 16px; height: 16px; accent-color: var(--accent); flex-shrink: 0; }
  .editable { flex: 1; min-width: 0; outline: none; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 15px; line-height: 1.8; color: var(--ink); caret-color: var(--accent); }
  .editable:empty::before { content: attr(data-placeholder); color: var(--muted); opacity: .55; pointer-events: none; }
  .type-text .editable:empty:not(:focus)::before { content: ""; }
  .editable.checked { color: var(--muted); text-decoration: line-through; }
  .editable :global(code) { font-family: var(--font-mono); font-size: .88em; padding: .1em .3em; border-radius: 4px; background: var(--surface-raised); }
  .editable :global(a) { color: var(--accent); text-decoration: underline; }
  .type-heading_1 .editable { font-family: var(--heading); font-size: 28px; font-weight: 700; line-height: 1.35; margin-top: 18px; }
  .type-heading_2 .editable { font-family: var(--heading); font-size: 22px; font-weight: 650; line-height: 1.4; margin-top: 14px; }
  .type-heading_3 .editable { font-family: var(--heading); font-size: 18px; font-weight: 650; line-height: 1.45; margin-top: 10px; }
  .type-quote .line { border-left: 3px solid var(--ink); padding-left: 12px; }
  .type-callout .line { background: var(--surface-raised); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; }
  .type-code .line { background: var(--surface-raised); border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px; }
  .type-code .editable { font-family: var(--font-mono); font-size: 13px; line-height: 1.6; white-space: pre; overflow-x: auto; overflow-wrap: normal; }
  .language { margin-top: 4px; font: inherit; font-size: 11px; color: var(--muted); background: transparent; border: 1px solid transparent; border-radius: 4px; padding: 1px 6px; width: 120px; }
  .language:hover, .language:focus { border-color: var(--border); }
  .divider { display: block; width: 100%; padding: 10px 0; background: none; border: 0; cursor: pointer; }
  .divider hr { border: 0; border-top: 1px solid var(--border); margin: 0; }
  .page-link { font: inherit; font-size: 15px; line-height: 1.8; color: var(--ink); background: none; border: 0; padding: 0 2px; cursor: pointer; text-decoration: underline; text-decoration-color: var(--border); text-underline-offset: 3px; }
  .page-link:hover { color: var(--accent); }
  .empty { display: block; font: inherit; color: var(--muted); background: none; border: 0; padding: 18px 44px; cursor: text; }
  .tail { display: block; width: 100%; min-height: 48px; background: none; border: 0; cursor: text; }
  .popover { position: fixed; z-index: 95; display: flex; flex-direction: column; min-width: 200px; max-height: 320px; overflow: auto; padding: 6px; background: var(--surface); color: var(--ink); border: 1px solid var(--border); border-radius: 8px; box-shadow: var(--overlay-shadow, 0 8px 24px #0003); }
  .popover button { display: flex; justify-content: space-between; gap: 16px; font: inherit; font-size: 13px; text-align: left; color: inherit; background: none; border: 0; border-radius: 5px; padding: 6px 8px; cursor: pointer; }
  .popover button:hover, .popover button.active { background: var(--surface-raised); }
  .popover kbd { font-family: var(--font-mono); font-size: 11px; color: var(--muted); }
  .menu-title { margin: 2px 8px 4px; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  .popover hr { border: 0; border-top: 1px solid var(--border); margin: 4px 0; width: 100%; }
  .danger { color: var(--error) !important; }
  .link-form { flex-direction: row; align-items: center; gap: 6px; }
  .link-form input { font: inherit; font-size: 13px; width: 200px; padding: 4px 6px; border: 1px solid var(--border); border-radius: 4px; background: var(--canvas, transparent); color: var(--ink); }
  .editor-error { color: var(--error); font-size: 13px; margin: 4px 44px; }
  .block-tags { display: flex; flex-wrap: wrap; gap: 4px; margin: 1px 0 4px; }
  .tag-chip { display: inline-flex; align-items: center; font-size: 11px; line-height: 1; color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, transparent); border: 1px solid color-mix(in srgb, var(--accent) 25%, transparent); border-radius: 999px; }
  .tag-chip.auto { border-style: dashed; }
  .tag-chip button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; padding: 3px 6px; border-radius: 999px; }
  .tag-chip .tag-remove { padding: 3px 6px 3px 0; color: var(--muted); }
  .tag-chip button:hover, .tag-chip button:focus-visible { color: var(--ink); }
  .tag-hint { min-width: 180px; gap: 2px; }
  .tag-hint-row { display: flex; gap: 4px; }
  .tag-hint-row .accept { flex: 1; color: var(--accent); font-weight: 600; }
  .tag-hint-row .ignore { color: var(--muted); }
  .tag-hint-keys { margin: 2px 8px 0; font-size: 11px; color: var(--muted); }
  .editor-error button { font: inherit; color: var(--accent); background: none; border: 0; cursor: pointer; }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
</style>
