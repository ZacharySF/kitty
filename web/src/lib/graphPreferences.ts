import { DEFAULT_PHYSICS, PHYSICS_LIMITS, webglAvailable, type GraphLayout, type GraphLook, type GraphPhysics } from "./graph3d";

/** The remembered look. "brain" is home-only; every other graph draws it as its own 3D. */
export type AtlasLook = GraphLook | "brain";
export const GRAPH_LOOK_KEY = "kitty.graph.look";
export function graphLook(): AtlasLook {
  try {
    const value = localStorage.getItem(GRAPH_LOOK_KEY);
    return value === "3d" || value === "brain" ? value : "2d";
  } catch { return "2d"; }
}
/** The look a 2D/3D graph renderer draws: the Brain clamps to 3D (2D without WebGL). */
export function rendererLook(look: AtlasLook = graphLook()): GraphLook {
  return look === "brain" ? (webglAvailable() ? "3d" : "2d") : look;
}
export function setGraphLook(look: AtlasLook): void {
  localStorage.setItem(GRAPH_LOOK_KEY, look);
  window.dispatchEvent(new CustomEvent(GRAPH_SETTINGS_EVENT));
}

export const GRAPH_LAYOUT_KEY = "kitty.graph.default-layout";
export const GRAPH_PHYSICS_KEY = "kitty.atlas.physics";
export const GRAPH_SETTINGS_EVENT = "kitty:graph-settings";
const TREE_DEFAULT_VERSION_KEY = "kitty.graph.tree-default.v2";
export const GRAPH_LAYOUTS: { id: GraphLayout; label: string }[] = [
  { id: "tree", label: "Tree" }, { id: "clusters", label: "Clusters" },
  { id: "sphere", label: "Sphere" }, { id: "force", label: "Force" },
];
export function graphLayout(): GraphLayout {
  try {
    // Older installs persisted Sphere automatically. Migrate once, rather than
    // overriding a deliberate layout choice made in the current Settings UI.
    if (localStorage.getItem(TREE_DEFAULT_VERSION_KEY) !== "1") {
      localStorage.setItem(GRAPH_LAYOUT_KEY, "tree");
      localStorage.setItem(TREE_DEFAULT_VERSION_KEY, "1");
      return "tree";
    }
    const value = localStorage.getItem(GRAPH_LAYOUT_KEY);
    return GRAPH_LAYOUTS.some(item => item.id === value) ? value as GraphLayout : "tree";
  } catch { return "tree"; }
}
export function graphPhysics(): GraphPhysics {
  const result = { ...DEFAULT_PHYSICS };
  try {
    const value = JSON.parse(localStorage.getItem(GRAPH_PHYSICS_KEY) || "{}");
    for (const key of Object.keys(PHYSICS_LIMITS) as (keyof GraphPhysics)[]) {
      const limit = PHYSICS_LIMITS[key];
      if (typeof value?.[key] === "number" && Number.isFinite(value[key]))
        result[key] = Math.min(limit.max, Math.max(limit.min, value[key]));
    }
  } catch { /* Invalid local preferences never prevent opening a graph. */ }
  return result;
}
