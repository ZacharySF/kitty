<script lang="ts">
  import type { Snippet } from "svelte";
  import Icon from "../Icon.svelte";
  import Popover, { type PopoverPlacement, type PopoverTriggerProps } from "./Popover.svelte";

  /**
   * Q8: creating anything is a small popover anchored to its + button: one
   * natural-language field plus at most two essential chips (a date, a subject).
   * Enter saves; Escape closes and keeps the phrase; More hands the phrase to the
   * full editor (the EditDrawer). The screen parses the phrase and renders the chips.
   */
  let {
    label,
    placeholder = "",
    value = $bindable(""),
    open = $bindable(false),
    placement = "bottom-end",
    chips,
    trigger: custom,
    onsave,
    onmore,
  }: {
    /** The action's name: the + button's text and the popover's and field's name. */
    label: string;
    placeholder?: string;
    value?: string;
    open?: boolean;
    placement?: PopoverPlacement;
    /** Up to two essentials, shown under the phrase. */
    chips?: Snippet;
    /** Replaces the default 32px "+ label" primary action. */
    trigger?: Snippet<[PopoverTriggerProps]>;
    /** Return `false` to keep the popover open (for example on a parse error). */
    onsave: (text: string) => void | boolean | Promise<void | boolean>;
    onmore?: (text: string) => void;
  } = $props();

  let popover = $state<{ close: (refocus?: boolean) => void }>();
  let saving = $state(false);

  async function save(event: SubmitEvent) {
    event.preventDefault();
    const text = value.trim();
    if (!text || saving) return;
    saving = true;
    try {
      if ((await onsave(text)) === false) return;
      value = "";
      popover?.close(true);
    } finally {
      saving = false;
    }
  }

  function more() {
    popover?.close(false);
    onmore?.(value.trim());
  }
</script>

<Popover bind:this={popover} bind:open {label} {placement} class="quick-add">
  {#snippet trigger(props)}
    {#if custom}{@render custom(props)}{:else}
      <button type="button" class="pill primary page-action" {...props}><Icon name="plus" size={14} />{label}</button>
    {/if}
  {/snippet}
  <form onsubmit={save}>
    <label class="phrase">
      <Icon name="plus" size={15} />
      <input class="phrase-input" data-autofocus aria-label={label} {placeholder} bind:value autocomplete="off" spellcheck="false" />
    </label>
    {#if chips}<div class="quick-chips">{@render chips()}</div>{/if}
    <div class="foot">
      {#if onmore}<button type="button" class="more" onclick={more}>More <span aria-hidden="true">›</span></button>{/if}
      <span class="grow"></span>
      <kbd aria-hidden="true">Esc</kbd>
      <button type="submit" class="pill primary save" disabled={saving || !value.trim()}>Save <kbd aria-hidden="true">↵</kbd></button>
    </div>
  </form>
</Popover>

<style>
  :global(.kitty-popover.quick-add) {
    width: min(380px, calc(100vw - 16px));
    padding: 10px;
  }
  form {
    display: grid;
    gap: 10px;
  }
  .phrase {
    display: flex;
    align-items: center;
    gap: 9px;
    height: var(--control-h-lg);
    padding: 0 10px;
    border: 1px solid var(--accent);
    border-radius: var(--r-field);
    background: color-mix(in srgb, var(--solid-inset, #111) 50%, transparent);
    color: var(--accent);
  }
  /* Outranks the global field fill: the phrase input sits flush in its frame. */
  :global(:root body) .phrase input.phrase-input {
    flex: 1;
    min-width: 0;
    height: 100%;
    padding: 0;
    border: 0;
    background: none;
    color: var(--ink);
    font: 400 13px var(--font-ui);
    outline: none;
  }
  .quick-chips {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
  }
  .foot {
    display: flex;
    align-items: center;
    gap: 8px;
    padding-top: 8px;
    border-top: 1px solid var(--hairline);
  }
  .grow {
    flex: 1;
  }
  .more {
    display: inline-flex;
    gap: 4px;
    padding: 0;
    border: 0;
    background: none;
    color: var(--muted);
    font: 500 var(--fs-control) var(--font-ui);
  }
  .more:hover {
    color: var(--ink);
  }
  kbd {
    padding: 2px 5px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-chip);
    color: var(--subtle);
    font-size: 9.5px;
    letter-spacing: 0.06em;
  }
  .save kbd {
    border-color: color-mix(in srgb, var(--solid-void, #141414) 30%, transparent);
    color: color-mix(in srgb, var(--solid-void, #141414) 70%, transparent);
  }
</style>
