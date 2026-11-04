<script lang="ts" module>
  import type { Guarantee } from "./agents";

  const SHIELD = "M12 3 20 6v6c0 5-8 9-8 9s-8-4-8-9V6Z";
  /** Each level has its own mark, so it reads without colour (one-accent themes, colour blindness). */
  const MARKS: Record<Guarantee, string> = {
    full: `${SHIELD}m-4 9 3 3 5-6`,
    attribution: `${SHIELD}M12 8v5m0 3h.01`,
    none: `${SHIELD}M4 4l16 16`,
  };
</script>

<script lang="ts">
  import { ICON_STROKE } from "../icons";
  import { GUARANTEE_HINTS, GUARANTEE_LABELS, guaranteeOf } from "./agents";

  /**
   * An agent's mediation guarantee (Full / Attribution only / Not mediated) in the theme's
   * roles: success, warning, error. `compact` draws only the shield (a Rika bar chip); its
   * name and tooltip still say the level. `detail` replaces the tooltip (a launch's message).
   */
  let { guarantee, compact = false, detail = "" }: { guarantee: unknown; compact?: boolean; detail?: string } = $props();
  const level = $derived(guaranteeOf(guarantee));
  const label = $derived(GUARANTEE_LABELS[level]);
  const size = $derived(compact ? 11 : 12);
</script>

<span class="guarantee" class:compact data-guarantee={level} role="img" aria-label={`Guarantee: ${label}`} title={detail || `${label}: ${GUARANTEE_HINTS[level]}`}
  ><svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width={ICON_STROKE * 1.4} stroke-linecap="round"
    stroke-linejoin="round" aria-hidden="true"><path d={MARKS[level]} /></svg
  >{#if !compact}<span aria-hidden="true">{label}</span>{/if}</span>

<style>
  .guarantee {
    --tone: var(--muted);
    display: inline-flex;
    flex-shrink: 0;
    align-items: center;
    gap: 4px;
    height: 18px;
    padding: 0 7px 0 5px;
    border: 1px solid color-mix(in srgb, var(--tone) 50%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--tone) 10%, transparent);
    /* Mixed toward the ink so the label keeps its contrast on light themes too. */
    color: color-mix(in srgb, var(--tone) 72%, var(--ink));
    font: 600 10.5px / 1 var(--font-ui);
    white-space: nowrap;
    cursor: help;
  }
  .guarantee[data-guarantee="full"] {
    --tone: var(--success);
  }
  .guarantee[data-guarantee="attribution"] {
    --tone: var(--warning);
  }
  .guarantee[data-guarantee="none"] {
    --tone: var(--error);
  }
  .compact {
    width: 16px;
    height: 16px;
    padding: 0;
    justify-content: center;
    border: 0;
    background: transparent;
  }
</style>
