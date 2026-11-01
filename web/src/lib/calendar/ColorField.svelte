<script lang="ts">
  import { PALETTE, cssColor, isSlot, subjectOf, type Subject } from "./palette";

  /**
   * Study subject plus colour for a calendar or an event. A linked subject (its
   * own, or `inherited` from the event's calendar) fixes the colour to the
   * subject's, as Study shows it; otherwise a swatch row picks a theme palette slot.
   */
  let { subjects, subject = $bindable(), color = $bindable(), inherited = null, plain = false }: {
    subjects: Subject[];
    subject: number | null | undefined;
    color: string | null | undefined;
    /** Events only: the calendar's paint, offered as the "Calendar colour" default. */
    inherited?: { color: string; subject: Subject | null; name: string } | null;
    /** Plain form labels ("Subject", as in dialogs) instead of the drawer's lowercase eyebrows. */
    plain?: boolean;
  } = $props();
  const own = $derived(subjectOf({ subject_id: subject }, subjects));
  const linked = $derived(own ?? inherited?.subject ?? null);
</script>

{#if subjects.length}
  <label class="field" class:plain><span class:eyebrow={!plain}>{plain ? "Subject" : "subject"}</span>
    <select aria-label="Study subject" value={own ? String(own.id) : ""}
      onchange={(e) => (subject = e.currentTarget.value ? Number(e.currentTarget.value) : null)}>
      <option value="">{inherited?.subject ? `From ${inherited.name} · ${inherited.subject.name}` : "No subject"}</option>
      {#each subjects as item (item.id)}<option value={String(item.id)}>{item.name}</option>{/each}
    </select>
  </label>
{/if}
<div class="field" class:plain>
  <span class:eyebrow={!plain}>{plain ? "Colour" : "colour"}</span>
  {#if linked}
    <p class="locked" title="Subject colours are set in Study">
      <i style:background={linked.color}></i>Auto · {linked.name}
      <small>matches Study</small>
      <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3.5" y="7" width="9" height="6.5" rx="1.5" /><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" /></svg>
    </p>
  {:else}
    <div class="swatches" role="radiogroup" aria-label="Colour">
      {#if inherited}
        <button type="button" role="radio" class="swatch inherit" style:--sw={inherited.color} aria-checked={!color}
          aria-label="Calendar colour" title={`${inherited.name}'s colour`} onclick={() => (color = null)}></button>
      {/if}
      {#each PALETTE as slot, index (slot)}
        <button type="button" role="radio" class="swatch" style:--sw={cssColor(slot)} aria-checked={color === slot}
          aria-label={`Theme colour ${index + 1}`} title={`Theme colour ${index + 1}`} onclick={() => (color = slot)}></button>
      {/each}
      {#if color && !isSlot(color)}
        <button type="button" role="radio" class="swatch" style:--sw={color} aria-checked="true"
          aria-label={`Custom colour ${color}`} title={`${color} · pick a theme colour to follow the theme`}></button>
      {/if}
      <small>follows the theme</small>
    </div>
  {/if}
</div>

<style>
  .field {
    display: grid;
    gap: 4px;
    margin: 0;
    font-size: 0.8rem;
  }
  .field.plain {
    font-size: inherit;
  }
  .eyebrow {
    color: var(--muted);
    padding: 0;
  }
  select {
    width: 100%;
  }
  .swatches {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
  }
  .swatch {
    width: 22px;
    height: 22px;
    padding: 0;
    border: 0;
    border-radius: 999px;
    background: var(--sw);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--solid-void) 30%, transparent);
    cursor: pointer;
  }
  .swatch.inherit {
    background: linear-gradient(135deg, var(--sw) 50%, transparent 50%);
    box-shadow: inset 0 0 0 1.5px var(--sw);
  }
  .swatch[aria-checked="true"] {
    outline: 2px solid var(--ink);
    outline-offset: 2px;
  }
  .swatch:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  small {
    color: var(--muted);
    font-size: 0.72rem;
  }
  .swatches small {
    margin-left: 4px;
  }
  .locked {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    padding: 6px 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--ink);
    width: fit-content;
  }
  .locked i {
    width: 14px;
    height: 14px;
    border-radius: 999px;
  }
  .locked svg {
    width: 13px;
    height: 13px;
    fill: none;
    stroke: var(--muted);
    stroke-width: 1.4;
  }
</style>
