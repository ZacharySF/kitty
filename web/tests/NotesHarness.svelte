<!-- Browser fixture for tests/notes.browser.test.mjs: one active workspace tab around the real Notes view. -->
<script>
  import Notes from "/src/lib/Notes.svelte";
  import { provideWorkspaceContext } from "/src/lib/workspaceContext.svelte.ts";

  let { initialMode = undefined } = $props();
  const trail = $state({ section: null, root: null, nodes: [] });
  provideWorkspaceContext({
    id: "harness",
    active: true,
    trail,
    setTrail(section, nodes, root) {
      trail.section = section;
      trail.nodes = nodes;
      trail.root = root ?? null;
    },
    clearTrail() {
      trail.section = null;
      trail.nodes = [];
      trail.root = null;
    },
  });
</script>

<Notes demo {initialMode} />
