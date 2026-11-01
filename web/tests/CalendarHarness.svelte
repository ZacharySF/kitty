<!-- Browser fixture for tests/calendarColors.browser.test.mjs: one active workspace tab around the real Calendar view. -->
<script>
  import Calendar from "/src/lib/Calendar.svelte";
  import { provideWorkspaceContext } from "/src/lib/workspaceContext.svelte.ts";

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

<Calendar demo />
