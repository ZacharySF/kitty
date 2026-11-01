<script module lang="ts">
  export type NoteBlock = {id:string;page_id?:number;parent_id:string|null;text:string;ordering:number;collapsed:boolean};
  export type BlocksResult = {revision:number;blocks:NoteBlock[];references:Record<string,string>;citation_counts:Record<string,number>};
</script>
<script lang="ts">
  import { tick } from "svelte";
  import { api } from "./api";
  import NoteBlockText from "./NoteBlockText.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  const pane = useWorkspaceContext();
  let root: HTMLDivElement;
  let {pageId,blocks,references,counts,pages,onchange,onpage,onopen,focusBlock="",preview=$bindable(false)}: {
    pageId:number;blocks:NoteBlock[];references:Record<string,string>;counts:Record<string,number>;
    pages:{id:number;title:string}[];onchange:(blocks:NoteBlock[])=>void;onpage:(title:string)=>void;
    onopen:(id:number,blockId?:string)=>void;focusBlock?:string;preview?:boolean;
  }=$props();
  type CitationContext = {id:string;page_id:number|null;title:string;text:string;count:number;contexts:{id:string;page_id:number;title:string;text:string;parent_text?:string}[];next_offset:number|null};
  type TopicContext = {kind:string;topic:string;blocks:{id:string;page_id:number;title:string;text:string}[];count:number;next_offset:number|null};
  let context=$state<CitationContext|null>(null);
  let topic=$state<TopicContext|null>(null);
  let error=$state(""),menu=$state<{id:string;x:number;y:number}|null>(null);
  function treeOrder(values: NoteBlock[]): NoteBlock[] {
    const sorted = [...values].sort((a,b) => a.ordering-b.ordering || a.id.localeCompare(b.id));
    const children = new Map<string|null, NoteBlock[]>();
    for (const block of sorted) { const siblings = children.get(block.parent_id) || []; siblings.push(block); children.set(block.parent_id, siblings); }
    const result: NoteBlock[] = [];
    function visit(parent: string|null) { for (const block of children.get(parent) || []) { result.push(block); visit(block.id); } }
    visit(null);
    return result;
  }
  const ordered=$derived(treeOrder(blocks));
  const mentions=$derived(ordered.flatMap(block=>{
    const plain=block.text.replace(/\[\[[^\]]*\]\]/g,"");
    return pages.filter(page=>page.id!==pageId && page.title.trim() && new RegExp(`(^|[^\\p{L}\\p{N}_])${page.title.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")}(?=$|[^\\p{L}\\p{N}_])`,"iu").test(plain)).map(page=>({block,page}));
  }).slice(0,100));
  function change(id:string,patch:Partial<NoteBlock>){onchange(blocks.map(block=>block.id===id?{...block,...patch}:block));}
  async function focus(id:string,position=0){await tick();if(!pane.active)return;const field=root?.querySelector<HTMLTextAreaElement>(`#${CSS.escape(`${pane.id}-note-block-${id}`)}`);if(!field?.getClientRects().length){root?.querySelector(`[data-block="${CSS.escape(id)}"]`)?.scrollIntoView({block:"nearest"});return;}field.focus();field.setSelectionRange(position,position);field.scrollIntoView({block:"nearest"});}
  $effect(()=>{if(focusBlock)void focus(focusBlock);});
  export function add(){const id=crypto.randomUUID();onchange([...ordered,{id,parent_id:null,text:"",ordering:ordered.length,collapsed:false}]);void focus(id);}
  function descendants(id:string):Set<string>{const found=new Set([id]);let changed=true;while(changed){changed=false;for(const b of blocks)if(b.parent_id&&found.has(b.parent_id)&&!found.has(b.id)){found.add(b.id);changed=true;}}return found;}
  function key(event:KeyboardEvent,block:NoteBlock){
    if(!pane.active||event.isComposing)return;
    const field=event.currentTarget as HTMLTextAreaElement;
    if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();const start=field.selectionStart,end=field.selectionEnd,id=crypto.randomUUID();const family=descendants(block.id);const index=ordered.reduce((last,b,i)=>family.has(b.id)?i+1:last,0);const next=ordered.map(b=>b.id===block.id?{...b,text:b.text.slice(0,start)}:{...b});next.splice(index,0,{id,parent_id:block.parent_id,text:block.text.slice(end),ordering:index,collapsed:false});onchange(next.map((b,i)=>({...b,ordering:i})));void focus(id);}
    if(event.key==="Tab"){
      event.preventDefault();let parent=block.parent_id;
      if(event.shiftKey)parent=blocks.find(b=>b.id===parent)?.parent_id??null;
      else {const siblings=ordered.filter(b=>b.parent_id===block.parent_id);const previous=siblings[siblings.findIndex(b=>b.id===block.id)-1];if(!previous)return;parent=previous.id;}
      const next=blocks.map(b=>b.id===block.id?{...b,parent_id:parent}:b.id===parent?{...b,collapsed:false}:b);
      onchange(treeOrder(next).map((b,i)=>({...b,ordering:i})));void focus(block.id,field.selectionStart);
    }
  }
  function remove(id:string){if(!pane.active||!confirm("Delete this block and its nested children? References will show that the original is unavailable."))return;const ids=descendants(id);onchange(ordered.filter(b=>!ids.has(b.id)).map((b,i)=>({...b,ordering:i})));menu=null;}
  async function citations(id:string){try{context=await api(`/notes/blocks/${id}/contexts`);topic=null;error="";}catch(cause){error=(cause as Error).message;}}
  async function showTopic(kind:"page"|"tag",name:string){try{topic=await api(`/notes/topics?kind=${kind}&topic=${encodeURIComponent(name)}`);context=null;error="";}catch(cause){error=(cause as Error).message;}}
  async function moreContexts() {
    const current = context;
    if (!current || current.next_offset === null) return;
    try { const result=await api<CitationContext>(`/notes/blocks/${current.id}/contexts?offset=${current.next_offset}`); if(context===current)context={...result,contexts:[...current.contexts,...result.contexts]}; }
    catch(cause) {error=(cause as Error).message;}
  }
  async function moreTopics() {
    const current=topic;
    if(!current || current.next_offset===null)return;
    try {const result=await api<TopicContext>(`/notes/topics?kind=${current.kind}&topic=${encodeURIComponent(current.topic)}&offset=${current.next_offset}`);if(topic===current)topic={...result,blocks:[...current.blocks,...result.blocks]};}
    catch(cause){error=(cause as Error).message;}
  }
  async function copyReference(value:string) {
    try { await navigator.clipboard.writeText(value); }
    catch { error=`Copy this reference: ${value}`; }
  }
  function link(block:NoteBlock,title:string){
    const escaped=title.replace(/[.*+?^${}()|[\]\\]/g,"\\$&");
    const pattern=new RegExp(`(^|[^\\p{L}\\p{N}_])(${escaped})(?=$|[^\\p{L}\\p{N}_])`,"iu");
    let linked=false;
    const text=block.text.split(/(\[\[[^\]]*\]\])/g).map(part=>{
      if(linked||part.startsWith("[[")||!pattern.test(part))return part;
      linked=true;return part.replace(pattern,"$1[[$2]]");
    }).join("");
    if(linked)change(block.id,{text});
  }
</script>
<svelte:window onclick={()=>{if(pane.active)menu=null;}} onkeydown={event=>{if(pane.active&&event.key==="Escape")menu=null;}}/>
{#snippet branch(parent:string|null,depth:number)}
  {#each ordered.filter(b=>b.parent_id===parent) as block (block.id)}
    <div class="block-row" role="group" data-block={block.id} aria-label={`Outline block ${block.ordering+1}`} style:padding-left={`${depth*22}px`} class:focused={focusBlock===block.id} oncontextmenu={event=>{event.preventDefault();menu={id:block.id,x:event.clientX,y:event.clientY};}}>
      <button class="bullet" aria-label={block.collapsed?"Expand block":"Collapse block"} aria-expanded={!block.collapsed} onclick={()=>change(block.id,{collapsed:!block.collapsed})} oncontextmenu={event=>{event.preventDefault();menu={id:block.id,x:event.clientX,y:event.clientY};}}>{blocks.some(b=>b.parent_id===block.id)?block.collapsed?"▸":"▾":"•"}</button>
      <div class="block-body">
        {#if preview}<div class="rendered"><NoteBlockText text={block.text} {references} onpage={onpage} ontopic={showTopic} onreference={citations}/></div>
        {:else}<textarea id={`${pane.id}-note-block-${block.id}`} aria-label={`Block ${block.ordering+1}`} rows={Math.max(1,block.text.split("\n").length)} value={block.text} placeholder="Write a thought…" oninput={event=>change(block.id,{text:event.currentTarget.value})} onkeydown={event=>key(event,block)}></textarea>
        {#if /\[\[|#\S|\(\(|\{\{embed:/.test(block.text)}<div class="inline-preview"><NoteBlockText text={block.text} {references} onpage={onpage} ontopic={showTopic} onreference={citations}/></div>{/if}{/if}
      </div>
      <div class="block-tools">
        <button title="Copy block reference" aria-label="Copy block reference" onclick={()=>void copyReference(`((${block.id}))`)}>⧉</button>
        <button title="Copy live embed" aria-label="Copy live block embed" onclick={()=>void copyReference(`{{embed:${block.id}}}`)}>↪</button>
        <button class="citations" title="Referencing block contexts" onclick={()=>void citations(block.id)}>{counts[block.id]||0} citations</button>
        <button aria-label="Block menu" onclick={event=>{menu={id:block.id,x:event.clientX,y:event.clientY};event.stopPropagation();}}>⋯</button>
      </div>
    </div>
    {#if !block.collapsed}{@render branch(block.id,depth+1)}{/if}
  {/each}
{/snippet}
<div class="text-note" bind:this={root}>
  {#if error}<p role="alert">{error}</p>{/if}
  <div class="text-layout">
    <article class="outline" aria-label="Text note outline">{@render branch(null,0)}{#if !blocks.length}<button class="empty" onclick={add}>Start writing a text note</button>{/if}
      {#if mentions.length}<section class="mentions"><h3>Unlinked title mentions</h3>{#each mentions as mention}<div><span>{mention.block.text.slice(0,160)}</span><button onclick={()=>link(mention.block,mention.page.title)}>Link [[{mention.page.title}]]</button></div>{/each}</section>{/if}
    </article>
    {#if context||topic}
      <aside class="contexts" aria-label="Reference contexts">
        <button class="close" onclick={()=>{context=null;topic=null;}}>Close ×</button>
        {#if context}
          <h3>{context.count} citations</h3>
          {#if context.page_id}<button class="context original" onclick={()=>context?.page_id&&onopen(context.page_id,context.id)}><strong>Original · {context.title}</strong><span>{context.text}</span></button>{:else}<p class="original">{context.text}</p>{/if}
          {#each context.contexts as item}
            <button class="context" onclick={()=>onopen(item.page_id,item.id)}>
              <strong>{item.title}</strong>{#if item.parent_text}<small>{item.parent_text}</small>{/if}<span>{item.text}</span>
            </button>
          {:else}<p>No blocks reference this original yet.</p>{/each}
          {#if context.next_offset!==null}<button onclick={()=>void moreContexts()}>Show more citation contexts ({context.contexts.length} of {context.count})</button>{/if}
        {:else if topic}
          <h3>{topic.kind==="tag"?"#":"[["}{topic.topic}{topic.kind==="tag"?"":"]]"}</h3>
          {#each topic.blocks as item}<button class="context" onclick={()=>onopen(item.page_id,item.id)}><strong>{item.title}</strong><span>{item.text}</span></button>{:else}<p>No matching blocks.</p>{/each}
          {#if topic.next_offset!==null}<button onclick={()=>void moreTopics()}>Show more topic blocks ({topic.blocks.length} of {topic.count})</button>{/if}
        {/if}
      </aside>
    {/if}
  </div>
</div>
{#if menu}<div class="block-menu" role="menu" style:left={`${Math.min(menu.x,window.innerWidth-180)}px`} style:top={`${Math.min(menu.y,window.innerHeight-70)}px`}><button role="menuitem" onclick={()=>menu&&remove(menu.id)}>Delete block…</button></div>{/if}
<style>
  .text-note{min-height:500px}.text-layout{display:flex;min-width:0}.outline{flex:1;min-width:0;max-width:950px;margin:0 auto;padding:16px 24px 80px}.block-row{display:flex;align-items:flex-start;gap:6px;min-height:38px}.block-row.focused{background:color-mix(in srgb,var(--accent) 8%,transparent)}.bullet{width:20px;margin-top:5px;color:var(--muted);flex-shrink:0}.block-body{flex:1;min-width:0}textarea{width:100%;min-height:32px;resize:vertical;border:0;border-radius:0;background:transparent;color:var(--ink);font:inherit;font-size:15px;line-height:1.8;padding:0 4px;field-sizing:content}textarea:focus{outline:1px solid var(--border)}.rendered{white-space:pre-wrap;overflow-wrap:anywhere;font-size:15px;line-height:1.8;padding:0 4px}.inline-preview{white-space:pre-wrap;font-size:13px;line-height:1.6;color:var(--muted);padding:4px 8px 12px}.block-tools{display:flex;align-items:center;gap:4px;opacity:.35;font-size:10px;padding-top:6px}.block-row:hover .block-tools,.block-row:focus-within .block-tools{opacity:1}button{font:inherit;color:inherit;cursor:pointer;background:none;border:0;border-radius:4px;padding:4px 6px}button:hover{background:var(--surface-raised)}.citations{white-space:nowrap;color:var(--accent)}.contexts{width:280px;flex-shrink:0;border-left:1px solid var(--border);padding:16px;max-height:75vh;overflow:auto;background:var(--chrome)}.contexts h3{font-size:14px}.context{display:flex;flex-direction:column;gap:6px;width:100%;padding:12px 4px;text-align:left;border-bottom:1px solid var(--border);white-space:pre-wrap;overflow-wrap:anywhere}.context span,.original{font-size:12px;line-height:1.6}.context small{color:var(--muted)}.close{float:right;font-size:11px}.mentions{margin-top:50px;border-top:1px solid var(--border);padding-top:15px;font-size:12px;color:var(--muted)}.mentions h3{font-size:12px}.mentions div{display:flex;justify-content:space-between;gap:12px;padding:8px 0}.mentions button{color:var(--accent)}.block-menu{position:fixed;z-index:90;padding:6px;background:var(--surface);border:1px solid var(--border);box-shadow:0 8px 20px #0003}.empty{padding:28px;color:var(--muted)}@media(max-width:900px){.block-tools{flex-direction:column}.contexts{width:220px}.outline{padding:12px 8px}}@media(max-width:650px){.text-layout{flex-direction:column}.contexts{width:auto;border-left:0;border-top:1px solid var(--border)}}
</style>
