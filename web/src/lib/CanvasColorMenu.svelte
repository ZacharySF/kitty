<script lang="ts">
  import {onMount} from "svelte";
  let { label, value = $bindable("#f4f1fa") }: { label: string; value?: string } = $props();
  let menu: HTMLDetailsElement;
  let recent=$state<string[]>([]);
  function choose(color:string) {
    if(!/^#[0-9a-f]{6}$/i.test(color))return;
    value=color;recent=[color,...recent.filter(item=>item!==color)].slice(0,6);
    try{localStorage.setItem("kitty.ink.recent",JSON.stringify(recent));}catch{/* optional preference */}
    close();
  }
  function escape(event:KeyboardEvent){if(event.key==="Escape"&&menu.open){event.preventDefault();event.stopPropagation();close();}}
  onMount(()=>{try{const saved=JSON.parse(localStorage.getItem("kitty.ink.recent")||"[]");if(Array.isArray(saved))recent=saved.filter(item=>typeof item==="string"&&/^#[0-9a-f]{6}$/i.test(item)).slice(0,6);}catch{/* empty recents */}});
  const colors = [
    { name: "Paper", value: "#f4f1fa" }, { name: "Graphite", value: "#25212d" },
    { name: "Lavender", value: "#beafea" }, { name: "Violet", value: "#9a7ddd" },
    { name: "Blue", value: "#88b8ef" }, { name: "Teal", value: "#80c5c0" },
    { name: "Sage", value: "#a9c5aa" }, { name: "Yellow", value: "#fff2a8" },
    { name: "Peach", value: "#d9b28c" }, { name: "Orange", value: "#eda478" },
    { name: "Rose", value: "#e99eb4" }, { name: "Red", value: "#ef858f" },
  ];
  const selected = $derived(colors.find(c => c.value.toLowerCase() === value.toLowerCase())?.name ?? "Custom");
  function close() { menu.open = false; menu.querySelector("summary")?.focus(); }
</script>
<svelte:window onclick={e => { if (menu && e.target instanceof Node && !menu.contains(e.target)) menu.open = false; }} />
<details class="color-menu" bind:this={menu}>
  <summary aria-label={`${label} color: ${selected}`} onkeydown={escape}><span class="swatch" style:background={value}></span><span>{label}</span><span class="chevron">⌄</span></summary>
  <div class="color-popover" role="dialog" tabindex="-1" onkeydown={escape} aria-label={`${label} palette`}>
    <span class="palette-label">{label} color</span>
    <div class="palette-grid">{#each colors as color}<button class:chosen={value === color.value} type="button" style:background={color.value} aria-label={`${label}: ${color.name}`} aria-pressed={value === color.value} title={color.name} onclick={() => choose(color.value)}>{value === color.value ? "✓" : ""}</button>{/each}</div>
    {#if recent.length}<span class="palette-label">Recent</span><div class="palette-grid">{#each recent as color}<button type="button" style:background={color} aria-label={`${label}: ${color}`} onclick={()=>choose(color)}></button>{/each}</div>{/if}
    <label class="custom-color">Custom<input aria-label={`Custom ${label.toLowerCase()} color`} type="color" value={value} onchange={event=>choose(event.currentTarget.value)}/></label>
    <label class="custom-color">Hex<input aria-label={`${label} hex`} type="text" value={value} maxlength="7" onchange={event=>choose(event.currentTarget.value)}/></label>
  </div>
</details>
<style>
  .color-menu {position:relative;flex-shrink:0;}
  .color-menu summary {display:flex;align-items:center;gap:7px;list-style:none;padding:6px 9px;border:1px solid var(--border);border-radius:6px;background:var(--surface-raised);font-size:12px;cursor:pointer;}
  summary::-webkit-details-marker {display:none;}
  .swatch {width:14px;height:14px;border-radius:4px;border:1px solid color-mix(in srgb,var(--ink) 30%,transparent);}
  .chevron {color:var(--muted);margin-left:3px;}
  .color-popover {position:absolute;top:calc(100% + 7px);left:0;z-index:12;width:216px;padding:14px;border:1px solid var(--border);border-radius:10px;background:var(--surface);box-shadow:0 12px 32px #0005;display:grid;gap:12px;}
  .palette-label {font-size:12px;font-weight:600;}
  .palette-grid {display:grid;grid-template-columns:repeat(6,1fr);gap:7px;}
  .palette-grid button {width:25px;height:25px;padding:0;border:2px solid transparent;border-radius:6px;cursor:pointer;color:#25212d;text-shadow:0 1px 1px #fff8;}
  .palette-grid button.chosen {outline:2px solid var(--accent);outline-offset:2px;}
  .custom-color {display:flex;align-items:center;justify-content:space-between;font-size:11px;color:var(--muted);}
  .custom-color input {width:34px;height:26px;padding:2px;border:1px solid var(--border);border-radius:4px;}
  .custom-color input[type="text"]{width:90px;color:var(--ink);background:var(--surface);font:12px var(--br-font-mono);}
</style>
