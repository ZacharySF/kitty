<script module lang="ts">
  /** GET /api/blocks/citations: one pinned citation ((id@seq)) checked against now. */
  export type Citation = {
    cite: string; id: string; cited_seq: number; seq: number; changed: boolean;
    status: "unchanged" | "changed" | "deleted" | "purged" | "compacted" | "unknown";
    then: { text: string; type: string } | null; now: { text: string; type: string } | null;
    diff: { op: "equal" | "delete" | "insert"; text: string }[];
  };
  /** Every pinned citation `id@seq` written in `text`. */
  export function pinnedCites(text: string): string[] {
    return Array.from(text.matchAll(/\(\(([0-9a-f-]{36})@([0-9]{1,18})\)\)/gi), (match) => `${match[1].toLowerCase()}@${match[2]}`);
  }
</script>
<script lang="ts">
  let { text, references = {}, citations = {}, onpage, ontopic, onreference, oncitation = () => {} }: {
    text: string; references?: Record<string,string>; citations?: Record<string, Citation>; onpage:(title:string)=>void;
    ontopic:(kind:"page"|"tag",topic:string)=>void; onreference:(id:string)=>void; oncitation?:(citation: Citation)=>void;
  } = $props();
  const parts = $derived(Array.from(text.matchAll(/\[\[([^\]\n|]+)(?:\|([^\]\n]+))?\]\]|(?<![\p{L}\p{N}_#])#([\p{L}\p{N}_][\p{L}\p{N}_/-]*)|\(\(([0-9a-f-]{36})(?:@([0-9]{1,18}))?\)\)|\{\{embed:([0-9a-f-]{36})\}\}/giu)));
  // A pinned citation whose block changed after the cited moment says so (paper §14.3).
  const changed = (id: string, seq: string | undefined) => (seq ? citations[`${id.toLowerCase()}@${seq}`] : undefined);
</script>
{#each parts as part,index}
  {text.slice(index ? parts[index-1].index! + parts[index-1][0].length : 0,part.index)}
  {#if part[1]}<button class="text-link" onclick={()=>onpage(part[1])}>{part[2]||part[1]}</button><button class="topic-link" aria-label={`Topic ${part[1]}`} onclick={()=>ontopic("page",part[1])}>↗</button>
  {:else if part[3]}<button class="text-link" onclick={()=>ontopic("tag",part[3])}>#{part[3]}</button>
  {:else}<button class:embed={!!part[6]} class="block-reference" title="Open citation contexts" onclick={()=>onreference((part[4]||part[6]).toLowerCase())}>{references[(part[4]||part[6]).toLowerCase()]??"[loading or unavailable block]"}</button>{#if part[4] && changed(part[4], part[5])?.changed}{@const citation = changed(part[4], part[5])!}<button class="source-changed" title={`The cited block changed after seq ${citation.cited_seq} (now at ${citation.seq}): show what changed`} onclick={()=>oncitation(citation)}>{citation.status === "changed" ? "source changed since" : citation.status === "deleted" ? "source in Trash since" : "source removed since"}</button>{/if}{/if}
{/each}
{text.slice(parts.length ? parts[parts.length-1].index! + parts[parts.length-1][0].length : 0)}
<style>
  button{font:inherit;cursor:pointer;border:0;padding:0;background:none;color:var(--accent);text-align:left;white-space:pre-wrap;overflow-wrap:anywhere}.topic-link{font-size:.7em;margin:0 .3em}.block-reference{color:inherit;border-bottom:1px dashed var(--accent)}.embed{display:block;border-left:3px solid var(--accent);padding:.6em 1em;margin:.4em 0;background:var(--chrome);width:100%}
  .source-changed{font-size:.72em;margin-left:.4em;padding:0 .45em;border-radius:999px;color:var(--warning,var(--accent));border:1px solid currentColor;white-space:nowrap;vertical-align:.1em}
</style>
