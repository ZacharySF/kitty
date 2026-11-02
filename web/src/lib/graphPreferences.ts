// Graph and atlas preferences live in the build (`ui.graph`, `ui.atlas`; Q8). Open graphs
// follow GRAPH_SETTINGS_EVENT and ATLAS_NODES_EVENT, raised whenever the build in effect
// changes those values: here, from Settings, or pushed from another window or editor.
import { ATLAS_NODES_EVENT } from "./atlasNodes";
import { buildStore, onBuildChange, setPreferences } from "./build.svelte";
import type { GraphLayout, GraphLook, GraphPhysics } from "./graph3d";
import type { View } from "./workspace";

export const GRAPH_SETTINGS_EVENT = "kitty:graph-settings";
export const GRAPH_LAYOUTS: { id: GraphLayout; label: string }[] = [
  { id: "tree", label: "Tree" }, { id: "clusters", label: "Clusters" },
  { id: "sphere", label: "Sphere" }, { id: "force", label: "Force" },
];

onBuildChange((next, previous) => {
  if (JSON.stringify(next.ui.graph) !== JSON.stringify(previous.ui.graph))
    window.dispatchEvent(new CustomEvent(GRAPH_SETTINGS_EVENT));
  if (JSON.stringify(next.ui.atlas.hidden) !== JSON.stringify(previous.ui.atlas.hidden))
    window.dispatchEvent(new CustomEvent(ATLAS_NODES_EVENT));
});

export function graphLook(): GraphLook {
  return buildStore.build.ui.graph.look;
}
export function setGraphLook(look: GraphLook): Promise<boolean> {
  return setPreferences({ ui: { graph: { look } } });
}
export function graphLayout(): GraphLayout {
  return buildStore.build.ui.graph.layout;
}
export function graphPhysics(): GraphPhysics {
  return { ...buildStore.build.ui.graph.physics };
}
/** Save the default layout and physics together (Settings → Schemas). */
export function setGraphSettings(layout: GraphLayout, physics: GraphPhysics): Promise<boolean> {
  return setPreferences({ ui: { graph: { layout, physics: { ...physics } } } });
}

/**
 * Sections the user removed from the atlas (Q13). Stored as hidden rather than shown so
 * sections added later appear by default. Ids for views whose module is off are kept:
 * turning the module back on must not resurrect them.
 */
export function hiddenSections(): Set<string> {
  return new Set(buildStore.build.ui.atlas.hidden);
}
export function setSectionHidden(view: View, hidden: boolean): void {
  const current = hiddenSections();
  if (current.has(view) === hidden) return;
  if (hidden) current.add(view);
  else current.delete(view);
  void setPreferences({ ui: { atlas: { hidden: [...current] } } });
}
