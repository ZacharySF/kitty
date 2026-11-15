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
    status: null,
    setStatus(status) {
      window.notesHarness.status = status;
    },
    // One tab system: the harness is a single tab; it records what the view reports and asks for.
    notePage: null,
    setNotePage(id, title) {
      window.notesHarness.notePage = id ? { id, title } : null;
    },
    focusNotePage: () => false,
    openNotePageTab(id) {
      window.notesHarness.newTabs = [...(window.notesHarness.newTabs ?? []), id];
    },
  });
</script>

<Notes demo {initialMode} />
