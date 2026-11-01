<script lang="ts" module>
  export type SaveState = "saved" | "unsaved" | "failed";
  const WORDS: Record<SaveState, string> = { saved: "Saved", unsaved: "Unsaved changes", failed: "Couldn't save" };
</script>

<script lang="ts">
  /**
   * Q3: status as a 6px dot on a tab or title instead of status text. Neutral when
   * saved, the warning colour when unsaved, danger when saving failed; `detail`
   * (shown as the tooltip and read as its name) explains or offers the shortcut.
   */
  let { state, detail = "" }: { state: SaveState; detail?: string } = $props();
  const text = $derived(detail || WORDS[state]);
</script>

<span class="save-dot" data-state={state} role="img" aria-label={text} title={text}></span>

<style>
  .save-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 999px;
    background: var(--subtle);
    flex-shrink: 0;
  }
  .save-dot[data-state="unsaved"] {
    background: var(--warning);
  }
  .save-dot[data-state="failed"] {
    background: var(--error);
  }
</style>
