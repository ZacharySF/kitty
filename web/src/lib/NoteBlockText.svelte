<script lang="ts">
  let { text, references = {}, onpage, ontopic, onreference }: {
    text: string; references?: Record<string,string>; onpage:(title:string)=>void;
    ontopic:(kind:"page"|"tag",topic:string)=>void; onreference:(id:string)=>void;
  } = $props();
  const parts = $derived(Array.from(text.matchAll(/\[\[([^\]\n|]+)(?:\|([^\]\n]+))?\]\]|(?<![\p{L}\p{N}_#])#([\p{L}\p{N}_][\p{L}\p{N}_/-]*)|\(\(([0-9a-f-]{36})(?:@[0-9]{1,18})?\)\)|\{\{embed:([0-9a-f-]{36})\}\}/giu)));
</script>
{#each parts as part,index}
  {text.slice(index ? parts[index-1].index! + parts[index-1][0].length : 0,part.index)}
  {#if part[1]}<button class="text-link" onclick={()=>onpage(part[1])}>{part[2]||part[1]}</button><button class="topic-link" aria-label={`Topic ${part[1]}`} onclick={()=>ontopic("page",part[1])}>↗</button>
  {:else if part[3]}<button class="text-link" onclick={()=>ontopic("tag",part[3])}>#{part[3]}</button>
  {:else}<button class:embed={!!part[5]} class="block-reference" title="Open citation contexts" onclick={()=>onreference((part[4]||part[5]).toLowerCase())}>{references[(part[4]||part[5]).toLowerCase()]??"[loading or unavailable block]"}</button>{/if}
{/each}
{text.slice(parts.length ? parts[parts.length-1].index! + parts[parts.length-1][0].length : 0)}
<style>
  button{font:inherit;cursor:pointer;border:0;padding:0;background:none;color:var(--accent);text-align:left;white-space:pre-wrap;overflow-wrap:anywhere}.topic-link{font-size:.7em;margin:0 .3em}.block-reference{color:inherit;border-bottom:1px dashed var(--accent)}.embed{display:block;border-left:3px solid var(--accent);padding:.6em 1em;margin:.4em 0;background:var(--chrome);width:100%}
</style>
