<!-- Browser fixture for tests/atlasNodes.browser.test.mjs: one workspace tab holding the real atlas. -->
<script lang="ts">
  import GraphNavigator from "../src/lib/GraphNavigator.svelte";
  import { provideWorkspaceContext, type WorkspaceTrail } from "../src/lib/workspaceContext.svelte";

  let { id, opened }: { id: string; opened: (view: string) => void } = $props();
  let atlas: GraphNavigator | undefined = $state();
  const trail: WorkspaceTrail = { section: null, root: null, nodes: [] };
  provideWorkspaceContext({
    get id() { return id; },
    active: true,
    trail,
    setTrail: () => {},
    clearTrail: () => {},
    status: null,
    setStatus: () => {},
  });
  export function nodePoint(node: string) {
    return atlas?.nodePoint(node) ?? null;
  }
</script>

<GraphNavigator bind:this={atlas} workspace={null} revision={0} onview={opened} onopenjob={() => {}} onguide={() => {}} />
