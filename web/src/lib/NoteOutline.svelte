<script module lang="ts">
  /** GET /api/notes/pages/:id/blocks: the page's outline with live references and citation counts. */
  export type BlocksResult = {
    revision: number;
    blocks: { id: string; parent_id: string | null; text: string; type: string }[];
    references: Record<string, string>;
    citation_counts: Record<string, number>;
  };
</script>
<script lang="ts">
  import { api } from "./api";
  import BlockEditor, { type PageRef } from "./BlockEditor.svelte";
  import NoteBlockText from "./NoteBlockText.svelte";
  import TagPage from "./TagPage.svelte";
  import MirrorPane from "./MirrorPane.svelte";
  import { createTag, findTag } from "./tags";
  import type { BlockData } from "./blockDoc";
  import { quoteSpecs, type Mirror } from "./mirror";

  // A note page's writing view: kitty!'s block editor plus Notes' reference tools
  // (live ((references)), citation contexts, [[page]] and #tag topics, tag pages, unlinked mentions).
  let {
    pageId, pages, references, counts, focusBlock = "", mirror = false,
    onpage, onopen, onopenpage, oncreatepage, onrevision, onerror, onclosemirror = () => {},
  }: {
    pageId: number;
    pages: { id: number; title: string }[];
    references: Record<string, string>;
    counts: Record<string, number>;
    focusBlock?: string;
    /** Show the Mirror pane (Q11) beside the page. */
    mirror?: boolean;
    onpage: (title: string) => void;
    onopen: (id: number, blockId?: string) => void;
    onopenpage: (pageBlockId: string) => void;
    oncreatepage: () => Promise<{ page: string; title: string } | null>;
    onrevision: (from: number, to: number, own: boolean) => void;
    onerror: (message: string) => void;
    onclosemirror?: () => void;
  } = $props();

  let editor = $state<ReturnType<typeof BlockEditor>>();
  type CitationContext = { id: string; page_id: number | null; title: string; text: string; count: number; contexts: { id: string; page_id: number; title: string; text: string; parent_text?: string }[]; next_offset: number | null };
  type TopicContext = { kind: string; topic: string; blocks: { id: string; page_id: number; title: string; text: string }[]; count: number; next_offset: number | null };
  type Mention = { block_id: string; page_id: number; title: string; text: string };
  let context = $state<CitationContext | null>(null);
  let topic = $state<TopicContext | null>(null);
  let mentions = $state<Mention[]>([]);
  let error = $state("");
  /** The tag whose page is open in the side panel (a chip or a #tag with a tag record was clicked). */
  let tagId = $state<string | null>(null);

  export function flush(): Promise<void> { return editor?.flush() ?? Promise.resolve(); }
  export function pending(): boolean { return editor?.pending() ?? false; }
  export function exclusive(write: () => Promise<number | null>): Promise<void> { return editor ? editor.exclusive(write) : write().then(() => undefined); }
  export function moveToPage(ids: string[], page: PageRef): Promise<boolean> { return editor?.moveToPage(ids, page) ?? Promise.resolve(false); }

  // References and counts refresh after every change to this page; mentions follow them.
  let mentionTimer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    void references;
    void counts;
    const id = pageId;
    clearTimeout(mentionTimer);
    mentionTimer = setTimeout(() => {
      api<{ mentions: Mention[] }>(`/notes/pages/${id}/mentions`).then((result) => { if (id === pageId) mentions = result.mentions.slice(0, 100); }).catch(() => {});
    }, 300);
    return () => clearTimeout(mentionTimer);
  });

  async function citations(id: string) { try { context = await api(`/notes/blocks/${id}/contexts`); topic = null; tagId = null; error = ""; } catch (cause) { error = (cause as Error).message; } }
  async function showTopic(kind: "page" | "tag", name: string) {
    try {
      const found = kind === "tag" ? (await findTag(name)).id : null;
      if (found) return openTag(found);
      topic = await api(`/notes/topics?kind=${kind}&topic=${encodeURIComponent(name)}`); context = null; tagId = null; error = "";
    } catch (cause) { error = (cause as Error).message; }
  }
  function openTag(id: string) { tagId = id; context = null; topic = null; error = ""; }
  async function makeTag(name: string) {
    try { openTag((await createTag({ name })).id); }
    catch (cause) { error = (cause as Error).message; }
  }
  async function moreContexts() {
    const current = context;
    if (!current || current.next_offset === null) return;
    try { const result = await api<CitationContext>(`/notes/blocks/${current.id}/contexts?offset=${current.next_offset}`); if (context === current) context = { ...result, contexts: [...current.contexts, ...result.contexts] }; }
    catch (cause) { error = (cause as Error).message; }
  }
  async function moreTopics() {
    const current = topic;
    if (!current || current.next_offset === null) return;
    try { const result = await api<TopicContext>(`/notes/topics?kind=${current.kind}&topic=${encodeURIComponent(current.topic)}&offset=${current.next_offset}`); if (topic === current) topic = { ...result, blocks: [...current.blocks, ...result.blocks] }; }
    catch (cause) { error = (cause as Error).message; }
  }
  async function copyReference(value: string) {
    try { await navigator.clipboard.writeText(value); }
    catch { error = `Copy this reference: ${value}`; }
  }
  /** Wrap the first unlinked mention of `title` in a block as [[title]]. */
  function link(mention: Mention) {
    const escaped = mention.title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const pattern = new RegExp(`(^|[^\\p{L}\\p{N}_])(${escaped})(?=$|[^\\p{L}\\p{N}_])`, "iu");
    void editor?.replaceText(mention.block_id, (text) => {
      let offset = 0;
      for (const part of text.split(/(\[\[[^\]]*\]\])/g)) {
        const match = part.startsWith("[[") ? null : pattern.exec(part);
        if (match) {
          const start = offset + match.index + match[1].length;
          return { start, end: start + match[2].length, text: `[[${match[2]}]]` };
        }
        offset += part.length;
      }
      return null;
    });
  }
  /** A passage quoted from the Mirror pane becomes a callout and quote in this page. */
  function quoteMirror(passage: string, source: Mirror) {
    if (!editor?.insertQuote(quoteSpecs({ passage, title: source.title, url: source.url, at: new Date() })))
      error = "The page is still loading; quote again in a moment.";
  }
</script>

{#snippet extras(block: BlockData)}
  {#if /\[\[|#\S|\(\(|\{\{embed:/.test(block.text)}
    <div class="inline-preview"><NoteBlockText text={block.text} {references} {onpage} ontopic={showTopic} onreference={citations} /></div>
  {/if}
  <div class="block-tools">
    <button title="Copy block reference" aria-label="Copy block reference" onclick={() => void copyReference(`((${block.id}))`)}>⧉</button>
    <button title="Copy live embed" aria-label="Copy live block embed" onclick={() => void copyReference(`{{embed:${block.id}}}`)}>↪</button>
    <button class="citations" title="Referencing block contexts" onclick={() => void citations(block.id)}>{counts[block.id] || 0} citations</button>
  </div>
{/snippet}

<div class="text-note">
  {#if error}<p role="alert">{error}</p>{/if}
  <div class="text-layout">
    <article class="outline" aria-label="Text note outline">
      <BlockEditor bind:this={editor} target={{ type: "page", number: pageId }} label="Text note blocks"
        pages={pages.filter((page) => page.id !== pageId).map((page) => ({ number: page.id, title: page.title }))}
        {focusBlock} {extras} {onrevision} {onerror} {onopenpage} {oncreatepage} ontag={openTag} />
      {#if mentions.length}
        <section class="mentions"><h3>Unlinked title mentions</h3>
          {#each mentions as mention (`${mention.block_id}:${mention.page_id}`)}
            <div><span>{mention.text.slice(0, 160)}</span><button onclick={() => link(mention)}>Link [[{mention.title}]]</button></div>
          {/each}
        </section>
      {/if}
    </article>
    {#if context || topic || tagId}
      <aside class="contexts" aria-label="Reference contexts">
        <button class="close" onclick={() => { context = null; topic = null; tagId = null; }}>Close ×</button>
        {#if tagId}
          <TagPage {tagId} onopen={(page, block) => onopen(page, block)} />
        {:else if context}
          <h3>{context.count} citations</h3>
          {#if context.page_id}<button class="context original" onclick={() => context?.page_id && onopen(context.page_id, context.id)}><strong>Original · {context.title}</strong><span>{context.text}</span></button>{:else}<p class="original">{context.text}</p>{/if}
          {#each context.contexts as item}
            <button class="context" onclick={() => onopen(item.page_id, item.id)}>
              <strong>{item.title}</strong>{#if item.parent_text}<small>{item.parent_text}</small>{/if}<span>{item.text}</span>
            </button>
          {:else}<p>No blocks reference this original yet.</p>{/each}
          {#if context.next_offset !== null}<button onclick={() => void moreContexts()}>Show more citation contexts ({context.contexts.length} of {context.count})</button>{/if}
        {:else if topic}
          <h3>{topic.kind === "tag" ? "#" : "[["}{topic.topic}{topic.kind === "tag" ? "" : "]]"}</h3>
          {#if topic.kind === "tag"}<button class="make-tag" onclick={() => topic && void makeTag(topic.topic)}>Make #{topic.topic} a tag with keywords</button>{/if}
          {#each topic.blocks as item}<button class="context" onclick={() => onopen(item.page_id, item.id)}><strong>{item.title}</strong><span>{item.text}</span></button>{:else}<p>No matching blocks.</p>{/each}
          {#if topic.next_offset !== null}<button onclick={() => void moreTopics()}>Show more topic blocks ({topic.blocks.length} of {topic.count})</button>{/if}
        {/if}
      </aside>
    {/if}
    {#if mirror}<MirrorPane onquote={quoteMirror} onclose={onclosemirror} />{/if}
  </div>
</div>

<style>
  .text-note { min-height: 500px; }
  .text-layout { display: flex; min-width: 0; }
  .outline { flex: 1; min-width: 0; max-width: 950px; margin: 0 auto; padding: 16px 24px 80px 0; }
  .inline-preview { white-space: pre-wrap; font-size: 13px; line-height: 1.6; color: var(--muted); padding: 2px 4px 8px; }
  .block-tools { position: absolute; top: 4px; right: 0; display: flex; align-items: center; gap: 4px; font-size: 10px; opacity: 0; background: var(--surface); border-radius: 4px; }
  :global(.block-row:hover) .block-tools, :global(.block-row:focus-within) .block-tools { opacity: 1; }
  button { font: inherit; color: inherit; cursor: pointer; background: none; border: 0; border-radius: 4px; padding: 4px 6px; }
  button:hover { background: var(--surface-raised); }
  .citations { white-space: nowrap; color: var(--accent); }
  .contexts { width: 280px; flex-shrink: 0; border-left: 1px solid var(--border); padding: 16px; max-height: 75vh; overflow: auto; background: var(--chrome); }
  .contexts h3 { font-size: 14px; }
  .context { display: flex; flex-direction: column; gap: 6px; width: 100%; padding: 12px 4px; text-align: left; border-bottom: 1px solid var(--border); white-space: pre-wrap; overflow-wrap: anywhere; }
  .context span, .original { font-size: 12px; line-height: 1.6; }
  .context small { color: var(--muted); }
  .close { float: right; font-size: 11px; }
  .make-tag { color: var(--accent); font-size: 12px; padding: 4px 0; }
  .mentions { margin: 50px 0 0 44px; border-top: 1px solid var(--border); padding-top: 15px; font-size: 12px; color: var(--muted); }
  .mentions h3 { font-size: 12px; }
  .mentions div { display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; }
  .mentions button { color: var(--accent); }
  @media (max-width: 900px) { .contexts { width: 220px; } .outline { padding: 12px 8px 60px 0; } }
  @media (max-width: 650px) { .text-layout { flex-direction: column; } .contexts { width: auto; border-left: 0; border-top: 1px solid var(--border); } }
</style>
