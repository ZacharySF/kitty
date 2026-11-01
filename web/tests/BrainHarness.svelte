<!-- Browser fixture for tests/brain.browser.test.mjs: one workspace tab holding the Brain stage,
     the real home atlas, or a section graph, so the Brain look can be checked everywhere it may appear. -->
<script lang="ts">
  import BrainStage from "../src/lib/brain/BrainStage.svelte";
  import GraphNavigator from "../src/lib/GraphNavigator.svelte";
  import SectionGraph from "../src/lib/SectionGraph.svelte";
  import SchemaNetwork from "../src/lib/SchemaNetwork.svelte";
  import { provideWorkspaceContext, type WorkspaceTrail } from "../src/lib/workspaceContext.svelte";

  let { kind }: { kind: "stage" | "atlas" | "section" | "schema" } = $props();
  let stage: BrainStage | undefined = $state();
  const trail: WorkspaceTrail = { section: null, root: null, nodes: [] };
  provideWorkspaceContext({ id: "brain-harness", active: true, trail, setTrail: () => {}, clearTrail: () => {} });
  const schemaNodes = ["Alpha", "Beta", "Gamma"].map((label) => ({ id: label.toLowerCase(), label, group: "Schema" }));

  export function screenPoint(id: string) {
    return stage?.screenPoint(id) ?? null;
  }
  export function worldPoint(id: string) {
    return stage?.worldPoint(id) ?? null;
  }
  export function pointColour(id: string) {
    return stage?.pointColour(id) ?? null;
  }
</script>

{#if kind === "stage"}
  <div class="stage-host"><BrainStage bind:this={stage} active={true} revision={0} /></div>
{:else if kind === "atlas"}
  <GraphNavigator workspace={null} revision={0} onview={() => {}} onopenjob={() => {}} onguide={() => {}} />
{:else if kind === "section"}
  <SectionGraph section="notes" listToggle={false} height="480px" />
{:else}
  <SchemaNetwork title="Schema" nodes={schemaNodes} links={[{ source: "alpha", target: "beta" }]} onSelect={() => {}} />
{/if}

<style>
  .stage-host { position: relative; flex: 1 1 auto; min-height: 0; }
</style>
