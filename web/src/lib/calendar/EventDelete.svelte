<script lang="ts">
  import Icon from "../Icon.svelte";

  /**
   * The x in an event's top-right corner. Its parent is the positioned element that carries
   * `data-event`; the x shows while the pointer is over that element, while focus is inside
   * it, or while it is selected (class `picked`), so touch and the keyboard reach it too.
   * A press deletes: it neither opens the event nor starts a drag. The second press of a
   * double-click does nothing, since by then the next event may lie under the pointer.
   */
  let { title, ondelete }: { title: string; ondelete: () => void } = $props();
</script>

<!-- Holding the press back keeps a draggable event under the x from being picked up. -->
<button type="button" class="event-x" aria-label={`Delete ${title}`} title="Delete (Del)" aria-keyshortcuts="Delete Backspace"
  onmousedown={(event) => event.preventDefault()}
  onpointerdown={(event) => event.stopPropagation()}
  onclick={(event) => { event.stopPropagation(); if (event.detail < 2) ondelete(); }}><span><Icon name="close" size={12} /></span></button>

<style>
  /* A 24px target around a smaller disc in the page colour, so it reads on any event colour. */
  .event-x {
    position: absolute;
    /* The corner; on a row shorter than the target, centred on it. */
    top: min(0px, (100% - 24px) / 2);
    right: var(--event-x-right, 0px);
    z-index: 2;
    display: grid;
    place-items: center;
    width: 24px;
    height: 24px;
    padding: 0;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    cursor: pointer;
    opacity: 0;
    pointer-events: none;
    transition: opacity var(--motion-ms) var(--motion-ease);
  }
  :global([data-event]:hover) > .event-x,
  :global([data-event]:focus-within) > .event-x,
  :global([data-event].picked) > .event-x {
    opacity: 1;
    pointer-events: auto;
  }
  span {
    display: grid;
    place-items: center;
    width: 18px;
    height: 18px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: var(--canvas);
  }
  .event-x:hover span {
    border-color: var(--error);
    color: var(--error);
  }
  /* The shared ring, pulled in onto the disc's edge: an event's block clips whatever lies outside it. */
  .event-x:focus-visible {
    outline-offset: -3px;
  }
</style>
