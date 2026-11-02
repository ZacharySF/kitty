import { DEFAULT_PHYSICS, PHYSICS_LIMITS, type GraphLayout, type GraphLook, type GraphPhysics } from "./graph3d";

/** The remembered look of section graphs and schemas. Home has its own (below). */
export const GRAPH_LOOK_KEY = "kitty.graph.look";
export function graphLook(): GraphLook {
  try {
    return localStorage.getItem(GRAPH_LOOK_KEY) === "3d" ? "3d" : "2d";
  } catch { return "2d"; }
}
export function setGraphLook(look: GraphLook): void {
  localStorage.setItem(GRAPH_LOOK_KEY, look);
  window.dispatchEvent(new CustomEvent(GRAPH_SETTINGS_EVENT));
}

/** Home's look: the Brain (default) or the 2D atlas. */
export type HomeLook = "brain" | "2d";
export const HOME_LOOK_KEY = "kitty.home.look";
export function homeLook(): HomeLook {
  try {
    const saved = localStorage.getItem(HOME_LOOK_KEY);
    if (saved === "brain" || saved === "2d") return saved;
    // Home's choice used to share the graph look: an old "2d" stays 2D, "3d" and "brain" become the Brain.
    const look: HomeLook = localStorage.getItem(GRAPH_LOOK_KEY) === "2d" ? "2d" : "brain";
    localStorage.setItem(HOME_LOOK_KEY, look);
    return look;
  } catch { return "brain"; }
}
export function setHomeLook(look: HomeLook): void {
  localStorage.setItem(HOME_LOOK_KEY, look);
  window.dispatchEvent(new CustomEvent(GRAPH_SETTINGS_EVENT));
}

/** Q13: home's side rail of widgets, collapsed to an edge strip so the Brain widens. */
export const HOME_RAIL_KEY = "kitty.home.rail";
export function homeRailCollapsed(): boolean {
  try { return localStorage.getItem(HOME_RAIL_KEY) === "collapsed"; } catch { return false; }
}
export function setHomeRailCollapsed(collapsed: boolean): void {
  localStorage.setItem(HOME_RAIL_KEY, collapsed ? "collapsed" : "expanded");
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
