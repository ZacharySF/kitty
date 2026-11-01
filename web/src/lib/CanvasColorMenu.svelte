<script lang="ts" module>
  /** The fixed canvas palette (Q48): stored colours are literal hex so drawings never change. */
  export const CANVAS_COLORS = [
    { name: "Paper", value: "#f4f1fa" }, { name: "Graphite", value: "#25212d" },
    { name: "Lavender", value: "#beafea" }, { name: "Violet", value: "#9a7ddd" },
    { name: "Blue", value: "#88b8ef" }, { name: "Teal", value: "#80c5c0" },
    { name: "Sage", value: "#a9c5aa" }, { name: "Yellow", value: "#fff2a8" },
    { name: "Peach", value: "#d9b28c" }, { name: "Orange", value: "#eda478" },
    { name: "Rose", value: "#e99eb4" }, { name: "Red", value: "#ef858f" },
  ];
  const RECENT_KEY = "kitty.ink.recent";
  const RECENT_COUNT = 5;
  const hex = /^#[0-9a-f]{6}$/i;
  // Until five colours have been picked, the row is topped up from the palette.
  const STARTERS = ["#f4f1fa", "#beafea", "#88b8ef", "#80c5c0", "#e99eb4"];
  function saved(): string[] {
    try {
      const value = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
      return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && hex.test(item)).map(item => item.toLowerCase()) : [];
    } catch {
      return [];
    }
  }
  // One recents list for every picker (ink, fill, card), persisted across sessions.
  let recent = $state<string[]>(saved());
  function remember(color: string) {
    recent = [color, ...recent.filter(item => item !== color)].slice(0, RECENT_COUNT);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(recent)); } catch { /* optional preference */ }
  }
</script>

<script lang="ts">
  import Icon from "./Icon.svelte";

  /**
   * Q67: a row of round swatches with the last five colours used; + opens all twelve
   * named colours and a hex field. `placement` says where the palette opens.
   */
  let { label, value = $bindable("#f4f1fa"), placement = "up" }: { label: string; value?: string; placement?: "up" | "down" | "inline" } = $props();
  let open = $state(false), plus = $state<HTMLButtonElement>(), root = $state<HTMLDivElement>();
  const row = $derived([...new Set([...recent, ...STARTERS])].slice(0, RECENT_COUNT));
  const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();
  const nameOf = (color: string) => CANVAS_COLORS.find(item => same(item.value, color))?.name ?? color;
  function pick(color: string) {
    if (!hex.test(color)) return;
    value = color.toLowerCase();
    remember(value);
    close();
  }
  function close() {
    if (!open) return;
    open = false;
    plus?.focus();
  }
  function escape(event: KeyboardEvent) {
    if (event.key !== "Escape" || !open) return;
    event.preventDefault();
    event.stopPropagation();
    close();
  }
</script>

<svelte:window onpointerdown={event => { if (open && event.target instanceof Node && !root?.contains(event.target)) open = false; }} />
<div class="color-row" class:inline={placement === "inline"} bind:this={root} role="group" aria-label={`${label} colours`}>
  {#each row as color (color)}
    <button type="button" class="dot" style:--dot={color} aria-label={`${label}: ${nameOf(color)}`} aria-pressed={same(value, color)} title={nameOf(color)} onclick={() => value = color}></button>
  {/each}
  <button bind:this={plus} type="button" class="dot plus" aria-label={`All ${label.toLowerCase()} colours`} aria-expanded={open} title="All colours" onclick={() => open = !open} onkeydown={escape}><Icon name="plus" size={13}/></button>
  {#if open}
    <div class={`palette ${placement}`} role="dialog" tabindex="-1" aria-label={`${label} palette`} onkeydown={escape}>
      {#if placement !== "inline"}<span class="eyebrow">{label} colour</span>{/if}
      <div class="named">
        {#each CANVAS_COLORS as color (color.value)}
          <button type="button" aria-label={`${label}: ${color.name}`} aria-pressed={same(value, color.value)} onclick={() => pick(color.value)}><span class="dot" style:--dot={color.value}></span><small>{color.name}</small></button>
        {/each}
      </div>
      <label class="hex">Hex<input aria-label={`${label} hex`} type="text" value={value} maxlength="7" spellcheck="false" onchange={event => pick(event.currentTarget.value.trim())}/></label>
    </div>
  {/if}
</div>

<style>
  .color-row { position: relative; display: flex; align-items: center; gap: 2px; }
  .color-row.inline { flex-wrap: wrap; }
  .dot {
    flex-shrink: 0;
    width: 20px;
    height: 20px;
    padding: 0;
    border: 2px solid transparent;
    border-radius: 50%;
    background: var(--dot) content-box;
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ink) 20%, transparent);
    cursor: pointer;
    transition: transform 140ms ease;
  }
  button.dot:hover { transform: scale(1.12); }
  .dot[aria-pressed="true"] { border-color: var(--dot); }
  .dot.plus {
    display: grid;
    place-items: center;
    margin-left: 2px;
    border: 1px dashed color-mix(in srgb, var(--ink) 35%, transparent);
    background: none;
    box-shadow: none;
    color: var(--muted);
  }
  .dot.plus:hover, .dot.plus[aria-expanded="true"] { color: var(--ink); border-color: var(--violet); }
  .palette {
    position: absolute;
    left: 50%;
    z-index: 6;
    display: grid;
    gap: 10px;
    width: 264px;
    box-sizing: border-box;
    padding: 12px;
    border: 1px solid var(--hairline);
    border-radius: 14px;
    background: color-mix(in srgb, var(--solid-raised) 94%, transparent);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    box-shadow: var(--overlay-shadow);
    transform: translateX(-50%);
    white-space: normal;
    animation: palette-in 140ms ease-out;
  }
  .palette.up { bottom: calc(100% + 14px); }
  .palette.down { top: calc(100% + 8px); left: auto; right: 0; transform: none; }
  .palette.inline { position: static; width: 100%; transform: none; padding: 10px 0 0; border: 0; border-radius: 0; background: none; backdrop-filter: none; -webkit-backdrop-filter: none; box-shadow: none; }
  .color-row.inline .palette { flex-basis: 100%; }
  @keyframes palette-in { from { opacity: 0; } }
  .palette .eyebrow { margin: 0 0 -4px; }
  .named { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; }
  .named button {
    display: grid;
    justify-items: center;
    gap: 3px;
    padding: 5px 0 4px;
    border: 0;
    border-radius: 10px;
    background: none;
    color: var(--muted);
    cursor: pointer;
  }
  .named button:hover, .named button[aria-pressed="true"] { color: var(--ink); background: var(--wash); }
  .named .dot { width: 24px; height: 24px; cursor: inherit; }
  .named small { font: 9.5px var(--font-mono); letter-spacing: 0.06em; text-transform: uppercase; }
  .hex { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 11px; }
  .hex input { width: 96px; padding: 4px 7px; border: 1px solid var(--hairline); border-radius: 6px; background: var(--solid-inset); color: var(--ink); font: 12px var(--font-mono); }
  @media (prefers-reduced-motion: reduce) {
    .dot { transition: none; }
    button.dot:hover { transform: none; }
    .palette { animation: none; }
  }
</style>
