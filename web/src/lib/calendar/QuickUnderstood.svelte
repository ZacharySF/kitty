<script lang="ts">
  import type { QuickKind, QuickSpan } from "./quickParse";

  /** Shows how the title was read: the typed text with recognised pieces tinted, then the resulting event. */
  let { text, spans, title, when, where = "", repeat = "" }: {
    text: string;
    spans: QuickSpan[];
    title: string;
    when: string;
    where?: string;
    repeat?: string;
  } = $props();
  const segments = $derived.by(() => {
    const out: { text: string; kind: QuickKind | "title" }[] = [];
    let cursor = 0;
    for (const span of spans) {
      if (span.start > cursor) out.push({ text: text.slice(cursor, span.start), kind: "title" });
      out.push({ text: text.slice(span.start, span.end), kind: span.kind });
      cursor = span.end;
    }
    if (cursor < text.length) out.push({ text: text.slice(cursor), kind: "title" });
    return out;
  });
</script>

<div class="understood" role="status" aria-label="Understood">
  <span class="eyebrow">kitty! read</span>
  <div class="ribbon" aria-hidden="true">
    {#each segments as segment, i (i)}<span class="seg {segment.kind}">{segment.text}</span>{/each}
  </div>
  <strong>{title}</strong>
  <span class="when">{when}</span>
  {#if where || repeat}
    <span class="facts">{#if where}<span>⌖ {where}</span>{/if}{#if repeat}<span>↻ {repeat}</span>{/if}</span>
  {/if}
</div>

<style>
  .understood {
    display: grid;
    gap: 3px;
    padding: 8px 10px;
    border: 1px solid var(--hairline);
    border-left: 3px solid var(--cal, var(--accent));
    border-radius: 8px;
    background: color-mix(in srgb, var(--solid-inset) 60%, transparent);
  }
  .eyebrow {
    color: var(--violet);
  }
  .ribbon {
    display: flex;
    flex-wrap: wrap;
    font: 500 0.72rem var(--font-mono);
    white-space: pre;
  }
  .seg {
    border-radius: 4px;
  }
  .seg.title {
    color: var(--muted);
  }
  .seg:not(.title) {
    padding: 0 3px;
    color: var(--solid-void);
  }
  .seg.day {
    background: var(--c1);
  }
  .seg.time {
    background: var(--c3);
  }
  .seg.dur {
    background: var(--c4);
  }
  .seg.where {
    background: var(--c6);
  }
  .seg.repeat {
    background: var(--c5);
  }
  strong {
    font-size: 0.9rem;
  }
  .when {
    font: 500 0.75rem var(--font-mono);
  }
  .facts {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 10px;
    color: var(--muted);
    font-size: 0.78rem;
  }
</style>
