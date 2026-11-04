<!-- Browser fixture for tests/blockEditor.browser.test.mjs: the real block editor, History and Trash. -->
<script>
  import BlockEditor, { BLOCK_DRAG_TYPE } from "/src/lib/BlockEditor.svelte";
  import BlockHistory from "/src/lib/BlockHistory.svelte";
  import BlockTrash from "/src/lib/BlockTrash.svelte";

  let { opened } = $props();
  let editor = $state();
  let history = $state(false);
  let trash = $state(false);
  let moved = $state("");
</script>

<nav aria-label="Harness pages">
  <button
    ondragover={(event) => event.preventDefault()}
    ondrop={async (event) => {
      event.preventDefault();
      const { ids } = JSON.parse(event.dataTransfer.getData(BLOCK_DRAG_TYPE));
      if (await editor.moveToPage(ids, { type: "page", number: 2 })) moved = `Moved ${ids.length} to Second page`;
    }}>Second page</button>
  <button onclick={() => (history = true)}>Open history</button>
  <button onclick={() => (trash = true)}>Open trash</button>
  <span role="status">{moved}</span>
</nav>
<BlockEditor bind:this={editor} target={{ type: "page", number: 1 }} label="Synthetic page blocks" pages={[{ number: 2, title: "Second page" }]}
  onopenpage={(id) => opened.push(id)} />
{#if history}<BlockHistory page={{ type: "page", number: 1 }} title="Synthetic page" onclose={() => (history = false)} />{/if}
{#if trash}<BlockTrash onclose={() => (trash = false)} />{/if}
