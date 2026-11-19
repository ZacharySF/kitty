<script lang="ts">
  import type { ThemeTokens } from "./contract";

  /**
   * Theme tiles: each theme's swatch and name. As radios they choose the theme in use
   * (Settings → Appearance); as checkboxes they choose which themes are kept (the Themes
   * module in the module picker).
   */
  let { themes, selected, kind, name, label, onpick }: {
    themes: readonly ThemeTokens[];
    selected: readonly string[];
    kind: "radio" | "checkbox";
    name: string;
    label: string;
    onpick: (id: string) => void;
  } = $props();
</script>

<div class="themes" role="group" aria-label={label}>
  {#each themes as item (item.id)}
    {@const on = selected.includes(item.id)}
    <label class:on title={item.label}>
      <input type={kind} {name} value={item.id} checked={on} onchange={() => onpick(item.id)} />
      <span class="swatch" style:background={item.roles.void} style:border-color={item.roles.line}
        ><i style:background={item.roles.structure}></i><i style:background={item.graph[2]}></i></span
      >
      <small>{item.label}</small>
    </label>
  {/each}
</div>

<style>
  .themes {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(62px, 1fr));
    gap: 4px;
  }
  label {
    position: relative;
    display: grid;
    justify-items: center;
    gap: 3px;
    padding: 5px 2px;
    border: 1px solid transparent;
    border-radius: 10px;
    cursor: pointer;
  }
  label:has(input:focus-visible) {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  label.on {
    border-color: color-mix(in srgb, var(--violet) 60%, transparent);
    background: var(--wash);
  }
  /* The real input covers its whole tile, invisible, so clicks and keys reach it. */
  input {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    margin: 0;
    opacity: 0;
    cursor: pointer;
  }
  small {
    max-width: 100%;
    overflow: hidden;
    color: var(--muted);
    font-size: 10.5px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  label.on small {
    color: var(--ink);
  }
  /* Theme swatches show each theme's own colours, so they are literal by design. */
  .swatch {
    display: flex;
    align-items: end;
    justify-content: center;
    gap: 3px;
    width: 34px;
    height: 22px;
    padding-bottom: 4px;
    border: 1px solid;
    border-radius: 7px;
  }
  .swatch i {
    width: 7px;
    height: 7px;
    border-radius: 999px;
  }
</style>
