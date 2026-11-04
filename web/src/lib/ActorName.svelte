<script lang="ts">
  // Who made an edit, drawn as its icon and name in its theme role (actors.ts). The icon carries
  // the pure role; the name mixes it toward ink so it stays AA on every theme's surfaces.
  import Icon from "./Icon.svelte";
  import { actorLook, actorStyle } from "./actors";
  import type { Actor } from "./live";

  let { actor, label, class: className = "" }: { actor: Actor | null | undefined; label: string; class?: string } = $props();
  const look = $derived(actorLook(actor));
</script>

<span class="actor {className}" data-actor={look.id} style={actorStyle(actor)} title={look.label}
  ><Icon name={look.icon} size={13} /><span class="name">{label}</span></span
>

<style>
  .actor {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    min-width: 0;
    max-width: 100%;
    color: color-mix(in srgb, var(--actor) 66%, var(--ink));
    font: 600 var(--fs-label, 12px) / 1.3 var(--font-ui);
  }
  .actor :global(svg) {
    flex-shrink: 0;
    color: var(--actor);
  }
  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
