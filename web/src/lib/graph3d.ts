/**
 * Shared switchable Canvas 2D / WebGL 3D network renderer for kitty! graphs.
 * The default is 2D; the 3D library is loaded only when requested.
 * Canvas interactions are pointer-only; every caller must offer a keyboard /
 * screen-reader path (lists + `focus(id)`). Coordinates in the public picking
 * API are viewport CSS pixels, suitable for external drop targets.
 *
 * Node semantics:
 * - `kind === "hub"` marks hub nodes (matte spheres with quiet labels). When no
 *   node is a hub, hubs are picked automatically by weighted degree.
 * - `size` is a visual weight (clamped 0.5–10, radius ∝ √size).
 * - `group` picks the colour (first-seen order over the CSS palette, stable
 *   across `update`) and the cluster in the "clusters" layout.
 *
 * Layouts:
 * - "sphere": hubs on an inner Fibonacci sphere (grouped), leaves fanned on the
 *   outer shell around their strongest-linked hub.
 * - "clusters": force layout pulled toward per-group centres, translucent hulls.
 * - "tree": radial levels outward from the largest hubs (BFS), outward arrows.
 * - "force": free force-directed, arrows + particles on directed links.
 */
import type { ForceGraph3DInstance } from "3d-force-graph";
import type * as Three from "three";
import type { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { harmonize } from "./graphPalette";
import { mountGraph2D } from "./graph2d";

export type GraphNode = {
  id: string;
  label: string;
  group: string;
  kind?: string;
  size?: number;
  detail?: string;
  href?: string;
};
export type GraphLink = {
  source: string;
  target: string;
  label?: string;
  directed?: boolean;
  weight?: number;
};
export type GraphLayout = "sphere" | "force" | "tree" | "clusters";
export type GraphLook = "2d" | "3d";
export type GraphOptions = {
  nodes: GraphNode[];
  links: GraphLink[];
  layout: GraphLayout;
  look?: GraphLook;
  physics?: Partial<GraphPhysics>;
  onSelect?: (n: GraphNode) => void;
  onContextMenu?: (n: GraphNode, event: MouseEvent) => void;
  onHover?: (n: GraphNode | null) => void;
  onDragEnd?: (n: GraphNode, point: { clientX: number; clientY: number }) => void;
  reducedMotion?: boolean;
  active?: boolean;
};
export type GraphPhysics = {
  repulsion: number;
  spring: number;
  damping: number;
};
export interface GraphHandle {
  readonly look: GraphLook;
  setLook(look: GraphLook): Promise<void>;
  nodePoint(id: string): { x: number; y: number } | null;
  nodeAt(clientX: number, clientY: number): GraphNode | null;
  update(data: { nodes: GraphNode[]; links: GraphLink[] }): void;
  setLayout(layout: GraphLayout): void;
  setPhysics(p: Partial<GraphPhysics>): void;
  focus(id: string | null): void;
  setActive(active: boolean): void;
  resize(): void;
  destroy(): void;
}
export type GraphRenderer = Omit<GraphHandle, "look" | "setLook">;

export const DEFAULT_PHYSICS: GraphPhysics = {
  repulsion: 60,
  spring: 0.5,
  damping: 0.35,
};
export const PHYSICS_LIMITS: Record<
  keyof GraphPhysics,
  { min: number; max: number; step: number }
> = {
  repulsion: { min: 0, max: 300, step: 5 },
  spring: { min: 0, max: 1, step: 0.05 },
  damping: { min: 0.05, max: 0.95, step: 0.05 },
};

let webglSupport: boolean | undefined;
export function webglAvailable(): boolean {
  if (webglSupport !== undefined) return webglSupport;
  try {
    const canvas = document.createElement("canvas");
    const gl = (canvas.getContext("webgl2") ??
      canvas.getContext("webgl")) as WebGLRenderingContext | null;
    webglSupport = !!gl;
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
  } catch {
    webglSupport = false;
  }
  return webglSupport;
}

type Vec = { x: number; y: number; z: number };
type SimNode = {
  id: string;
  data: GraphNode;
  hub: boolean;
  radius: number;
  rgb: [number, number, number];
  css: string;
  x?: number;
  y?: number;
  z?: number;
  vx?: number;
  vy?: number;
  vz?: number;
  fx?: number;
  fy?: number;
  fz?: number;
};
type SimLink = {
  source: string | SimNode;
  target: string | SimNode;
  data: GraphLink;
};
type Visual = {
  core?: Three.Mesh<Three.SphereGeometry, Three.MeshLambertMaterial | Three.MeshBasicMaterial>;
  radius: number;
  opacity: number;
  group: Three.Group;
  label?: Three.Sprite;
};
type Force = ((alpha: number) => void) & {
  initialize?: (nodes: SimNode[]) => void;
};

const GOLDEN = Math.PI * (3 - Math.sqrt(5));
const PALETTE_VARS = [
  "--c1",
  "--c2",
  "--c3",
  "--c4",
  "--c5",
  "--c6",
];
const FALLBACK_COLOR = "#b2a6c0";

const unit = (v: Vec): Vec => {
  const length = Math.hypot(v.x, v.y, v.z) || 1;
  return { x: v.x / length, y: v.y / length, z: v.z / length };
};
const dot = (a: Vec, b: Vec) => a.x * b.x + a.y * b.y + a.z * b.z;
const scale = (v: Vec, k: number): Vec => ({
  x: v.x * k,
  y: v.y * k,
  z: v.z * k,
});
const cross = (a: Vec, b: Vec): Vec => ({
  x: a.y * b.z - a.z * b.y,
  y: a.z * b.x - a.x * b.z,
  z: a.x * b.y - a.y * b.x,
});

function fibonacciSphere(count: number): Vec[] {
  return Array.from({ length: count }, (_, index) => {
    const y = 1 - (2 * (index + 0.5)) / count;
    const ring = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = index * GOLDEN;
    return { x: Math.cos(theta) * ring, y, z: Math.sin(theta) * ring };
  });
}

/** Point at `angle` radians from unit vector `centre`, rotated `phi` around it. */
function capPoint(centre: Vec, angle: number, phi: number): Vec {
  const helper =
    Math.abs(centre.y) < 0.9 ? { x: 0, y: 1, z: 0 } : { x: 1, y: 0, z: 0 };
  const u = unit(cross(centre, helper));
  const v = cross(centre, u);
  const s = Math.sin(angle);
  const c = Math.cos(angle);
  return unit({
    x: centre.x * c + s * (Math.cos(phi) * u.x + Math.sin(phi) * v.x),
    y: centre.y * c + s * (Math.cos(phi) * u.y + Math.sin(phi) * v.y),
    z: centre.z * c + s * (Math.cos(phi) * u.z + Math.sin(phi) * v.z),
  });
}

/** Angular radius of a spherical cap covering `share` of the sphere. */
const capAngle = (share: number) =>
  Math.acos(Math.max(-1, Math.min(1, 1 - 2 * share)));

function hash01(text: string): number {
  let value = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    value ^= text.charCodeAt(index);
    value = Math.imul(value, 16777619);
  }
  return (value >>> 0) / 4294967295;
}

const endpoint = (value: string | SimNode) =>
  typeof value === "string" ? value : value.id;

class Topology {
  readonly adjacency = new Map<string, Map<string, number>>();
  readonly hubs: SimNode[];
  constructor(
    readonly nodes: SimNode[],
    readonly links: SimLink[],
  ) {
    for (const node of nodes) this.adjacency.set(node.id, new Map());
    for (const link of links) {
      const a = endpoint(link.source);
      const b = endpoint(link.target);
      if (a === b) continue;
      const weight = Math.max(0.1, link.data.weight ?? 1);
      const left = this.adjacency.get(a)!;
      const right = this.adjacency.get(b)!;
      left.set(b, (left.get(b) ?? 0) + weight);
      right.set(a, (right.get(a) ?? 0) + weight);
    }
    const explicit = nodes.filter((node) => node.data.kind === "hub");
    this.hubs = explicit.length ? explicit : this.autoHubs();
    const hubIds = new Set(this.hubs.map((node) => node.id));
    for (const node of nodes) node.hub = hubIds.has(node.id);
  }
  degree(id: string) {
    let total = 0;
    for (const weight of this.adjacency.get(id)?.values() ?? [])
      total += weight;
    return total;
  }
  private autoHubs() {
    const ranked = this.nodes
      .map((node) => ({ node, degree: this.degree(node.id) }))
      .filter((item) => item.degree >= 3)
      .sort(
        (a, b) => b.degree - a.degree || a.node.id.localeCompare(b.node.id),
      );
    const limit = Math.max(1, Math.round(Math.sqrt(this.nodes.length) / 1.5));
    return ranked.slice(0, limit).map((item) => item.node);
  }
  /** Hub that owns each non-hub node: strongest linked hub, else nearest by BFS. */
  owners(): Map<string, string | null> {
    const hubIds = new Set(this.hubs.map((node) => node.id));
    const owners = new Map<string, string | null>();
    for (const node of this.nodes) {
      if (hubIds.has(node.id)) continue;
      let best: string | null = null;
      let bestWeight = -1;
      for (const [other, weight] of this.adjacency.get(node.id)!) {
        if (hubIds.has(other) && weight > bestWeight) {
          best = other;
          bestWeight = weight;
        }
      }
      if (!best) best = this.nearestHub(node.id, hubIds);
      if (!best)
        best =
          this.hubs.find((hub) => hub.data.group === node.data.group)?.id ??
          null;
      owners.set(node.id, best);
    }
    return owners;
  }
  private nearestHub(start: string, hubIds: Set<string>) {
    const seen = new Set([start]);
    let frontier = [start];
    for (let depth = 0; depth < 6 && frontier.length; depth += 1) {
      const next: string[] = [];
      for (const id of frontier) {
        for (const other of [...this.adjacency.get(id)!.keys()].sort()) {
          if (hubIds.has(other)) return other;
          if (!seen.has(other)) {
            seen.add(other);
            next.push(other);
          }
        }
      }
      frontier = next;
    }
    return null;
  }
  /** BFS levels from the largest hubs (or busiest node per component). */
  levels(): {
    depth: Map<string, number>;
    parent: Map<string, string>;
    roots: number;
  } {
    const depth = new Map<string, number>();
    const parent = new Map<string, string>();
    const size = (node: SimNode) => node.data.size ?? 1;
    const maxHub = Math.max(...this.hubs.map(size), -Infinity);
    let queue = this.hubs
      .filter((node) => size(node) === maxHub)
      .map((n) => n.id);
    let roots = queue.length;
    const walk = (start: string[]) => {
      for (const id of start) depth.set(id, 0);
      let frontier = start;
      while (frontier.length) {
        const next: string[] = [];
        for (const id of frontier) {
          for (const other of [...this.adjacency.get(id)!.keys()].sort()) {
            if (depth.has(other)) continue;
            depth.set(other, depth.get(id)! + 1);
            parent.set(other, id);
            next.push(other);
          }
        }
        frontier = next;
      }
    };
    walk(queue);
    const rest = this.nodes
      .filter((node) => !depth.has(node.id))
      .sort(
        (a, b) =>
          this.degree(b.id) - this.degree(a.id) || a.id.localeCompare(b.id),
      );
    for (const node of rest) {
      if (depth.has(node.id)) continue;
      queue = [node.id];
      roots += 1;
      walk(queue);
    }
    return { depth, parent, roots };
  }
}

/** Reference-1 layout: hubs on an inner sphere, leaf fans on the outer shell. */
function sphereTargets(
  topology: Topology,
  groupOrder: string[],
): Map<string, Vec> {
  const targets = new Map<string, Vec>();
  const hubs = topology.hubs;
  const owners = topology.owners();
  const leafCount = owners.size;
  const inner = 40 + 13 * Math.sqrt(Math.max(1, hubs.length));
  const outer = inner * 1.3 + 2 * Math.sqrt(leafCount);

  const groups = [...new Set(hubs.map((hub) => hub.data.group))].sort(
    (a, b) => groupOrder.indexOf(a) - groupOrder.indexOf(b),
  );
  const groupDirections = fibonacciSphere(groups.length);
  type Cap = {
    id: string | null;
    home: Vec;
    dir: Vec;
    angle: number;
    top: boolean;
  };
  const caps: Cap[] = [];
  const fans = new Map<string | null, SimNode[]>();
  for (const node of topology.nodes) {
    if (!owners.has(node.id)) continue;
    const owner = owners.get(node.id)!;
    const fan = fans.get(owner);
    if (fan) fan.push(node);
    else fans.set(owner, [node]);
  }
  // Each fan's cap holds its leaves at ~0.09 rad spacing, capped so all fans
  // together cover at most ~55% of the shell; fans stay distinct clusters.
  const share = (count: number) =>
    leafCount
      ? Math.min(
          (0.55 * count) / leafCount,
          (count * 0.09 ** 2) / (4 * Math.PI),
        )
      : 0;
  groups.forEach((group, groupIndex) => {
    const members = hubs
      .filter((hub) => hub.data.group === group)
      .sort(
        (a, b) =>
          (b.data.size ?? 1) - (a.data.size ?? 1) ||
          a.data.label.localeCompare(b.data.label),
      );
    const spread =
      groups.length === 1
        ? Math.PI * 0.9
        : Math.max(0.25, capAngle((0.85 * members.length) / hubs.length));
    const largest = members[0]?.data.size ?? 1;
    members.forEach((hub, index) => {
      const angle =
        members.length === 1 ? 0 : spread * Math.sqrt(index / members.length);
      const home = capPoint(groupDirections[groupIndex], angle, index * GOLDEN);
      caps.push({
        id: hub.id,
        home,
        dir: home,
        angle: Math.max(0.12, capAngle(share(fans.get(hub.id)?.length ?? 0))),
        top:
          members.length > 1 && (hub.data.size ?? 1) === largest && index === 0,
      });
    });
  });
  if (fans.has(null)) {
    const home = { x: 0, y: -1, z: 0 };
    caps.push({
      id: null,
      home,
      dir: home,
      angle: capAngle(share(fans.get(null)!.length)),
      top: false,
    });
  }
  // Relax cap centres on the sphere so fans do not pile up, staying near home.
  for (let iteration = 0; iteration < 90; iteration += 1) {
    for (let i = 0; i < caps.length; i += 1) {
      for (let j = i + 1; j < caps.length; j += 1) {
        const a = caps[i];
        const b = caps[j];
        const between = Math.acos(Math.max(-1, Math.min(1, dot(a.dir, b.dir))));
        const overlap = a.angle + b.angle - between;
        if (overlap <= 0) continue;
        const push = overlap * 0.2;
        const total = a.angle + b.angle || 1;
        const away = (from: Vec, other: Vec, amount: number) => {
          let tangent = {
            x: from.x - other.x * dot(from, other),
            y: from.y - other.y * dot(from, other),
            z: from.z - other.z * dot(from, other),
          };
          if (Math.hypot(tangent.x, tangent.y, tangent.z) < 1e-6)
            tangent = capPoint(
              other,
              Math.PI / 2,
              hash01(String(i * 31 + j)) * 6.28,
            );
          tangent = unit(tangent);
          return unit({
            x: from.x * Math.cos(amount) + tangent.x * Math.sin(amount),
            y: from.y * Math.cos(amount) + tangent.y * Math.sin(amount),
            z: from.z * Math.cos(amount) + tangent.z * Math.sin(amount),
          });
        };
        a.dir = away(a.dir, b.dir, (push * b.angle) / total);
        b.dir = away(b.dir, a.dir, (push * a.angle) / total);
      }
    }
    for (const cap of caps)
      cap.dir = unit({
        x: cap.dir.x * 0.97 + cap.home.x * 0.03,
        y: cap.dir.y * 0.97 + cap.home.y * 0.03,
        z: cap.dir.z * 0.97 + cap.home.z * 0.03,
      });
  }
  for (const cap of caps) {
    if (cap.id)
      targets.set(cap.id, scale(cap.dir, inner * (cap.top ? 0.4 : 1)));
    const leaves = (fans.get(cap.id) ?? []).sort(
      (a, b) =>
        (a.data.kind ?? "").localeCompare(b.data.kind ?? "") ||
        a.data.label.localeCompare(b.data.label) ||
        a.id.localeCompare(b.id),
    );
    const seed = hash01(cap.id ?? "orphans") * Math.PI * 2;
    leaves.forEach((leaf, index) => {
      const angle = cap.angle * Math.sqrt((index + 0.5) / leaves.length);
      const point = capPoint(cap.dir, angle, seed + index * GOLDEN);
      targets.set(
        leaf.id,
        scale(point, outer * (0.97 + 0.06 * hash01(leaf.id))),
      );
    });
  }
  return targets;
}

function clusterCentres(groups: string[], nodeCount: number): Map<string, Vec> {
  const radius =
    40 + 12 * Math.sqrt(nodeCount) * Math.min(1, groups.length / 3);
  const directions = fibonacciSphere(groups.length);
  return new Map(
    groups.map((group, index) => [
      group,
      groups.length === 1
        ? { x: 0, y: 0, z: 0 }
        : scale(directions[index], radius),
    ]),
  );
}

function resolveColor(value: string, probe: CanvasRenderingContext2D): string {
  probe.fillStyle = "#000000";
  probe.fillStyle = value || FALLBACK_COLOR;
  const normalised = String(probe.fillStyle);
  if (normalised.startsWith("#")) return normalised;
  probe.fillRect(0, 0, 1, 1);
  const [r, g, b] = probe.getImageData(0, 0, 1, 1).data;
  return `#${[r, g, b].map((part) => part.toString(16).padStart(2, "0")).join("")}`;
}

function hexRgb(hex: string): [number, number, number] {
  const value = Number.parseInt(hex.slice(1, 7), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

const rgba = ([r, g, b]: [number, number, number], alpha: number) =>
  `rgba(${r},${g},${b},${alpha.toFixed(3)})`;

/** Stable handle: switching renderers retains current data, focus and preferences. */
export async function mountGraph(el: HTMLElement, opts: GraphOptions): Promise<GraphHandle> {
  let look = opts.look ?? "2d";
  let data = { nodes: opts.nodes, links: opts.links };
  let layout = opts.layout;
  let physics = { ...opts.physics };
  let active = opts.active ?? true;
  let focused: string | null = null;
  let destroyed = false;
  let renderer: GraphRenderer;
  let switching = Promise.resolve();
  const create = async (next: GraphLook) => {
    const mount = next === "2d" ? mountGraph2D : mountGraph3D;
    const input = { nodes: data.nodes, links: data.links, layout, physics, active };
    const result = await mount(el, {
      ...opts, ...input,
      onSelect(node) { focused = node.id; opts.onSelect?.(node); },
    });
    if (destroyed) result.destroy();
    else {
      renderer = result;
      if (data.nodes !== input.nodes || data.links !== input.links) result.update(data);
      if (layout !== input.layout) result.setLayout(layout);
      if (physics !== input.physics) result.setPhysics(physics);
      if (active !== input.active) result.setActive(active);
      if (focused) result.focus(focused);
    }
  };
  await create(look);
  return {
    get look() { return look; },
    setLook(next) {
      switching = switching.catch(() => {}).then(async () => {
        if (destroyed || next === look) return;
        if (next === "3d" && !webglAvailable())
          throw new Error("WebGL is unavailable; choose the 2D graph instead.");
        // The existing 3D engine stays lazy: a 2D graph must not initialize WebGL.
        // Load that optional backend before releasing the working 2D surface.
        if (next === "3d") await import("3d-force-graph");
        if (destroyed) return;
        renderer.destroy();
        await create(next);
        look = next;
      });
      return switching;
    },
    update(next) {
      data = next;
      if (focused && !data.nodes.some(node => node.id === focused)) focused = null;
      if (!destroyed) renderer.update(data);
    },
    setLayout(next) { layout = next; if (!destroyed) renderer.setLayout(next); },
    setPhysics(next) { physics = { ...physics, ...next }; if (!destroyed) renderer.setPhysics(next); },
    setActive(next) { active = next; if (!destroyed) renderer.setActive(next); },
    focus(id) { focused = id; if (!destroyed) renderer.focus(id); },
    nodePoint(id) { return destroyed ? null : renderer.nodePoint(id); },
    nodeAt(x, y) { return destroyed ? null : renderer.nodeAt(x, y); },
    resize() { if (!destroyed) renderer.resize(); },
    destroy() { destroyed = true; renderer.destroy(); },
  };
}

async function mountGraph3D(el: HTMLElement, opts: GraphOptions): Promise<GraphRenderer> {
  if (!webglAvailable())
    throw new Error(
      "WebGL is unavailable in this browser, so the 3D graph cannot be drawn.",
    );
  const [{ default: ForceGraph3D }, T, { ConvexGeometry }] = await Promise.all([
    import("3d-force-graph"),
    import("three"),
    import("three/examples/jsm/geometries/ConvexGeometry.js"),
    document.fonts?.ready,
  ]);
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  let reduced = motionQuery.matches || !!opts.reducedMotion;
  let layout: GraphLayout = opts.layout;
  const physics: GraphPhysics = { ...DEFAULT_PHYSICS, ...opts.physics };

  const styles = getComputedStyle(el);
  const probeCanvas = document.createElement("canvas");
  probeCanvas.width = probeCanvas.height = 1;
  const probe = probeCanvas.getContext("2d", { willReadFrequently: true })!;
  const cssVar = (name: string) => styles.getPropertyValue(name).trim();
  const palette = harmonize(PALETTE_VARS.map((name) => cssVar(name))
    .filter(Boolean)
    .map((value) => resolveColor(value, probe)));
  if (!palette.length) palette.push(FALLBACK_COLOR);
  let ink = resolveColor(cssVar("--ink") || "#e7dfef", probe);
  let mutedRgb = hexRgb(
    resolveColor(cssVar("--muted") || FALLBACK_COLOR, probe),
  );
  let chrome = hexRgb(resolveColor(cssVar("--chrome") || "#0e0c14", probe));
  const orphan: SimNode = {
    id: "",
    data: { id: "", label: "", group: "" },
    hub: false,
    radius: 1,
    rgb: mutedRgb,
    css: FALLBACK_COLOR,
  };
  const fontFamily = cssVar("--br-font-ui") || "ui-monospace, monospace";
  const groupOrder: string[] = [];
  const colorOf = (group: string) => {
    if (!groupOrder.includes(group)) groupOrder.push(group);
    return palette[groupOrder.indexOf(group) % palette.length];
  };

  // Shared GPU resources; per-node materials are disposed by the library.
  const hubGeometry = new T.SphereGeometry(1, 24, 18);
  const leafGeometry = new T.SphereGeometry(1, 8, 6);
  const hitMaterial = new T.MeshBasicMaterial({
    transparent: true,
    opacity: 0,
    depthWrite: false,
    colorWrite: false,
  });

  const visuals = new Map<string, Visual>();
  let nodes: SimNode[] = [];
  let links: SimLink[] = [];
  let byId = new Map<string, SimNode>();
  let topology = new Topology([], []);
  // Posting labels remain selective; every other node keeps its name, including during focus.
  let labelledIds = new Set<string>();
  let sphere = new Map<string, Vec>();
  let tree = topology.levels();
  let centres = new Map<string, Vec>();
  let hoverId: string | null = null;
  let selectedId: string | null = null;
  let destroyed = false;
  let paneActive = opts.active ?? true;
  let intersecting = true;
  let rendering = false;
  let pendingFocus: string | null | undefined;
  let fitAfterSettle = false;
  let resumeTimer: ReturnType<typeof setTimeout> | undefined;
  let hullTick = 0;
  // cameraPosition() is ignored until the library's first render; fit on first tick.
  let initialFit = true;


  // Keep all data and individual picking identities, but batch dense geometry.
  // InstancedMesh reduces leaf draw calls to one; LineSegments does the same
  // for edges. The force engine and existing OrbitControls remain unchanged.
  const batches = new T.Group();
  let leafBatch: Three.InstancedMesh<Three.SphereGeometry, Three.MeshBasicMaterial> | null = null;
  let edgeBatch: Three.LineSegments<Three.BufferGeometry, Three.LineBasicMaterial> | null = null;
  const leafIndices = new Map<string, number>();
  const transform = new T.Object3D();
  const batchColor = new T.Color();
  const hitCentre = new T.Vector3();
  const hitPoint = new T.Vector3();
  let dense = false;

  let labelFrame = 0;
  const projectedLabel = new T.Vector3();

  function scheduleLabels() {
    if (labelFrame || destroyed || !rendering) return;
    labelFrame = requestAnimationFrame(() => {
      labelFrame = 0;
      if (destroyed || !rendering) return;
      const camera = graph.camera() as Three.PerspectiveCamera;
      const width = el.clientWidth || 1;
      const height = el.clientHeight || 1;
      const occupied: { left: number; top: number; right: number; bottom: number }[] = [];
      const ordered = [...visuals.entries()].filter(([, visual]) => visual.label);
      ordered.sort(([a], [b]) => Number(b === active()) - Number(a === active()) || topology.degree(b) - topology.degree(a));
      for (const [id, visual] of ordered) {
        const sprite = visual.label!;
        sprite.getWorldPosition(projectedLabel);
        const distance = camera.position.distanceTo(projectedLabel);
        const worldPerPixel = 2 * distance * Math.tan(camera.fov * Math.PI / 360) / height;
        const image = sprite.material.map!.image as HTMLCanvasElement;
        const pixelsHigh = id === active() ? 20 : 17;
        const pixelsWide = pixelsHigh * image.width / image.height;
        sprite.scale.set(pixelsWide * worldPerPixel, pixelsHigh * worldPerPixel, 1);
        projectedLabel.project(camera);
        const x = (projectedLabel.x + 1) * width / 2;
        const y = (1 - projectedLabel.y) * height / 2;
        const box = { left: x - pixelsWide / 2 - 4, right: x + pixelsWide / 2 + 4, top: y - pixelsHigh / 2 - 3, bottom: y + pixelsHigh / 2 + 3 };
        const persistent = byId.get(id)?.data.kind !== "job";
        const visible = projectedLabel.z >= -1 && projectedLabel.z <= 1 &&
          box.right > 0 && box.left < width && box.bottom > 0 && box.top < height &&
          (persistent || !occupied.some(other => box.left < other.right && box.right > other.left && box.top < other.bottom && box.bottom > other.top));
        sprite.visible = visible;
        if (visible) occupied.push(box);
      }
    });
  }
  function clearBatches() {
    if (leafBatch) {
      batches.remove(leafBatch);
      leafBatch.dispose();
      leafBatch.material.dispose();
      leafBatch = null;
    }
    if (edgeBatch) {
      batches.remove(edgeBatch);
      edgeBatch.geometry.dispose();
      edgeBatch.material.dispose();
      edgeBatch = null;
    }
    leafIndices.clear();
  }
  function syncBatches() {
    if (!dense) return;
    if (leafBatch) {
      const focus = active();
      for (const [id, index] of leafIndices) {
        const node = byId.get(id)!;
        transform.position.set(node.x ?? 0, node.y ?? 0, node.z ?? 0);
        transform.scale.setScalar(node.radius * (focus === id ? 1.2 : 1));
        transform.updateMatrix();
        leafBatch.setMatrixAt(index, transform.matrix);
      }
      leafBatch.instanceMatrix.needsUpdate = true;
    }
    if (edgeBatch) {
      const positions = edgeBatch.geometry.getAttribute("position") as Three.BufferAttribute;
      links.forEach((link, index) => {
        const source = nodeOf(link.source);
        const target = nodeOf(link.target);
        positions.setXYZ(index * 2, source.x ?? 0, source.y ?? 0, source.z ?? 0);
        positions.setXYZ(index * 2 + 1, target.x ?? 0, target.y ?? 0, target.z ?? 0);
      });
      positions.needsUpdate = true;
    }
  }
  function colorBatches() {
    const focus = active();
    if (leafBatch) {
      for (const [id, index] of leafIndices) {
        const node = byId.get(id)!;
        const nearby = !focus || focus === id || topology.adjacency.get(focus)?.has(id);
        leafBatch.setColorAt(index, batchColor.set(node.css).multiplyScalar(nearby ? 1 : 0.25));
      }
      if (leafBatch.instanceColor) leafBatch.instanceColor.needsUpdate = true;
    }
    if (edgeBatch) {
      const colors = edgeBatch.geometry.getAttribute("color") as Three.BufferAttribute;
      links.forEach((link, index) => {
        const nearby = !focus || touchesActive(link);
        batchColor.set(nodeOf(link.source).css).multiplyScalar(nearby ? 0.6 : 0.12);
        colors.setXYZ(index * 2, batchColor.r, batchColor.g, batchColor.b);
        colors.setXYZ(index * 2 + 1, batchColor.r, batchColor.g, batchColor.b);
      });
      colors.needsUpdate = true;
    }
  }
  function buildBatches() {
    clearBatches();
    if (!dense) return;
    const leaves = nodes.filter(node => !node.hub);
    if (leaves.length) {
      leafBatch = new T.InstancedMesh(leafGeometry, new T.MeshBasicMaterial(), leaves.length);
      leafBatch.instanceMatrix.setUsage(T.DynamicDrawUsage);
      leafBatch.frustumCulled = false;
      // Picking is handled by each node's proxy, never by this shared mesh.
      leafBatch.raycast = () => undefined;
      leaves.forEach((node, index) => leafIndices.set(node.id, index));
      batches.add(leafBatch);
    }
    if (links.length) {
      const geometry = new T.BufferGeometry();
      geometry.setAttribute("position", new T.BufferAttribute(new Float32Array(links.length * 6), 3).setUsage(T.DynamicDrawUsage));
      geometry.setAttribute("color", new T.BufferAttribute(new Float32Array(links.length * 6), 3));
      edgeBatch = new T.LineSegments(geometry, new T.LineBasicMaterial({
        vertexColors: true, transparent: true,
        opacity: Math.min(0.22, 2 / Math.sqrt(Math.max(1, links.length))), depthWrite: false,
      }));
      edgeBatch.frustumCulled = false;
      edgeBatch.raycast = () => undefined;
      batches.add(edgeBatch);
    }
    syncBatches();
    colorBatches();
  }
  function labelSprite(node: SimNode) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const fontSize = 24 * dpr;
    const text =
      node.data.label.length > 30
        ? `${node.data.label.slice(0, 29)}…`
        : node.data.label;
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d")!;
    const font = `500 ${fontSize}px ${fontFamily}`;
    context.font = font;
    const padX = 14 * dpr;
    const width = Math.ceil(context.measureText(text).width + padX * 2);
    const height = Math.ceil(fontSize + 18 * dpr);
    canvas.width = width;
    canvas.height = height;
    context.fillStyle = rgba(chrome, node.hub ? 0.94 : 0.88);
    context.strokeStyle = node.hub ? rgba(node.rgb, 0.28) : node.css;
    context.lineWidth = Math.max(1, dpr);
    if (node.hub) {
      context.beginPath();
      context.roundRect(0.5 * dpr, 0.5 * dpr, width - dpr, height - dpr, 5 * dpr);
      context.fill();
      context.stroke();
    } else {
      context.fillRect(0, 0, width, height);
      context.strokeRect(0.5 * dpr, 0.5 * dpr, width - dpr, height - dpr);
      const tick = 7 * dpr;
      context.lineWidth = 2 * dpr;
      context.beginPath();
      context.moveTo(dpr, tick);
      context.lineTo(dpr, dpr);
      context.lineTo(tick, dpr);
      context.moveTo(width - tick, height - dpr);
      context.lineTo(width - dpr, height - dpr);
      context.lineTo(width - dpr, height - tick);
      context.stroke();
    }
    context.font = font;
    context.fillStyle = ink;
    context.textBaseline = "middle";
    context.fillText(text, padX, height / 2 + dpr);
    const texture = new T.CanvasTexture(canvas);
    texture.colorSpace = T.SRGBColorSpace;
    texture.minFilter = T.LinearFilter;
    const material = new T.SpriteMaterial({
      map: texture,
      transparent: true,
      depthTest: false,
      depthWrite: false,
    });
    const sprite = new T.Sprite(material);
    const worldHeight = 3.6 + 1.1 * Math.sqrt(node.data.size ?? 1);
    sprite.scale.set((worldHeight * width) / height, worldHeight, 1);
    sprite.position.set(0, node.radius + worldHeight * 0.8, 0);
    sprite.renderOrder = 10;
    sprite.raycast = () => undefined;
    return { sprite, material };
  }

  function buildNode(node: SimNode): Three.Object3D {
    const group = new T.Group();
    const opacity = node.hub ? 1 : 0.92;
    let core: Visual["core"];
    if (dense && !node.hub) {
      // Analytic sphere picking avoids an invisible extra draw call per leaf.
      // Returning this proxy preserves 3d-force-graph's node ID/hover callbacks.
      group.raycast = (raycaster, intersections) => {
        group.getWorldPosition(hitCentre);
        const radius = Math.max(node.radius * 1.6, 2.6);
        if (raycaster.ray.distanceSqToPoint(hitCentre) > radius * radius) return;
        raycaster.ray.closestPointToPoint(hitCentre, hitPoint);
        const distance = raycaster.ray.origin.distanceTo(hitPoint);
        if (distance < raycaster.near || distance > raycaster.far) return;
        intersections.push({ distance, point: hitPoint.clone(), object: group });
      };
    } else {
      const material = node.hub
        ? new T.MeshLambertMaterial({
            color: node.css,
            transparent: true, opacity,
          })
        : new T.MeshBasicMaterial({ color: node.css, transparent: true, opacity });
      core = new T.Mesh(node.hub ? hubGeometry : leafGeometry, material);
      const hit = new T.Mesh(leafGeometry, hitMaterial);
      hit.scale.setScalar(Math.max(node.radius * 1.6, 2.6));
      group.add(hit);
    }
    const focus = active();
    const nearby = !focus || focus === node.id || topology.adjacency.get(focus)?.has(node.id);
    if (core) {
      core.material.opacity = opacity * (nearby ? 1 : 0.24);
      core.scale.setScalar(node.radius * (focus === node.id ? (node.hub ? 1.08 : 1.2) : 1));
      group.add(core);
    }
    const visual: Visual = { core, radius: node.radius, opacity, group };
    if (node.data.kind !== "job" || focus === node.id || (!focus && labelledIds.has(node.id))) {
      visual.label = labelSprite(node).sprite;
      group.add(visual.label);
    }
    visuals.set(node.id, visual);
    scheduleLabels();
    return group;
  }

  function tooltip(node: SimNode): HTMLElement {
    const box = document.createElement("div");
    box.className = "graph3d-tip";
    const title = document.createElement("strong");
    title.textContent = node.data.label;
    box.append(title);
    const meta = [
      node.data.kind === "hub" ? "hub" : node.data.kind,
      node.data.group,
    ]
      .filter(Boolean)
      .join(" · ");
    if (meta) {
      const line = document.createElement("small");
      line.textContent = meta;
      box.append(line);
    }
    if (node.data.detail) {
      const detail = document.createElement("span");
      detail.textContent = node.data.detail;
      box.append(detail);
    }
    return box;
  }

  const active = () => hoverId ?? selectedId;
  const isTreeEdge = (link: SimLink) =>
    tree.parent.get(endpoint(link.target)) === endpoint(link.source);
  const touchesActive = (link: SimLink) => {
    const id = active();
    return (
      !!id && (endpoint(link.source) === id || endpoint(link.target) === id)
    );
  };
  const showsArrow = (link: SimLink) =>
    !!link.data.directed || (layout === "tree" && isTreeEdge(link));
  /** Links may still hold the previous data's node objects until the library re-digests. */
  const nodeOf = (value: string | SimNode): SimNode =>
    typeof value === "object" ? value : (byId.get(value) ?? orphan);
  const sameGroup = (link: SimLink) =>
    nodeOf(link.source).data.group === nodeOf(link.target).data.group;

  function linkColor(link: SimLink): string {
    const source = nodeOf(link.source);
    const target = nodeOf(link.target);
    const weight = Math.min(4, Math.max(0.2, link.data.weight ?? 1));
    const focus = active();
    if (focus) {
      if (touchesActive(link))
        return rgba(byId.get(focus)?.rgb ?? mutedRgb, source.hub || target.hub ? 0.68 : 0.85);
      return rgba(mutedRgb, 0.025);
    }
    if (layout === "clusters" && source.data.group !== target.data.group)
      return rgba(source.rgb, 0.4);
    if (source.hub && target.hub) return rgba(source.rgb, 0.34);
    return rgba(source.rgb, 0.16 + 0.05 * weight);
  }

  function paint() {
    if (destroyed) return;
    const focus = active();
    const neighbors = focus ? topology.adjacency.get(focus) : undefined;
    for (const [id, visual] of visuals) {
      const factor = !focus || id === focus || neighbors?.has(id) ? 1 : 0.24;
      if (visual.core) {
        visual.core.material.opacity = visual.opacity * factor;
        visual.core.scale.setScalar(visual.radius * (id === focus ? (byId.get(id)?.hub ? 1.08 : 1.2) : 1));
      }
      const showLabel = byId.get(id)?.data.kind !== "job" || id === focus || (!focus && labelledIds.has(id));
      if (showLabel && !visual.label) {
        const node = byId.get(id);
        if (node) {
          visual.label = labelSprite(node).sprite;
          visual.group.add(visual.label);
        }
      } else if (!showLabel && visual.label) {
        visual.group.remove(visual.label);
        visual.label.material.map?.dispose();
        visual.label.material.dispose();
        visual.label = undefined;
      }
    }
    colorBatches();
    syncBatches();
    scheduleLabels();
    // Dense edge colors live in one GPU buffer, not library materials. Avoid a
    // graph digest (and simulation restart) on every pointer hover.
    if (dense) return;
    graph
      .linkColor((link) => linkColor(link))
      .linkDirectionalArrowLength((link) => (!dense && showsArrow(link) ? 3.5 : 0))
      .linkDirectionalParticles((link) => {
        if (dense || reduced || nodeOf(link.source).hub || nodeOf(link.target).hub ||
            !touchesActive(link) || !showsArrow(link)) return 0;
        return 1;
      })
      .linkCurvature((link) => {
        if (endpoint(link.source) === endpoint(link.target)) return 0.8;
        if (layout !== "clusters") return 0;
        return sameGroup(link) ? 0 : 0.25;
      });
  }

  // --- Forces -------------------------------------------------------------
  function targetForce(
    targetOf: (node: SimNode) => Vec | undefined,
    strength: number,
  ): Force {
    let current: SimNode[] = [];
    const force: Force = (alpha) => {
      const k = strength * alpha;
      for (const node of current) {
        const target = targetOf(node);
        if (!target || node.x === undefined) continue;
        node.vx = (node.vx ?? 0) + (target.x - node.x) * k;
        node.vy = (node.vy ?? 0) + (target.y - node.y!) * k;
        node.vz = (node.vz ?? 0) + (target.z - node.z!) * k;
      }
    };
    force.initialize = (list) => (current = list);
    return force;
  }
  function radialForce(
    radiusOf: (node: SimNode) => number,
    strength: number,
  ): Force {
    let current: SimNode[] = [];
    const force: Force = (alpha) => {
      for (const node of current) {
        if (node.x === undefined) continue;
        const distance = Math.hypot(node.x, node.y!, node.z!) || 1e-3;
        const k = ((radiusOf(node) - distance) / distance) * strength * alpha;
        node.vx = (node.vx ?? 0) + node.x * k;
        node.vy = (node.vy ?? 0) + node.y! * k;
        node.vz = (node.vz ?? 0) + node.z! * k;
      }
    };
    force.initialize = (list) => (current = list);
    return force;
  }
  let levelGap = 55;
  const treeRadius = (node: SimNode) => {
    const depth = tree.depth.get(node.id) ?? 0;
    return depth === 0
      ? tree.roots > 1
        ? levelGap * 0.7
        : 0
      : depth * levelGap;
  };

  function applyForces() {
    const chargeScale = { sphere: 0.06, clusters: 0.6, tree: 0.9, force: 1 }[
      layout
    ];
    const charge = graph.d3Force("charge");
    charge?.strength?.(
      (node: SimNode) =>
        -physics.repulsion * chargeScale * (node.hub ? 1.8 : 1),
    );
    charge?.distanceMax?.(layout === "sphere" ? 70 : dense ? levelGap * 2 : Infinity);
    const link = graph.d3Force("link");
    link
      ?.distance?.((item: SimLink) => {
        const weight = Math.max(0.2, item.data.weight ?? 1);
        if (layout === "tree") return levelGap * 0.9;
        if (layout === "clusters") return sameGroup(item) ? 18 : 70;
        return 30 / Math.sqrt(weight);
      })
      .strength?.((item: SimLink) => {
        const weight = Math.min(3, Math.max(0.2, item.data.weight ?? 1));
        const base = physics.spring * (0.35 + 0.25 * weight);
        if (layout === "sphere") return base * 0.04;
        if (layout === "clusters") return sameGroup(item) ? base : base * 0.08;
        return base;
      });
    graph.d3Force(
      "layout",
      layout === "sphere"
        ? targetForce(
            (node) => sphere.get(node.id),
            0.12 + 0.5 * physics.spring,
          )
        : layout === "clusters"
          ? targetForce(
              (node) => centres.get(node.data.group),
              0.04 + 0.12 * physics.spring,
            )
          : layout === "tree"
            ? radialForce(treeRadius, 0.25 + 0.5 * physics.spring)
            : null,
    );
    graph.d3VelocityDecay(physics.damping);
  }

  // --- Guides: group hulls ("clusters") and shell rings ("sphere") ----------
  const hulls = new T.Group();
  hulls.renderOrder = -1;
  function clearHulls() {
    for (const child of [...hulls.children]) {
      hulls.remove(child);
      const mesh = child as Three.Mesh | Three.Line;
      mesh.geometry.dispose();
      (mesh.material as Three.Material).dispose();
    }
  }
  function drawShell() {
    let reach = 0;
    for (const target of sphere.values())
      reach = Math.max(reach, Math.hypot(target.x, target.y, target.z));
    if (!reach) return;
    const ring = Array.from({ length: 129 }, (_, index) => {
      const angle = (index / 128) * Math.PI * 2;
      return new T.Vector3(Math.cos(angle) * reach, 0, Math.sin(angle) * reach);
    });
    for (const [index, [x, z]] of [[0.28, 0.16]].entries()) {
      const color = new T.Color(palette[index % palette.length]);
      const line = new T.Line(
        new T.BufferGeometry().setFromPoints(ring),
        new T.LineBasicMaterial({
          color,
          transparent: true,
          opacity: 0.07,
          depthWrite: false,
        }),
      );
      line.rotation.set(x, 0, z);
      line.raycast = () => undefined;
      hulls.add(line);
    }
  }
  function drawHulls() {
    clearHulls();
    if (destroyed) return;
    if (layout === "sphere") {
      drawShell();
      return;
    }
    if (layout !== "clusters") return;
    const focus = active();
    const focusGroup = focus ? byId.get(focus)?.data.group : undefined;
    for (const group of groupOrder) {
      const members = nodes.filter(
        (node) => node.data.group === group && node.x !== undefined,
      );
      if (members.length < 2) continue;
      const color = new T.Color(colorOf(group));
      const faded = focusGroup !== undefined && focusGroup !== group;
      const points = members.map(
        (node) => new T.Vector3(node.x, node.y, node.z),
      );
      let geometry: Three.BufferGeometry | null = null;
      if (points.length >= 4) {
        try {
          const convex = new ConvexGeometry(points);
          if (convex.getAttribute("position")?.count) geometry = convex;
          else convex.dispose();
        } catch {
          geometry = null;
        }
      }
      if (!geometry) {
        const centre = points
          .reduce((sum, point) => sum.add(point), new T.Vector3())
          .divideScalar(points.length);
        const reach =
          Math.max(...points.map((point) => point.distanceTo(centre))) + 6;
        geometry = new T.SphereGeometry(reach, 16, 12);
        geometry.translate(centre.x, centre.y, centre.z);
      }
      const surface = new T.Mesh(
        geometry,
        new T.MeshBasicMaterial({
          color,
          transparent: true,
          opacity: faded ? 0.01 : 0.035,
          depthWrite: false,
          side: T.DoubleSide,
        }),
      );
      surface.raycast = () => undefined;
      const edges = new T.LineSegments(
        new T.EdgesGeometry(geometry, 20),
        new T.LineBasicMaterial({
          color,
          transparent: true,
          opacity: faded ? 0.025 : 0.12,
        }),
      );
      edges.raycast = () => undefined;
      hulls.add(surface, edges);
    }
  }

  // --- Data ----------------------------------------------------------------
  function ingest(data: { nodes: GraphNode[]; links: GraphLink[] }) {
    const previous = byId;
    const seen = new Set<string>();
    visuals.clear();
    dense = data.nodes.length >= 400;
    nodes = [];
    for (const item of data.nodes) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      const css = colorOf(item.group);
      const old = previous.get(item.id);
      nodes.push({
        id: item.id,
        data: item,
        hub: false,
        radius: 0,
        rgb: hexRgb(css),
        css,
        ...(old ? { x: old.x, y: old.y, z: old.z, vx: 0, vy: 0, vz: 0 } : {}),
      });
    }
    byId = new Map(nodes.map((node) => [node.id, node]));
    links = data.links
      .filter((link) => byId.has(link.source) && byId.has(link.target))
      .map((link) => ({
        source: link.source,
        target: link.target,
        data: link,
      }));
    topology = new Topology(nodes, links);
    labelledIds = new Set(
      [...topology.hubs]
        .sort((a, b) => topology.degree(b.id) - topology.degree(a.id) || a.id.localeCompare(b.id))
        .slice(0, Math.min(24, Math.max(4, Math.ceil(Math.sqrt(nodes.length)))))
        .map(node => node.id),
    );
    for (const node of nodes) {
      const size = Math.min(
        10,
        Math.max(0.5, node.data.size ?? (node.hub ? 4 : 1)),
      );
      // Overall node scale; kept below 1 so labels and edges, not spheres, dominate.
      node.radius = 0.6 * (node.hub
        ? 2 + 1.6 * Math.sqrt(size)
        : 1.15 * Math.sqrt(size));
    }
    // Sphere cap relaxation is unnecessary work for Tree and Force layouts.
    sphere = layout === "sphere" ? sphereTargets(topology, groupOrder) : new Map();
    tree = topology.levels();
    const breadth = new Map<number, number>();
    for (const depth of tree.depth.values()) breadth.set(depth, (breadth.get(depth) ?? 0) + 1);
    levelGap = Math.max(55, Math.sqrt(Math.max(1, ...breadth.values())) * 7);
    const seeds = fibonacciSphere(nodes.length);
    centres = clusterCentres(
      groupOrder.filter((group) =>
        nodes.some((node) => node.data.group === group),
      ),
      nodes.length,
    );
    for (const [index, node] of nodes.entries()) {
      if (node.x !== undefined) continue;
      const seed = sphere.get(node.id) ?? scale(seeds[index], layout === "tree" ? treeRadius(node) : levelGap * 1.5);
      node.x = seed.x;
      node.y = seed.y;
      node.z = seed.z;
    }
    if (hoverId && !byId.has(hoverId)) hoverId = null;
    if (selectedId && !byId.has(selectedId)) selectedId = null;
    for (const id of [...visuals.keys()]) if (!byId.has(id)) visuals.delete(id);
    buildBatches();
  }

  function load(warmup: number) {
    applyForces();
    graph
      .linkVisibility(!dense)
      .warmupTicks(dense ? Math.min(warmup, reduced ? 30 : 4) : warmup)
      .graphData({ nodes, links });
    applyForces();
    paint();
  }

  function overview(duration: number) {
    const points =
      layout === "sphere"
        ? [...sphere.values()]
        : nodes.map((node) => ({
            x: node.x ?? 0,
            y: node.y ?? 0,
            z: node.z ?? 0,
          }));
    if (!points.length) return;
    const centre =
      layout === "sphere"
        ? { x: 0, y: 0, z: 0 }
        : scale(
            points.reduce(
              (sum, p) => ({ x: sum.x + p.x, y: sum.y + p.y, z: sum.z + p.z }),
              {
                x: 0,
                y: 0,
                z: 0,
              },
            ),
            1 / points.length,
          );
    let reach = 0;
    for (const p of points)
      reach = Math.max(
        reach,
        Math.hypot(p.x - centre.x, p.y - centre.y, p.z - centre.z),
      );
    reach = Math.max(24, reach + 10);
    const camera = graph.camera() as Three.PerspectiveCamera;
    const vertical = ((camera.fov || 40) * Math.PI) / 180;
    const aspect = (el.clientWidth || 1) / (el.clientHeight || 1);
    const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * aspect);
    const distance =
      (reach / Math.sin(Math.min(vertical, horizontal) / 2)) * 1.04;
    const direction = unit({ x: 0.22, y: 0.2, z: 1 });
    graph.cameraPosition(
      {
        x: centre.x + direction.x * distance,
        y: centre.y + direction.y * distance,
        z: centre.z + direction.z * distance,
      },
      centre,
      duration,
    );
  }

  // --- Graph instance ------------------------------------------------------
  type Instance = ForceGraph3DInstance<SimNode, SimLink>;
  const Graph = ForceGraph3D as unknown as new (
    element: HTMLElement,
    config?: {
      controlType?: "trackball" | "orbit" | "fly";
      rendererConfig?: object;
    },
  ) => Instance;
  const graph = new Graph(el, {
    controlType: "orbit",
    rendererConfig: {
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
    },
  });
  function finishInitialFit() {
    if (!initialFit) return;
    initialFit = false;
    overview(0);
    // Public focus can arrive before the renderer's first frame. Apply it
    // after initialization instead of letting the initial fit override it.
    queueMicrotask(() => {
      if (destroyed || !rendering || pendingFocus === undefined) return;
      const next = pendingFocus;
      pendingFocus = undefined;
      handle.focus(next);
    });
  }
  graph
    .width(el.clientWidth || 1)
    .height(el.clientHeight || 1)
    .backgroundColor("rgba(0,0,0,0)")
    .showNavInfo(false)
    .nodeRelSize(1)
    .nodeVisibility(true)
    .enablePointerInteraction(true)
    .nodeVal((node) => node.radius ** 3)
    .nodeThreeObject((node) => buildNode(node))
    .nodeLabel((node) => tooltip(node))
    .linkWidth((link) => {
      const weight = link.data.weight ?? 1;
      const a = nodeOf(link.source);
      const b = nodeOf(link.target);
      if (weight > 1.5)
        return a.hub || b.hub ? Math.min(0.8, 0.2 * weight) : Math.min(1.6, 0.35 * weight);
      return a.hub && b.hub ? 0.18 : 0;
    })
    .linkOpacity(1)
    .linkResolution(4)
    .linkDirectionalArrowRelPos(1)
    .linkDirectionalArrowColor((link) => rgba(nodeOf(link.source).rgb, 0.9))
    .linkDirectionalParticleWidth(1.3)
    .linkDirectionalParticleSpeed(0.006)
    .linkDirectionalParticleColor((link) => nodeOf(link.source).css)
    .linkLabel((link) => {
      if (!link.data.label) return "";
      const box = document.createElement("div");
      box.className = "graph3d-tip";
      box.textContent = link.data.label;
      return box;
    })
    .d3AlphaDecay(0.03)
    .cooldownTime(8000)
    .cooldownTicks(reduced ? 0 : Infinity)
    .onEngineTick(() => {
      syncBatches();
      scheduleLabels();
      finishInitialFit();
      if (!dense && layout === "clusters" && ++hullTick % 12 === 0) drawHulls();
    })
    .onEngineStop(() => {
      syncBatches();
      finishInitialFit();
      drawHulls();
      if (fitAfterSettle) {
        fitAfterSettle = false;
        overview(reduced ? 0 : 700);
      }
    })
    .onNodeHover((node) => {
      if (!rendering) return;
      hoverId = node?.id ?? null;
      el.style.cursor = node ? "pointer" : "";
      setRotation(!node, node ? 0 : 2500);
      paint();
      if (layout === "clusters") drawHulls();
      opts.onHover?.(node?.data ?? null);
    })
    .onNodeClick((node) => {
      if (!rendering) return;
      selectedId = node.id;
      fly(node);
      paint();
      opts.onSelect?.(node.data);
    })
    .onNodeRightClick((node, event) => {
      if (!rendering) return;
      opts.onContextMenu?.(node.data, event);
    })
    .onNodeDragEnd((node) => {
      if (rendering && lastPointer) opts.onDragEnd?.(node.data, lastPointer);
    })
    .onBackgroundClick(() => {
      if (!rendering) return;
      if (!selectedId) return;
      selectedId = null;
      paint();
    });
  graph.scene().add(hulls);
  graph.scene().add(batches);
  graph.renderer().setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  graph.renderer().domElement.setAttribute("aria-hidden", "true");

  const controls = graph.controls() as OrbitControls;
  controls.mouseButtons.MIDDLE = T.MOUSE.ROTATE;
  controls.autoRotateSpeed = 0.45;
  function setRotation(on: boolean, delay = 0) {
    clearTimeout(resumeTimer);
    if (destroyed) return;
    if (reduced || !on || !rendering) controls.autoRotate = false;
    else if (delay)
      resumeTimer = setTimeout(() => {
        if (!destroyed && rendering && !reduced) controls.autoRotate = true;
      }, delay);
    else controls.autoRotate = true;
  }
  const onControlStart = () => setRotation(false);
  const onControlEnd = () => setRotation(true, 7000);
  controls.addEventListener?.("change", scheduleLabels);
  controls.addEventListener?.("start", onControlStart);
  controls.addEventListener?.("end", onControlEnd);
  setRotation(true);

  // 3d-force-graph ends a node drag by dispatching a synthetic touch `pointerup`
  // with pointerId 0. OrbitControls (three r186) then still holds the real mouse
  // pointer, reads a position it never tracked and throws, leaving orbiting stuck.
  // Swap that event for one naming the pointer that actually went down.
  const canvas = graph.renderer().domElement;
  const page = canvas.ownerDocument;
  let lastPointer: PointerEvent | null = null;
  const rememberPointer = (event: PointerEvent) => {
    if (event.isTrusted) lastPointer = event;
  };
  const substitutes = new WeakSet<Event>();
  const releaseRealPointer = (event: PointerEvent) => {
    if (
      !rendering ||
      event.isTrusted ||
      substitutes.has(event) ||
      event.pointerId !== 0 ||
      !lastPointer
    )
      return;
    event.stopImmediatePropagation();
    const real = new PointerEvent("pointerup", {
      pointerId: lastPointer.pointerId,
      pointerType: lastPointer.pointerType,
      clientX: lastPointer.clientX,
      clientY: lastPointer.clientY,
    });
    substitutes.add(real);
    page.dispatchEvent(real);
  };
  canvas.addEventListener("pointerdown", rememberPointer, true);
  page.addEventListener("pointermove", rememberPointer, true);
  page.addEventListener("pointerup", rememberPointer, true);
  page.addEventListener("pointerup", releaseRealPointer, true);

  const keyboardOrbit = new T.Vector3();
  const rotateFromKeyboard = (event: KeyboardEvent) => {
    if (destroyed || !rendering || event.defaultPrevented || event.isComposing ||
        !event.ctrlKey || event.altKey || event.metaKey || event.shiftKey ||
        (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
    const target = event.target;
    if (target instanceof Element && target.closest(
      "input,textarea,select,[contenteditable]:not([contenteditable=false]),[role=textbox]"
    )) return;
    if (page.getSelection()?.toString()) return;
    for (const dialog of page.querySelectorAll("dialog[open],[aria-modal=true]:not(dialog)")) {
      if (dialog.getClientRects().length) return;
    }
    if (page.fullscreenElement && !page.fullscreenElement.contains(el)) return;
    const bounds = el.getBoundingClientRect();
    if (!bounds.width || !bounds.height || bounds.bottom <= 0 || bounds.right <= 0 ||
        bounds.top >= window.innerHeight || bounds.left >= window.innerWidth) return;
    event.preventDefault();
    setRotation(false);
    const camera = graph.camera() as Three.PerspectiveCamera;
    keyboardOrbit.copy(camera.position).sub(controls.target)
      .applyAxisAngle(camera.up, (event.key === "ArrowLeft" ? -1 : 1) * Math.PI / 18)
      .add(controls.target);
    graph.cameraPosition(keyboardOrbit, controls.target, 0);
    controls.update();
    scheduleLabels();
    setRotation(true, 7000);
  };
  page.addEventListener("keydown", rotateFromKeyboard);

  function fly(node: SimNode) {
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    const z = node.z ?? 0;
    const distance = Math.hypot(x, y, z);
    const offset = node.hub ? 110 + node.radius * 8 : 55;
    const position =
      distance < 1
        ? { x: 0, y: offset * 0.3, z: offset }
        : scale({ x, y, z }, 1 + offset / distance);
    graph.cameraPosition(position, { x, y, z }, reduced ? 0 : 900);
  }

  ingest(opts);
  load(reduced ? 300 : layout === "sphere" ? 40 : 160);
  drawHulls();
  fitAfterSettle = layout !== "sphere";

  const resizeObserver = new ResizeObserver(() => handle.resize());
  const syncVisibility = () => {
    if (destroyed) return;
    const visible = paneActive && intersecting && !document.hidden &&
      el.getClientRects().length > 0 && el.clientWidth > 0 && el.clientHeight > 0;
    controls.enabled = visible;
    if (visible === rendering) return;
    rendering = visible;
    if (!visible) {
      setRotation(false);
      cancelAnimationFrame(labelFrame);
      labelFrame = 0;
      hoverId = null;
      el.style.cursor = "";
      opts.onHover?.(null);
      graph.pauseAnimation();
    } else {
      graph.resumeAnimation();
      handle.resize();
      paint();
      if (pendingFocus !== undefined) {
        const next = pendingFocus;
        pendingFocus = undefined;
        handle.focus(next);
      }
      setRotation(true, 2500);
    }
  };
  const visibilityObserver = new IntersectionObserver((entries) => {
    intersecting = entries.some(entry => entry.isIntersecting);
    syncVisibility();
  });
  visibilityObserver.observe(el);
  document.addEventListener("visibilitychange", syncVisibility);
  resizeObserver.observe(el);
  const recolor = () => {
    const colors = harmonize(PALETTE_VARS.map(cssVar).filter(Boolean).map(value => resolveColor(value, probe)));
    if (colors.length) palette.splice(0, palette.length, ...colors);
    ink = resolveColor(cssVar("--ink"), probe);
    mutedRgb = hexRgb(resolveColor(cssVar("--muted"), probe));
    chrome = hexRgb(resolveColor(cssVar("--chrome"), probe));
    handle.update({nodes: nodes.map(node => node.data), links: links.map(link => link.data)});
  };
  window.addEventListener("kitty:theme", recolor);
  const onMotionChange = () => {
    reduced = motionQuery.matches || !!opts.reducedMotion;
    controls.enableDamping = !reduced;
    setRotation(!hoverId);
    graph.cooldownTicks(reduced ? 0 : Infinity);
    if (reduced) {
      load(300);
      overview(0);
    } else {
      paint();
      graph.d3ReheatSimulation();
    }
  };
  controls.enableDamping = !reduced;
  motionQuery.addEventListener("change", onMotionChange);

  canvas.dataset.graphLook = "3d";
  const handle: GraphRenderer = {
    nodePoint(id) {
      const node = byId.get(id);
      if (destroyed || !node) return null;
      const point = graph.graph2ScreenCoords(node.x ?? 0, node.y ?? 0, node.z ?? 0);
      const bounds = el.getBoundingClientRect();
      return { x: bounds.left + point.x, y: bounds.top + point.y };
    },
    nodeAt(x, y) {
      if (destroyed || !rendering) return null;
      const bounds = el.getBoundingClientRect();
      if (x < bounds.left || x > bounds.right || y < bounds.top || y > bounds.bottom) return null;
      const camera = graph.camera() as Three.PerspectiveCamera;
      const ray = new T.Raycaster();
      ray.setFromCamera(new T.Vector2((x - bounds.left) / bounds.width * 2 - 1, 1 - (y - bounds.top) / bounds.height * 2), camera);
      let nearest: SimNode | null = null;
      let distance = Infinity;
      for (const node of nodes) {
        hitCentre.set(node.x ?? 0, node.y ?? 0, node.z ?? 0);
        const radius = Math.max(node.radius * 1.6, 2.6);
        const hit = ray.ray.intersectSphere(new T.Sphere(hitCentre, radius), hitPoint);
        if (!hit) continue;
        const depth = ray.ray.origin.distanceTo(hit);
        if (depth < distance) { nearest = node; distance = depth; }
      }
      return nearest?.data ?? null;
    },
    update(data) {
      if (destroyed) return;
      ingest(data);
      load(reduced ? 300 : 20);
      drawHulls();
    },
    setLayout(next) {
      if (destroyed || next === layout) return;
      layout = next;
      if (layout === "sphere") sphere = sphereTargets(topology, groupOrder);
      drawHulls();
      fitAfterSettle = true;
      if (reduced) {
        load(300);
        drawHulls();
        overview(0);
        fitAfterSettle = false;
      } else {
        applyForces();
        paint();
        graph.d3ReheatSimulation();
        if (layout === "sphere") {
          fitAfterSettle = false;
          overview(900);
        }
      }
    },
    setPhysics(next) {
      if (destroyed) return;
      for (const key of Object.keys(PHYSICS_LIMITS) as (keyof GraphPhysics)[]) {
        const value = next[key];
        if (typeof value !== "number" || !Number.isFinite(value)) continue;
        const limit = PHYSICS_LIMITS[key];
        physics[key] = Math.min(limit.max, Math.max(limit.min, value));
      }
      if (reduced) load(300);
      else {
        applyForces();
        graph.d3ReheatSimulation();
      }
    },
    setActive(active) {
      paneActive = active;
      syncVisibility();
    },
    focus(id) {
      if (destroyed) return;
      if (!rendering || initialFit) {
        pendingFocus = id;
        return;
      }
      fitAfterSettle = false;
      initialFit = false;
      setRotation(false);
      const node = id ? byId.get(id) : undefined;
      selectedId = node?.id ?? null;
      paint();
      if (layout === "clusters") drawHulls();
      if (node) fly(node);
      else overview(reduced ? 0 : 800);
      setRotation(true, 7000);
    },
    resize() {
      if (destroyed) return;
      const width = el.clientWidth;
      const height = el.clientHeight;
      if (width > 0 && height > 0) graph.width(width).height(height);
      syncVisibility();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      clearTimeout(resumeTimer);
      resizeObserver.disconnect();
      visibilityObserver.disconnect();
      document.removeEventListener("visibilitychange", syncVisibility);
      window.removeEventListener("kitty:theme", recolor);
      motionQuery.removeEventListener("change", onMotionChange);
      controls.removeEventListener?.("start", onControlStart);
      controls.removeEventListener?.("end", onControlEnd);
      canvas.removeEventListener("pointerdown", rememberPointer, true);
      page.removeEventListener("pointermove", rememberPointer, true);
      page.removeEventListener("pointerup", rememberPointer, true);
      page.removeEventListener("pointerup", releaseRealPointer, true);
      page.removeEventListener("keydown", rotateFromKeyboard);
      clearHulls();
      clearBatches();
      cancelAnimationFrame(labelFrame);
      controls.removeEventListener?.("change", scheduleLabels);
      const renderer = graph.renderer();
      graph._destructor();
      for (const resource of [
        hubGeometry,
        leafGeometry,
        hitMaterial,
      ])
        resource.dispose();
      visuals.clear();
      renderer.forceContextLoss();
      el.replaceChildren();
    },
  };
  graph.pauseAnimation();
  syncVisibility();
  return handle;
}
