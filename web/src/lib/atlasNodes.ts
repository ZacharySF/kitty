import type { GraphLink, GraphNode } from "./graph3d";
import type { navigation as Navigation, View } from "./workspace";

/**
 * Sections the user removed from the atlas (Q13). Stored as hidden rather than
 * shown so sections added later appear by default. Ids for views whose module is
 * off are kept: turning the module back on must not resurrect them.
 */
export const ATLAS_HIDDEN_KEY = "kitty.atlas.hidden";
export const ATLAS_NODES_EVENT = "kitty:atlas-nodes";
/** dataTransfer type carried by a Sections row dragged onto the graph. */
export const SECTION_DRAG_TYPE = "application/x-kitty-atlas-section";

type Section = (typeof Navigation)[number];
export type AtlasSection = { label: string; items: Section[] };

/** Hidden section ids. Unknown ids are harmless: no section ever matches them. */
export function hiddenSections(): Set<string> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(ATLAS_HIDDEN_KEY) || "[]");
    if (!Array.isArray(value)) return new Set();
    return new Set(value.filter((item): item is string => typeof item === "string"));
  } catch {
    // Invalid local preferences never prevent opening the atlas.
    return new Set();
  }
}

export function setSectionHidden(view: View, hidden: boolean): void {
  const current = hiddenSections();
  if (current.has(view) === hidden) return;
  if (hidden) current.add(view);
  else current.delete(view);
  localStorage.setItem(ATLAS_HIDDEN_KEY, JSON.stringify([...current]));
  window.dispatchEvent(new CustomEvent(ATLAS_NODES_EVENT));
}

/**
 * The atlas Sections list: every enabled section, shown or hidden. Takes the
 * enabled groups (`visibleGroups`) and the `navigation` table.
 */
export function atlasSections(
  groups: readonly { label: string; views: readonly View[] }[],
  navigation: readonly Section[],
): AtlasSection[] {
  return groups.map((group) => ({
    label: group.label,
    items: group.views
      .filter((id) => id !== "today" && (group.label !== "Job search" || id === "jobs"))
      .map((id) => navigation.find((item) => item.id === id)!),
  }));
}

const ROOT_VIEWS: Partial<Record<string, View>> = { Study: "study", "Job search": "jobs", Settings: "settings" };

/**
 * Graph for the shown sections. A hidden section drops its node and every link
 * touching it; a group hub without a root view is drawn only while one of its
 * sections is shown.
 */
export function atlasGraph(
  sections: AtlasSection[],
  hidden: ReadonlySet<string>,
  relations: readonly [View, View][],
  counts: ReadonlyMap<View, number>,
): { nodes: GraphNode[]; links: GraphLink[] } {
  const nodes: GraphNode[] = [];
  const links: GraphLink[] = [];
  const hubs = new Map<string, string | null>();
  for (const group of sections) {
    const root = ROOT_VIEWS[group.label];
    const anyShown = group.items.some((item) => !hidden.has(item.id));
    const hub = root ? (hidden.has(root) ? null : `view:${root}`) : anyShown ? `group:${group.label}` : null;
    hubs.set(group.label, hub);
    if (hub)
      nodes.push({
        id: hub,
        label: group.label,
        group: group.label,
        kind: "hub",
        size: 7,
        detail: `${group.items.length} sections`,
        href: root ? `#${root}` : undefined,
      });
  }
  for (const group of sections) {
    const hub = hubs.get(group.label);
    for (const item of group.items) {
      if (item.id === ROOT_VIEWS[group.label] || hidden.has(item.id)) continue;
      const count = counts.get(item.id) ?? 0;
      nodes.push({
        id: `view:${item.id}`,
        label: item.label,
        group: group.label,
        kind: "hub",
        size: 3,
        detail: count ? `${count} linked item${count === 1 ? "" : "s"}` : "",
        href: `#${item.id}`,
      });
      if (hub) links.push({ source: hub, target: `view:${item.id}` });
    }
  }
  const visible = new Set(nodes.map((node) => node.id));
  const existing = new Set(links.map((link) => `${link.source}\0${link.target}`));
  for (const [a, b] of relations)
    if (visible.has(`view:${a}`) && visible.has(`view:${b}`) &&
        !existing.has(`view:${a}\0view:${b}`) && !existing.has(`view:${b}\0view:${a}`))
      links.push({ source: `view:${a}`, target: `view:${b}`, weight: 0.8, label: "related sections" });
  return { nodes, links };
}
