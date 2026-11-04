<!-- Browser fixture for tests/rikaSettings.browser.test.mjs: one active workspace tab around the real Settings view, plus the real Rika UI. -->
<script lang="ts">
  import WorkspaceSettings from "../src/lib/WorkspaceSettings.svelte";
  import Rika from "../src/lib/rika/Rika.svelte";
  import { provideWorkspaceContext, type WorkspaceTrail } from "../src/lib/workspaceContext.svelte";

  let { opened }: { opened: (view: string) => void } = $props();
  const trail: WorkspaceTrail = { section: null, root: null, nodes: [] };
  provideWorkspaceContext({
    id: "harness",
    active: true,
    trail,
    setTrail: () => {},
    clearTrail: () => {},
    status: null,
    setStatus: () => {},
  });
</script>

<main id="view"><WorkspaceSettings onview={opened} onchange={async () => {}} /></main>
<Rika onnavigate={opened} />
