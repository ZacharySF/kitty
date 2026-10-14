<script lang="ts">
  import { onMount } from "svelte";
  import { shortcutGroups } from "./workspace";
  let { onclose }: { onclose: () => void } = $props();
  let dialog: HTMLDialogElement;
  onMount(() => dialog.showModal());
</script>

<dialog
  bind:this={dialog}
  class="workspace-dialog shortcuts"
  aria-labelledby="shortcuts-title"
  oncancel={onclose}
>
  <header>
    <div>
      <span class="eyebrow">KEEP YOUR HANDS ON THE KEYS</span>
      <h2 id="shortcuts-title">Keyboard shortcuts</h2>
    </div>
    <button
      class="icon-button"
      onclick={onclose}
      aria-label="Close keyboard help">×</button
    >
  </header>
  <p class="muted">
    Letter shortcuts pause while you type or use a dialog. Actions are
    contextual; controls remain available by mouse. Browser print, reload, and
    close-tab shortcuts are untouched.
  </p>
  {#each shortcutGroups as group}<section>
      <h3>{group.title}</h3>
      <dl>
        {#each group.shortcuts as [keys, action]}<div>
            <dt><kbd>{keys}</kbd></dt>
            <dd>{action}</dd>
          </div>{/each}
      </dl>
    </section>{/each}
</dialog>

<style>
  .shortcuts {
    max-width: 760px;
  }
  .shortcuts > p {
    font-size: 12px;
    line-height: 1.8;
  }
  .shortcuts section {
    border-top: 1px solid var(--border);
    padding: 20px 0;
  }
  h3 {
    font-size: 13px;
  }
  dl {
    margin: 0;
  }
  dl div {
    display: grid;
    grid-template-columns: 180px 1fr;
    gap: 16px;
    padding: 9px 0;
    font-size: 12px;
  }
  dd {
    margin: 0;
    color: var(--muted);
    line-height: 1.6;
  }
  @media (max-width: 520px) {
    dl div {
      grid-template-columns: 110px 1fr;
      gap: 10px;
    }
    kbd {
      font-size: 10px;
    }
  }
</style>
