import type { GraphLink, GraphNode } from "./graph3d";
import { isExternalViewId, type AnyView, type NavItem, type View } from "./workspace.ts";

/** Raised when the atlas's hidden sections (build `ui.atlas.hidden`, graphPreferences.ts) change. */
export const ATLAS_NODES_EVENT = "kitty:atlas-nodes";
/** dataTransfer type carried by a Sections row dragged onto the graph. */
export const SECTION_DRAG_TYPE = "application/x-kitty-atlas-section";

export type AtlasSection = { label: string; items: NavItem[] };

/**
 * Add-on sections always show: the build's hidden list (`ui.atlas.hidden`) holds only
 * built-in view ids.
 */
export function hideableSection(view: string): boolean {
  return !isExternalViewId(view);
}

/**
 * The atlas Sections list: every enabled section, shown or hidden. Takes the
 * enabled groups (`visibleGroups`) and a view's label and icon (`navItem`).
 * Job search shows only its hub on the atlas, plus any add-on sections.
 */
export function atlasSections(
  groups: readonly { label: string; views: readonly AnyView[] }[],
  lookup: (view: AnyView) => NavItem | undefined,
): AtlasSection[] {
  return groups.map((group) => ({
    label: group.label,
    items: group.views
      .filter((id) => id !== "today" && (group.label !== "Job search" || id === "jobs" || isExternalViewId(id)))
      .flatMap((id) => lookup(id) ?? []),
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
  storedHidden: ReadonlySet<string>,
  relations: readonly [View, View][],
  counts: ReadonlyMap<AnyView, number>,
): { nodes: GraphNode[]; links: GraphLink[] } {
  const hidden = { has: (view: string) => hideableSection(view) && storedHidden.has(view) };
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
