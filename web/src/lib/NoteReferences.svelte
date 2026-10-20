<script lang="ts">
  import { Button } from '@nunera/bracket';
  import { api } from './api';
  type Ref = {kind:string;id:number|string;title:string;snippet:string;shared_terms:string[];score:number;view:string};
  let { text, pageId, onopen, onlink }: { text:string;pageId:number;onopen:(id:number)=>void;onlink:(title:string)=>void } = $props();
  let data = $state<{linked:Ref[];unlinked:Ref[];related:Ref[]}>({linked:[],unlinked:[],related:[]});
  let error = $state('');
  $effect(() => {
    const body = {text:text.slice(0,20000),exclude:`note_page:${pageId}`};
    let active = true;
    const timer = setTimeout(() => {
      void api<typeof data>('/backlinks/related',body).then(result => {if(active){data=result;error='';}}, cause => {if(active)error=cause.message;});
    }, 600);
    return () => {active=false;clearTimeout(timer);};
  });
  function open(ref:Ref) {
    if(ref.kind==='note_page')onopen(Number(ref.id));
    else {
      if(ref.kind==='flashcard_note')sessionStorage.setItem('kitty.flashcards.open-note',String(ref.id));
      location.hash='#'+ref.view;
    }
  }
</script>
<details class="references" open>
  <summary>Connections <span>{data.linked.length+data.unlinked.length+data.related.length}</span></summary>
  {#if error}<p role="alert">{error}</p>{/if}
  {#each [{key:'linked',title:'Linked'},{key:'unlinked',title:'Mentions'},{key:'related',title:'Related'}] as group}
    {#if data[group.key as keyof typeof data].length}
      <h4>{group.title}</h4>
      {#each data[group.key as keyof typeof data] as ref (`${group.key}:${ref.kind}:${ref.id}`)}
        <article><button class="reference-title" onclick={()=>open(ref)}>{ref.title}</button>
          {#if group.key==='unlinked'}<Button size="sm" variant="ghost" onclick={()=>onlink(ref.title)}>Link</Button>{/if}
          <p>{ref.snippet}</p>
          {#if group.key==='related'}<small>{ref.shared_terms.join(' · ')}</small>{/if}
        </article>
      {/each}
    {/if}
  {/each}
  {#if !data.linked.length&&!data.unlinked.length&&!data.related.length&&!error}<p class="empty">Related notes appear as you write.</p>{/if}
</details>
<style>
.references{border-top:1px solid var(--border);padding:12px 0;font-size:13px}summary{cursor:pointer;font-weight:600}summary span{color:var(--muted);margin-left:8px}h4{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:18px 0 8px}article{padding:8px 0;display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--border)}.reference-title{border:0;background:none;color:var(--accent);padding:0;text-align:left;cursor:pointer;font:inherit;font-weight:550}p{width:100%;font-size:12px;margin:0;color:var(--muted);line-height:1.5}small{color:var(--muted);font-size:11px}.empty{margin-top:12px}
</style>
