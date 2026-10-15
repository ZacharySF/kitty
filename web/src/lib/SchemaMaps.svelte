<script lang="ts">
  import { onMount, tick } from "svelte";
  import { Panel, Button, Badge } from "@nunera/bracket";
  import { api, safeUrl } from "./api";
  import SchemaNetwork from "./SchemaNetwork.svelte";
  import type { GraphLayout, GraphLink, GraphNode } from "./graph3d";
  let { revision = 0, demo = false }: { revision?: number; demo?: boolean } =
    $props();
  type Source = { kind: "subject" | "job" | "company"; id: number };
  type Node = {
    id: string;
    kind: string;
    label: string;
    notes: string;
    x: number;
    y: number;
    url: string;
    source: Source | null;
  };
  type Edge = { id: string; from: string; to: string; label: string };
  type Graph = { nodes: Node[]; edges: Edge[] };
  type View = { x: number; y: number; zoom: number };
  type Live = {
    label?: string;
    url?: string;
    status?: string;
    queue_state?: string;
    company?: string;
    closed_at?: string;
    missing?: boolean;
  };
  type MapItem = {
    id: number;
    title: string;
    mode: string;
    revision: number;
    graph: Graph;
    viewport: View;
    live: Record<string, Live>;
    can_undo: boolean;
  };
  type Summary = { id: number; title: string; mode: string; revision: number };
  type Job = {
    id: number;
    title: string;
    company: string;
    status: string | null;
    queue_state: string;
  };
  type Snapshot = { title: string; graph: Graph; viewport: View };
  type View3D = "edit" | "map3d" | "network";
  type NetworkNode = {
    id: string;
    kind: string;
    label: string;
    source: Source | null;
    live: Live | null;
    url: string;
    maps: number[];
    members: {
      map_id: number;
      node_id: string;
      label: string;
      notes: string;
    }[];
    degree: number;
    cluster: number;
  };
  type NetworkEdge = {
    id: string;
    from: string;
    to: string;
    label: string;
    weight: number;
    maps: number[];
    members: { map_id: number; edge_id: string }[];
  };
  type Network = {
    mode: string | null;
    merge_labels: boolean;
    maps: Summary[];
    nodes: NetworkNode[];
    edges: NetworkEdge[];
    clusters: { id: number; size: number; hub: string; label: string }[];
    unified: number;
  };
  type Point = { x: number; y: number };
  const NODE_W = 200,
    NODE_H = 64,
    LIMIT = 100000;
  let maps = $state<Summary[]>([]),
    subjects = $state<{ id: number; name: string }[]>([]);
  let current = $state<MapItem | null>(null),
    busy = $state(false),
    dirty = $state(false),
    blocked = $state(false);
  let error = $state(""),
    notice = $state(""),
    newTitle = $state(""),
    mode = $state("study");
  let selected = $state(""),
    selectedEdge = $state(""),
    search = $state(""),
    kind = $state("topic"),
    label = $state(""),
    subjectId = $state("");
  let edgeFrom = $state(""),
    edgeTo = $state(""),
    edgeLabel = $state("relates to");
  let draftLabel = $state(""),
    draftNotes = $state(""),
    draftUrl = $state("");
  let history = $state<Snapshot[]>([]),
    stage = $state<SVGSVGElement>();
  let showJobs = $state(false),
    jobSearch = $state(""),
    jobs = $state<Job[]>([]),
    jobIds = $state<number[]>([]),
    jobOffset = $state(0),
    moreJobs = $state(false);
  let gesture: {
    pointer: number;
    x: number;
    y: number;
    node: string;
    before: Snapshot;
    moved: boolean;
  } | null = null;
  let view = $state<View3D>("edit"),
    mapLayout = $state<GraphLayout>("force"),
    netLayout = $state<GraphLayout>("clusters");
  let network = $state<Network | null>(null),
    netMode = $state(""),
    netMaps = $state<number[]>([]),
    mergeLabels = $state(false),
    colorBy = $state<"map" | "kind" | "cluster">("cluster"),
    netSelected = $state(""),
    netBusy = $state(false),
    netError = $state("");
  const netNode = $derived(network?.nodes.find((n) => n.id === netSelected));
  const netChoices = $derived(
    netMode ? maps.filter((m) => m.mode === netMode) : maps,
  );
  const node = $derived(current?.graph.nodes.find((n) => n.id === selected));
  const filtered = $derived(
    current?.graph.nodes.filter((n) =>
      `${name(n)} ${n.kind} ${n.notes}`
        .toLowerCase()
        .includes(search.toLowerCase()),
    ) ?? [],
  );
  const editable = $derived(!!current && !busy && !blocked);
  function clone<T>(value: T): T {
    return JSON.parse(JSON.stringify(value));
  }
  function name(n: Node) {
    return (n.source ? current?.live[n.id]?.label : undefined) ?? n.label;
  }
  function snapshot(): Snapshot {
    return clone({
      title: current!.title,
      graph: current!.graph,
      viewport: current!.viewport,
    });
  }
  function remember() {
    history = [...history.slice(-29), snapshot()];
    dirty = true;
    notice = "Unsaved local edits";
  }
  function accepted(item: MapItem) {
    current = item;
    dirty = false;
    blocked = false;
    history = [];
    selected = "";
    selectedEdge = "";
    notice = "Saved locally";
  }
  function failure(e: unknown) {
    error = e instanceof Error ? e.message : String(e);
  }
  async function refresh() {
    const data = await api<{
      maps: Summary[];
      subjects: { id: number; name: string }[];
    }>("/schema");
    maps = data.maps;
    subjects = data.subjects;
  }
  async function open(id: number, force = false) {
    if (
      busy ||
      (!force &&
        dirty &&
        !confirm(
          "Discard unsaved map edits? Export them first to keep a copy.",
        ))
    )
      return;
    busy = true;
    error = "";
    try {
      accepted(await api<MapItem>(`/schema/map?id=${id}`));
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  async function create() {
    if (
      busy ||
      !newTitle.trim() ||
      (dirty && !confirm("Discard unsaved map edits?"))
    )
      return;
    busy = true;
    error = "";
    try {
      accepted(await api<MapItem>("/schema/create", { title: newTitle, mode }));
      newTitle = "";
      await refresh();
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  async function save() {
    if (!current || !editable || !dirty) return;
    busy = true;
    error = "";
    try {
      accepted(
        await api<MapItem>("/schema/save", {
          id: current.id,
          revision: current.revision,
          title: current.title,
          graph: current.graph,
          viewport: current.viewport,
        }),
      );
      await refresh();
    } catch (e) {
      failure(e);
      blocked = error.includes("changed in another window");
    } finally {
      busy = false;
    }
  }
  async function removeMap() {
    if (
      !current ||
      !editable ||
      !confirm(
        `Permanently delete “${current.title}” and its graph? Export first to keep a copy.`,
      )
    )
      return;
    busy = true;
    error = "";
    try {
      await api("/schema/delete", {
        id: current.id,
        revision: current.revision,
        confirm: true,
      });
      current = null;
      history = [];
      dirty = false;
      await refresh();
    } catch (e) {
      failure(e);
      blocked = error.includes("changed in another window");
    } finally {
      busy = false;
    }
  }
  async function undo() {
    if (!current || !editable) return;
    if (history.length) {
      const previous = history[history.length - 1];
      history = history.slice(0, -1);
      Object.assign(current, clone(previous));
      dirty = true;
      selected = "";
      selectedEdge = "";
      return;
    }
    if (dirty || !current.can_undo) return;
    busy = true;
    error = "";
    try {
      accepted(
        await api<MapItem>("/schema/undo", {
          id: current.id,
          revision: current.revision,
        }),
      );
      await refresh();
    } catch (e) {
      failure(e);
      blocked = error.includes("changed in another window");
    } finally {
      busy = false;
    }
  }
  function choose(n: Node) {
    selected = n.id;
    selectedEdge = "";
    draftLabel = n.label;
    draftNotes = n.notes;
    draftUrl = n.url;
  }
  function addNode() {
    if (!current || !editable || current.graph.nodes.length >= 300) return;
    const subject = subjects.find((s) => s.id === Number(subjectId));
    if (kind === "class" && !subject) {
      error = "Choose an existing study class first. Create classes in Study.";
      return;
    }
    if (kind !== "class" && !label.trim()) return;
    if (
      subject &&
      kind === "class" &&
      current.graph.nodes.some(
        (n) => n.source?.kind === "subject" && n.source.id === subject.id,
      )
    ) {
      error = "This class already has a node in this map.";
      return;
    }
    remember();
    const rect = stage?.getBoundingClientRect(),
      view = current.viewport;
    const n: Node = {
      id: crypto.randomUUID(),
      kind,
      label: kind === "class" ? subject!.name : label.trim(),
      notes: "",
      url: "",
      source: kind === "class" ? { kind: "subject", id: subject!.id } : null,
      x: Math.max(
        -100000,
        Math.min(100000, ((rect?.width ?? 700) / 2 - view.x) / view.zoom - 100),
      ),
      y: Math.max(
        -100000,
        Math.min(100000, ((rect?.height ?? 500) / 2 - view.y) / view.zoom - 32),
      ),
    };
    current.graph.nodes.push(n);
    label = "";
    choose(n);
  }
  function updateNode() {
    if (!node || !editable || !draftLabel.trim()) return;
    remember();
    node.label = draftLabel.trim();
    node.notes = draftNotes;
    node.url = draftUrl.trim();
  }
  function deleteNode() {
    if (!current || !node || !editable) return;
    remember();
    current.graph.nodes = current.graph.nodes.filter((n) => n.id !== selected);
    current.graph.edges = current.graph.edges.filter(
      (e) => e.from !== selected && e.to !== selected,
    );
    if (edgeFrom === selected) edgeFrom = "";
    if (edgeTo === selected) edgeTo = "";
    selected = "";
  }
  function unlink() {
    if (!node || !editable) return;
    remember();
    node.label = name(node);
    node.url = current?.live[node.id]?.url ?? node.url;
    node.source = null;
    choose(node);
  }
  function connect() {
    if (
      !current ||
      !editable ||
      !current.graph.nodes.some((n) => n.id === edgeFrom) ||
      !current.graph.nodes.some((n) => n.id === edgeTo) ||
      current.graph.edges.length >= 600
    )
      return;
    remember();
    current.graph.edges.push({
      id: crypto.randomUUID(),
      from: edgeFrom,
      to: edgeTo,
      label: edgeLabel,
    });
  }
  function removeEdge(id: string) {
    if (!current || !editable) return;
    remember();
    current.graph.edges = current.graph.edges.filter((e) => e.id !== id);
    selectedEdge = "";
  }
  function editEdge(id: string, field: "label" | "from" | "to", value: string) {
    if (!current || !editable) return;
    const e = current.graph.edges.find((e) => e.id === id);
    if (!e) return;
    remember();
    e[field] = value;
  }
  function nodeKey(e: KeyboardEvent, n: Node) {
    if (!editable) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      choose(n);
    }
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      choose(n);
      deleteNode();
    }
    const delta: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    };
    if (delta[e.key]) {
      e.preventDefault();
      remember();
      const d = delta[e.key],
        step = e.shiftKey ? 20 : 4;
      n.x = Math.max(-100000, Math.min(100000, n.x + d[0] * step));
      n.y = Math.max(-100000, Math.min(100000, n.y + d[1] * step));
    }
  }
  function start(e: PointerEvent, id = "") {
    if (!current || !editable || gesture || e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    if (id) {
      const n = current.graph.nodes.find((n) => n.id === id)!;
      choose(n);
    }
    gesture = {
      pointer: e.pointerId,
      x: e.clientX,
      y: e.clientY,
      node: id,
      before: snapshot(),
      moved: false,
    };
    stage?.setPointerCapture(e.pointerId);
  }
  function move(e: PointerEvent) {
    if (!gesture || !current || gesture.pointer !== e.pointerId) return;
    const dx = e.clientX - gesture.x,
      dy = e.clientY - gesture.y;
    if (Math.abs(dx) + Math.abs(dy) < 2 && !gesture.moved) return;
    gesture.moved = true;
    if (gesture.node) {
      const n = current.graph.nodes.find((n) => n.id === gesture!.node)!,
        old = gesture.before.graph.nodes.find((n) => n.id === gesture!.node)!;
      n.x = Math.max(
        -100000,
        Math.min(100000, old.x + dx / current.viewport.zoom),
      );
      n.y = Math.max(
        -100000,
        Math.min(100000, old.y + dy / current.viewport.zoom),
      );
    } else {
      current.viewport.x = Math.max(
        -1e7,
        Math.min(1e7, gesture.before.viewport.x + dx),
      );
      current.viewport.y = Math.max(
        -1e7,
        Math.min(1e7, gesture.before.viewport.y + dy),
      );
    }
  }
  function end(e: PointerEvent, cancel = false) {
    if (!gesture || !current || gesture.pointer !== e.pointerId) return;
    if (cancel) Object.assign(current, clone(gesture.before));
    else if (gesture.moved) {
      history = [...history.slice(-29), gesture.before];
      dirty = true;
      notice = "Unsaved local edits";
    }
    gesture = null;
    if (stage?.hasPointerCapture(e.pointerId))
      stage.releasePointerCapture(e.pointerId);
  }
  function zoom(factor: number, clientX?: number, clientY?: number) {
    if (!current || !editable) return;
    const rect = stage?.getBoundingClientRect(),
      x =
        clientX === undefined
          ? (rect?.width ?? 700) / 2
          : clientX - (rect?.left ?? 0),
      y =
        clientY === undefined
          ? (rect?.height ?? 500) / 2
          : clientY - (rect?.top ?? 0);
    remember();
    const v = current.viewport,
      z = Math.max(0.1, Math.min(4, v.zoom * factor));
    v.x = Math.max(-1e7, Math.min(1e7, x - ((x - v.x) * z) / v.zoom));
    v.y = Math.max(-1e7, Math.min(1e7, y - ((y - v.y) * z) / v.zoom));
    v.zoom = z;
  }
  function fit() {
    if (!current || !editable) return;
    remember();
    frame();
  }
  function frame() {
    if (!current) return;
    const ns = current.graph.nodes,
      rect = stage?.getBoundingClientRect();
    if (!ns.length) {
      current.viewport = { x: 40, y: 40, zoom: 1 };
      return;
    }
    const left = Math.min(...ns.map((n) => n.x)),
      top = Math.min(...ns.map((n) => n.y)),
      width = Math.max(...ns.map((n) => n.x + NODE_W)) - left,
      height = Math.max(...ns.map((n) => n.y + NODE_H)) - top;
    const w = rect?.width ?? 700,
      h = rect?.height ?? 500,
      z = Math.max(0.1, Math.min(2, (w - 60) / width, (h - 60) / height));
    current.viewport = {
      x: (w - width * z) / 2 - left * z,
      y: (h - height * z) / 2 - top * z,
      zoom: z,
    };
  }
  function clampPosition(value: number) {
    return Math.max(-LIMIT, Math.min(LIMIT, Math.round(value)));
  }
  /** Point where the ray from a node box's center along (dx, dy) leaves the box. */
  function boundary(c: Point, dx: number, dy: number): Point {
    const t = Math.min(
      dx === 0 ? Infinity : NODE_W / 2 / Math.abs(dx),
      dy === 0 ? Infinity : NODE_H / 2 / Math.abs(dy),
    );
    return Number.isFinite(t) ? { x: c.x + dx * t, y: c.y + dy * t } : c;
  }
  /**
   * Edge paths: straight when a pair has one connection, mirrored arcs for
   * bidirectional or parallel connections, stacked loops for self-connections.
   * Endpoints stop at box borders so arrowheads stay visible.
   */
  const geometry = $derived.by(() => {
    const shapes = new Map<string, { d: string; lx: number; ly: number }>();
    if (!current) return shapes;
    const byId = new Map(current.graph.nodes.map((n) => [n.id, n])),
      pairs = new Map<string, Edge[]>();
    for (const e of current.graph.edges) {
      const key = e.from < e.to ? `${e.from} ${e.to}` : `${e.to} ${e.from}`;
      const list = pairs.get(key);
      if (list) list.push(e);
      else pairs.set(key, [e]);
    }
    for (const list of pairs.values())
      list.forEach((e, index) => {
        const a = byId.get(e.from)!,
          b = byId.get(e.to)!;
        if (a.id === b.id) {
          const r = 30 + index * 18,
            sx = a.x + NODE_W - 78,
            ex = a.x + NODE_W - 22;
          shapes.set(e.id, {
            d: `M ${sx} ${a.y} C ${sx - 12} ${a.y - 2.2 * r}, ${ex + 12} ${a.y - 2.2 * r}, ${ex} ${a.y}`,
            lx: (sx + ex) / 2,
            ly: a.y - 1.65 * r - 6,
          });
          return;
        }
        const ca = { x: a.x + NODE_W / 2, y: a.y + NODE_H / 2 },
          cb = { x: b.x + NODE_W / 2, y: b.y + NODE_H / 2 },
          sign = e.from < e.to ? 1 : -1,
          dx = (cb.x - ca.x) * sign,
          dy = (cb.y - ca.y) * sign,
          length = Math.hypot(dx, dy) || 1,
          offset = list.length === 1 ? 0 : (index - (list.length - 1) / 2) * 64,
          control = {
            x: (ca.x + cb.x) / 2 - (dy / length) * offset,
            y: (ca.y + cb.y) / 2 + (dx / length) * offset,
          },
          s = boundary(ca, control.x - ca.x, control.y - ca.y),
          t = boundary(cb, control.x - cb.x, control.y - cb.y);
        shapes.set(e.id, {
          d: `M ${s.x} ${s.y} Q ${control.x} ${control.y} ${t.x} ${t.y}`,
          lx: 0.25 * s.x + 0.5 * control.x + 0.25 * t.x,
          ly: 0.25 * s.y + 0.5 * control.y + 0.25 * t.y + 4,
        });
      });
    return shapes;
  });
  /** Layered tree: roots are nodes without incoming connections (or, inside cycles, the busiest node); parents center over children. */
  function treeLayout(graph: Graph) {
    const out = new Map(graph.nodes.map((n) => [n.id, [] as string[]])),
      incoming = new Map(graph.nodes.map((n) => [n.id, 0]));
    for (const e of graph.edges)
      if (e.from !== e.to) {
        out.get(e.from)!.push(e.to);
        incoming.set(e.to, incoming.get(e.to)! + 1);
      }
    const children = new Map<string, string[]>(),
      depth = new Map<string, number>(),
      roots: string[] = [];
    const grow = (root: string) => {
      roots.push(root);
      depth.set(root, 0);
      children.set(root, []);
      const queue = [root];
      for (let i = 0; i < queue.length; i++)
        for (const next of out.get(queue[i])!)
          if (!depth.has(next)) {
            depth.set(next, depth.get(queue[i])! + 1);
            children.get(queue[i])!.push(next);
            children.set(next, []);
            queue.push(next);
          }
    };
    for (const n of graph.nodes)
      if (incoming.get(n.id) === 0 && !depth.has(n.id)) grow(n.id);
    for (const n of [...graph.nodes].sort(
      (a, b) => out.get(b.id)!.length - out.get(a.id)!.length,
    ))
      if (!depth.has(n.id)) grow(n.id);
    const positions = new Map<string, Point>();
    let slot = 0;
    const place = (id: string): number => {
      const kids = children.get(id)!;
      const x = kids.length
        ? (() => {
            const xs = kids.map(place);
            return (xs[0] + xs[xs.length - 1]) / 2;
          })()
        : slot++ * (NODE_W + 40);
      positions.set(id, { x, y: depth.get(id)! * (NODE_H + 90) });
      return x;
    };
    for (const root of roots) {
      place(root);
      slot += 0.5;
    }
    return positions;
  }
  /** Deterministic Fruchterman–Reingold from current positions, then box-overlap removal. */
  function forceLayout(graph: Graph) {
    const ns = graph.nodes,
      count = ns.length,
      spread = new Set(ns.map((n) => `${n.x},${n.y}`)).size === count,
      p = ns.map((n, i) =>
        spread
          ? { x: n.x + NODE_W / 2, y: n.y + NODE_H / 2 }
          : {
              x: Math.cos((2 * Math.PI * i) / count) * 60 * Math.sqrt(count),
              y: Math.sin((2 * Math.PI * i) / count) * 60 * Math.sqrt(count),
            },
      ),
      index = new Map(ns.map((n, i) => [n.id, i])),
      links = graph.edges
        .filter((e) => e.from !== e.to)
        .map((e) => [index.get(e.from)!, index.get(e.to)!]),
      k = 170;
    let temperature = 40 * Math.sqrt(count);
    for (let step = 0; step < 220; step++) {
      const d = p.map(() => ({ x: 0, y: 0 }));
      for (let i = 0; i < count; i++)
        for (let j = i + 1; j < count; j++) {
          let dx = p[i].x - p[j].x,
            dy = p[i].y - p[j].y;
          if (dx === 0 && dy === 0) {
            dx = ((i * 7919 + j) % 13) - 6 || 1;
            dy = ((i + j * 104729) % 11) - 5 || 1;
          }
          const dist = Math.max(1, Math.hypot(dx, dy)),
            force = (k * k) / dist;
          d[i].x += (dx / dist) * force;
          d[i].y += (dy / dist) * force;
          d[j].x -= (dx / dist) * force;
          d[j].y -= (dy / dist) * force;
        }
      for (const [a, b] of links) {
        const dx = p[a].x - p[b].x,
          dy = p[a].y - p[b].y,
          dist = Math.max(1, Math.hypot(dx, dy)),
          force = (dist * dist) / k;
        d[a].x -= (dx / dist) * force;
        d[a].y -= (dy / dist) * force;
        d[b].x += (dx / dist) * force;
        d[b].y += (dy / dist) * force;
      }
      // Gravity keeps disconnected nodes and components from drifting apart.
      const cx = p.reduce((sum, q) => sum + q.x, 0) / count,
        cy = p.reduce((sum, q) => sum + q.y, 0) / count;
      for (let i = 0; i < count; i++) {
        d[i].x -= (p[i].x - cx) * 0.5;
        d[i].y -= (p[i].y - cy) * 0.5;
      }
      for (let i = 0; i < count; i++) {
        const length = Math.max(1, Math.hypot(d[i].x, d[i].y)),
          move = Math.min(length, temperature);
        p[i].x += (d[i].x / length) * move;
        p[i].y += (d[i].y / length) * move;
      }
      temperature = Math.max(1, temperature * 0.975);
    }
    for (let pass = 0; pass < 60; pass++) {
      let moved = false;
      for (let i = 0; i < count; i++)
        for (let j = i + 1; j < count; j++) {
          const ox = NODE_W + 70 - Math.abs(p[i].x - p[j].x),
            oy = NODE_H + 50 - Math.abs(p[i].y - p[j].y);
          if (ox <= 0 || oy <= 0) continue;
          moved = true;
          if (ox / (NODE_W + 70) < oy / (NODE_H + 50)) {
            const shift = (p[i].x <= p[j].x ? -ox : ox) / 2;
            p[i].x += shift;
            p[j].x -= shift;
          } else {
            const shift = (p[i].y <= p[j].y ? -oy : oy) / 2;
            p[i].y += shift;
            p[j].y -= shift;
          }
        }
      if (!moved) break;
    }
    return new Map(
      ns.map((n, i) => [
        n.id,
        { x: p[i].x - NODE_W / 2, y: p[i].y - NODE_H / 2 },
      ]),
    );
  }
  function arrange(kind: "force" | "tree") {
    if (!current || !editable || !current.graph.nodes.length) return;
    remember();
    const positions =
      kind === "tree" ? treeLayout(current.graph) : forceLayout(current.graph);
    for (const n of current.graph.nodes) {
      const point = positions.get(n.id)!;
      n.x = clampPosition(point.x);
      n.y = clampPosition(point.y);
    }
    frame();
    notice = `${kind === "tree" ? "Tree" : "Force"} auto-layout applied locally. Undo restores previous positions; Save keeps them.`;
  }
  function degreeOf(id: string) {
    return (
      current?.graph.edges.filter((e) => e.from === id || e.to === id).length ??
      0
    );
  }
  const mapGraph = $derived.by(() => {
    if (!current) return { nodes: [] as GraphNode[], links: [] as GraphLink[] };
    const degree = new Map<string, number>();
    for (const e of current.graph.edges) {
      degree.set(e.from, (degree.get(e.from) ?? 0) + 1);
      if (e.to !== e.from) degree.set(e.to, (degree.get(e.to) ?? 0) + 1);
    }
    return {
      nodes: current.graph.nodes.map((n): GraphNode => ({
        id: n.id,
        label: name(n),
        group: n.kind,
        kind: n.kind,
        size: Math.min(10, 1 + (degree.get(n.id) ?? 0)),
        detail: `${n.kind}${n.source ? " / linked source" : ""} / ${degree.get(n.id) ?? 0} connections`,
      })),
      links: current.graph.edges.map((e): GraphLink => ({
        source: e.from,
        target: e.to,
        label: e.label,
        directed: true,
      })),
    };
  });
  function mapTitle(id: number) {
    const m =
      network?.maps.find((m) => m.id === id) ?? maps.find((m) => m.id === id);
    return m ? `${m.title} #${m.id}` : `Map #${id}`;
  }
  const netGraph = $derived.by(() => {
    if (!network) return { nodes: [] as GraphNode[], links: [] as GraphLink[] };
    return {
      nodes: network.nodes.map((n): GraphNode => ({
        id: n.id,
        label: n.label,
        kind: n.kind,
        group:
          colorBy === "kind"
            ? n.kind
            : colorBy === "cluster"
              ? `cluster ${n.cluster + 1}`
              : n.maps.length > 1
                ? "shared across maps"
                : mapTitle(n.maps[0]),
        size: Math.min(10, 1 + n.degree),
        detail: `${n.kind} / in ${n.maps.length} map${n.maps.length === 1 ? "" : "s"} / ${n.degree} connections${n.live?.missing ? " / source deleted" : ""}`,
      })),
      links: network.edges.map((e): GraphLink => ({
        source: e.from,
        target: e.to,
        label: e.weight > 1 ? `${e.label} ×${e.weight}` : e.label,
        directed: true,
        weight: e.weight,
      })),
    };
  });
  let networkRequest = 0;
  async function loadNetwork() {
    const ticket = ++networkRequest,
      params = new URLSearchParams();
    if (netMode) params.set("mode", netMode);
    const chosen = netMaps.filter((id) => netChoices.some((m) => m.id === id));
    if (chosen.length) params.set("map_ids", chosen.join(","));
    if (mergeLabels) params.set("merge_labels", "true");
    netBusy = true;
    netError = "";
    try {
      const result = await api<Network>(`/schema/network?${params}`);
      if (ticket !== networkRequest) return;
      network = result;
      if (!result.nodes.some((n) => n.id === netSelected)) netSelected = "";
    } catch (e) {
      if (ticket === networkRequest)
        netError = e instanceof Error ? e.message : String(e);
    } finally {
      if (ticket === networkRequest) netBusy = false;
    }
  }
  $effect(() => {
    if (view !== "network") return;
    void [netMode, netMaps.length, mergeLabels];
    void loadNetwork();
  });
  function neighbors(id: string) {
    const edges = network?.edges ?? [];
    return {
      out: edges.filter((e) => e.from === id),
      in: edges.filter((e) => e.to === id && e.from !== id),
    };
  }
  function netLabel(id: string) {
    return network?.nodes.find((n) => n.id === id)?.label ?? id;
  }
  /** Leave the 3D views for the authoritative 2D editor with the node selected, focused and in view. */
  async function editInMap(mapId: number, nodeId: string) {
    if (current?.id !== mapId) {
      await open(mapId);
      if (current?.id !== mapId) return;
    }
    view = "edit";
    const n = current!.graph.nodes.find((n) => n.id === nodeId);
    if (!n) {
      error = "That node is no longer in the saved map. Reload the network.";
      return;
    }
    choose(n);
    await tick();
    const rect = stage?.getBoundingClientRect(),
      v = current!.viewport;
    if (rect) {
      const sx = n.x * v.zoom + v.x,
        sy = n.y * v.zoom + v.y;
      if (
        sx < 0 ||
        sy < 0 ||
        sx + NODE_W * v.zoom > rect.width ||
        sy + NODE_H * v.zoom > rect.height
      ) {
        v.x = Math.max(
          -1e7,
          Math.min(1e7, rect.width / 2 - (n.x + NODE_W / 2) * v.zoom),
        );
        v.y = Math.max(
          -1e7,
          Math.min(1e7, rect.height / 2 - (n.y + NODE_H / 2) * v.zoom),
        );
      }
    }
    stage
      ?.querySelector<SVGGElement>(`[data-node="${CSS.escape(n.id)}"]`)
      ?.focus();
  }
  function pick(e: PointerEvent) {
    const edge = (e.target as Element | null)?.getAttribute?.("data-edge");
    if (edge) {
      selectedEdge = edge;
      selected = "";
      return;
    }
    start(e);
  }
  function download() {
    if (!current) return;
    const graph = clone(current.graph);
    for (const n of graph.nodes) {
      n.label = name(n).slice(0, 200);
      n.url = (n.source ? current.live[n.id]?.url : undefined) ?? n.url;
      n.source = null;
    }
    const data = {
      format: "kitty-schema",
      version: 1,
      title: current.title,
      mode: current.mode,
      graph,
      viewport: current.viewport,
    };
    const blob = new Blob([JSON.stringify(data)], { type: "application/json" }),
      url = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = url;
    a.download = `schema-${current.id}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function importFile(e: Event) {
    const input = e.currentTarget as HTMLInputElement,
      file = input.files?.[0];
    input.value = "";
    if (
      !file ||
      busy ||
      (dirty && !confirm("Discard unsaved edits to open the imported map?"))
    )
      return;
    if (file.size > 1_000_000) {
      error = "Import must be at most 1 MB.";
      return;
    }
    busy = true;
    error = "";
    try {
      const document = JSON.parse(await file.text());
      accepted(
        await api<MapItem>("/schema/import", {
          title: document.title,
          mode: document.mode,
          document,
        }),
      );
      await refresh();
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  async function findJobs(offset = 0) {
    if (busy) return;
    busy = true;
    error = "";
    try {
      const result = await api<{ jobs: Job[]; more: boolean }>(
        `/schema/jobs?search=${encodeURIComponent(jobSearch)}&offset=${offset}`,
      );
      jobs = result.jobs;
      moreJobs = result.more;
      jobOffset = offset;
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  async function importJobs() {
    if (!current || !editable || dirty || !jobIds.length) return;
    busy = true;
    error = "";
    try {
      accepted(
        await api<MapItem>("/schema/import-jobs", {
          id: current.id,
          revision: current.revision,
          job_ids: jobIds,
        }),
      );
      jobIds = [];
      showJobs = false;
      await refresh();
    } catch (e) {
      failure(e);
      blocked = error.includes("changed in another window");
    } finally {
      busy = false;
    }
  }
  onMount(() => {
    void refresh()
      .then(() => {
        if (maps.length) return open(maps[0].id);
      })
      .catch(failure);
    const guard = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  });
  let seenRevision: number | undefined;
  $effect(() => {
    if (seenRevision === undefined) seenRevision = revision;
    else if (revision !== seenRevision) {
      seenRevision = revision;
      void refresh().catch(failure);
      if (current && !dirty && !busy) void open(current.id, true);
      if (view === "network") void loadNetwork();
    }
  });
</script>

<section class="schema" aria-label="Mental schema maps">
  <Panel
    title="~/schema / mental connections"
    readout={demo ? "LOCAL DEMO" : "LOCAL SQLITE"}
  >
    <p class="intro">
      Connect classes and topics, explore free ideas, or map tracked jobs to
      what you want to learn. Connections are yours — no inferred requirements
      or AI edges.
    </p>
    <div class="toolbar">
      <label
        >Map<select
          value={current?.id ?? ""}
          disabled={busy}
          onchange={(e) => {
            if (e.currentTarget.value) void open(Number(e.currentTarget.value));
          }}
          ><option value="">Select map</option>{#each maps as m}<option
              value={m.id}>{m.title} / {m.mode}</option
            >{/each}</select
        ></label
      >
      <label
        >New map<input
          bind:value={newTitle}
          maxlength="200"
          placeholder="A web of things to learn"
          disabled={busy}
        /></label
      >
      <label
        >Mode<select bind:value={mode} disabled={busy}
          ><option value="study">Study / classes + topics</option><option
            value="general">General / mental map</option
          ><option value="jobs">Jobs / career learning</option></select
        ></label
      >
      <Button onclick={create} disabled={busy || !newTitle.trim()}
        >Create</Button
      >
      <label class="file"
        >Import JSON<input
          type="file"
          accept="application/json,.json"
          onchange={importFile}
          disabled={busy}
        /></label
      >
    </div>
  </Panel>
  {#if error}<div role="alert" class="error">{error}</div>{/if}
  <div class="toolbar views" role="group" aria-label="Schema view">
    {#each [["edit", "Edit / 2D editor"], ["map3d", "3D map / this map"], ["network", "Network / all schemas"]] as [id, text]}<Button
        intent={view === id ? "primary" : "neutral"}
        aria-pressed={view === id}
        disabled={id === "map3d" && !current}
        onclick={() => {
          view = id as View3D;
        }}>{text}</Button
      >{/each}
    <span class="hint"
      >2D editing is authoritative. 3D views are read-only; select a node and
      jump back to edit it.</span
    >
  </div>
  {#if view === "network"}
    <div class="workspace network-workspace">
      <aside>
        <Panel title="[01] Network filters">
          <label
            >Mode<select bind:value={netMode} disabled={netBusy}
              ><option value="">All modes</option><option value="study"
                >Study</option
              ><option value="general">General</option><option value="jobs"
                >Jobs</option
              ></select
            ></label
          >
          <fieldset class="map-picks">
            <legend>Maps ({netMaps.length ? netMaps.length : "all"})</legend>
            <Button
              onclick={() => {
                netMaps = [];
              }}
              disabled={!netMaps.length}>Use all maps</Button
            >
            {#each netChoices as m (m.id)}<label class="check"
                ><input
                  type="checkbox"
                  checked={netMaps.includes(m.id)}
                  onchange={(e) => {
                    netMaps = e.currentTarget.checked
                      ? [...netMaps, m.id]
                      : netMaps.filter((id) => id !== m.id);
                  }}
                />{m.title} <small>{m.mode}</small></label
              >{:else}<p class="hint">No maps in this mode yet.</p>{/each}
          </fieldset>
          <label class="check"
            ><input type="checkbox" bind:checked={mergeLabels} />Merge unlinked
            nodes with the same kind + label</label
          >
          <p class="hint">
            Linked classes, jobs and companies always unify across maps. Label
            merge is off by default so different ideas that share a name stay
            separate.
          </p>
          <label
            >Color by<select bind:value={colorBy}
              ><option value="cluster">Cluster (connected group)</option><option
                value="kind">Node kind</option
              ><option value="map">Map (shared nodes highlighted)</option
              ></select
            ></label
          >
          <Button onclick={() => loadNetwork()} disabled={netBusy}
            >Refresh from saved maps</Button
          >
          <p class="hint" role="status">
            {netBusy
              ? "Merging maps…"
              : network
                ? `${network.maps.length} maps → ${network.nodes.length} nodes, ${network.edges.length} connections, ${network.clusters.length} clusters; ${network.unified} nodes unified across maps or labels.`
                : ""}{dirty
              ? " Unsaved edits in the open map are not included."
              : ""}
          </p>
          {#if network?.clusters.length}<ol class="clusters">
              {#each network.clusters.slice(0, 12) as c (c.id)}<li>
                  Cluster {c.id + 1}: {c.size} node{c.size === 1 ? "" : "s"},
                  hub “{c.label}”
                </li>{/each}
            </ol>{/if}
        </Panel>
      </aside>
      <main class="graph-pane">
        {#if netError}<div role="alert" class="error">{netError}</div>{/if}
        <SchemaNetwork
          title="Merged schema network"
          nodes={netGraph.nodes}
          links={netGraph.links}
          bind:layout={netLayout}
          selected={netSelected || null}
          onSelect={(id) => {
            netSelected = id;
          }}
        />
      </main>
      <aside>
        <Panel title="[03] Network inspector">
          {#if netNode}
            {@const links = neighbors(netNode.id)}
            <h2>{netNode.label}</h2>
            <Badge label={netNode.kind} />
            <p>
              Cluster {netNode.cluster + 1} · {netNode.degree} connection{netNode.degree ===
              1
                ? ""
                : "s"}
            </p>
            {#if netNode.source}<p>
                Linked {netNode.source.kind} #{netNode.source.id}
              </p>
              {#if netNode.live?.missing}<p class="error">
                  Source deleted. Unlink the node in each map below.
                </p>{/if}
              {#if netNode.kind === "job" && !netNode.live?.missing}<p>
                  Company: {netNode.live?.company ?? "Unavailable"}
                </p>
                <p>
                  Application: {netNode.live?.status ??
                    "No application recorded"}
                </p>
                <p>
                  Queue: {netNode.live?.queue_state ?? "Unavailable"}{netNode
                    .live?.closed_at
                    ? " / closed"
                    : ""}
                </p>{/if}{/if}
            {#if safeUrl(netNode.url)}<a
                href={safeUrl(netNode.url)}
                target="_blank"
                rel="noopener noreferrer">Open safe source ↗</a
              >{/if}
            <h3>Appears in</h3>
            <ul class="members">
              {#each netNode.members as m (`${m.map_id}:${m.node_id}`)}<li>
                  <strong>{mapTitle(m.map_id)}</strong>
                  <small>as “{m.label}”</small>
                  {#if m.notes}<p class="notes">{m.notes}</p>{/if}
                  <Button
                    onclick={() => editInMap(m.map_id, m.node_id)}
                    disabled={busy}>Edit in 2D editor</Button
                  >
                </li>{/each}
            </ul>
            <h3>Connections</h3>
            <ul class="members">
              {#each links.out as e (e.id)}<li>
                  → {e.label || "(unlabeled)"} → {netLabel(e.to)}
                  <small>×{e.weight} in {e.maps.map(mapTitle).join(", ")}</small
                  >
                </li>{/each}
              {#each links.in as e (e.id)}<li>
                  ← {e.label || "(unlabeled)"} ← {netLabel(e.from)}
                  <small>×{e.weight} in {e.maps.map(mapTitle).join(", ")}</small
                  >
                </li>{/each}
              {#if !links.out.length && !links.in.length}<li>
                  No connections.
                </li>{/if}
            </ul>
          {:else}<p>
              Select a node in the 3D network or the adjacency table to see
              which maps it appears in, its live source status, and to jump to
              editing it.
            </p>{/if}
        </Panel>
      </aside>
    </div>
  {:else if current}
    <div class="toolbar mapbar">
      <Badge label={`${current.mode.toUpperCase()} / r${current.revision}`} />
      <label
        >Map title<input
          value={current.title}
          maxlength="200"
          disabled={!editable}
          onchange={(e) => {
            remember();
            current!.title = e.currentTarget.value;
          }}
        /></label
      >
      <Button intent="primary" onclick={save} disabled={!editable || !dirty}
        >Save graph + view</Button
      >
      <Button
        onclick={undo}
        disabled={!editable ||
          (!history.length && (!current.can_undo || dirty))}
        >Undo {history.length ? "local" : "last save"}</Button
      >
      <Button onclick={download}>Export {dirty ? "local edits" : "JSON"}</Button
      >
      <Button onclick={() => current && open(current.id)} disabled={busy}
        >Reload</Button
      >
      <Button onclick={removeMap} disabled={!editable}>Delete map…</Button>
      <span role="status"
        >{busy
          ? "Working…"
          : blocked
            ? "Edits held safely — export or reload"
            : notice}</span
      >
    </div>
    {#if view === "map3d"}
      <div class="workspace map3d-workspace">
        <main class="graph-pane">
          <SchemaNetwork
            title={`3D map / ${current.title}`}
            nodes={mapGraph.nodes}
            links={mapGraph.links}
            bind:layout={mapLayout}
            selected={selected || null}
            onSelect={(id) => {
              const n = current?.graph.nodes.find((n) => n.id === id);
              if (n) choose(n);
            }}
          />
          {#if dirty}<p class="hint">
              Showing unsaved local edits from the 2D editor.
            </p>{/if}
        </main>
        <aside>
          <Panel title="[03] Node inspector / read-only">
            {#if node}
              <h2>{name(node)}</h2>
              <Badge label={node.kind} />
              <p>{degreeOf(node.id)} connections in this map</p>
              {#if node.source}<p>
                  Linked {node.source.kind} #{node.source.id}
                </p>
                {#if current.live[node.id]?.missing}<p class="error">
                    Source deleted. Unlink this node in the 2D editor.
                  </p>{/if}{#if node.kind === "job" && !current.live[node.id]?.missing}<p
                  >
                    Company: {current.live[node.id]?.company ?? "Unavailable"}
                  </p>
                  <p>
                    Application: {current.live[node.id]?.status ??
                      "No application recorded"}
                  </p>{/if}{/if}
              {#if node.notes}<p class="notes">{node.notes}</p>{/if}
              {#if safeUrl((node.source ? current.live[node.id]?.url : undefined) ?? node.url)}<a
                  href={safeUrl(
                    (node.source ? current.live[node.id]?.url : undefined) ??
                      node.url,
                  )}
                  target="_blank"
                  rel="noopener noreferrer">Open safe source ↗</a
                >{/if}
              <h3>Connections</h3>
              <ul class="members">
                {#each current.graph.edges.filter((e) => e.from === node.id || e.to === node.id) as e (e.id)}
                  {@const other = current.graph.nodes.find(
                    (n) => n.id === (e.from === node.id ? e.to : e.from),
                  )!}
                  <li>
                    {e.from === node.id
                      ? `→ ${e.label || "(unlabeled)"} → ${e.to === node.id ? "itself" : name(other)}`
                      : `← ${e.label || "(unlabeled)"} ← ${name(other)}`}
                  </li>
                {:else}<li>No connections.</li>{/each}
              </ul>
              <Button
                intent="primary"
                onclick={() => editInMap(current!.id, node.id)}
                >Edit in 2D editor</Button
              >
            {:else}<p>
                Select a node in 3D or the adjacency table. Editing happens in
                the 2D editor.
              </p>{/if}
          </Panel>
        </aside>
      </div>
    {:else}
      <div class="workspace">
        <aside>
          <Panel title="[01] Nodes + sources">
            <label
              >Node type<select bind:value={kind} disabled={!editable}
                ><option value="topic">Topic</option><option value="class"
                  >Existing study class</option
                ><option value="idea">Idea</option><option value="skill"
                  >Skill / learning goal</option
                ><option value="company">Company (unlinked)</option><option
                  value="job">Job (unlinked note)</option
                ></select
              ></label
            >
            {#if kind === "class"}<label
                >Class<select bind:value={subjectId} disabled={!editable}
                  ><option value="">Choose existing class</option
                  >{#each subjects as s}<option value={s.id}>{s.name}</option
                    >{/each}</select
                ></label
              >
              <p class="hint">
                Classes are linked to Study, never duplicated. Create new
                classes in Study first.
              </p>
            {:else}<label
                >Label<input
                  bind:value={label}
                  maxlength="200"
                  disabled={!editable}
                  onkeydown={(e) => {
                    if (e.key === "Enter") addNode();
                  }}
                /></label
              >{/if}
            <Button
              onclick={addNode}
              disabled={!editable || current.graph.nodes.length >= 300}
              >Add node</Button
            >
            {#if current.mode === "jobs"}<Button
                onclick={() => {
                  showJobs = !showJobs;
                  if (showJobs) void findJobs();
                }}
                disabled={busy}>Import tracked jobs…</Button
              >
              <p class="hint">
                Imports only factual job → at → company links. Add your own
                skills or connect study classes/topics. No eligibility claims.
              </p>{/if}
            <label
              >Search nodes<input
                bind:value={search}
                type="search"
                placeholder="Class, topic, idea…"
              /></label
            >
            <ul class="node-list">
              {#each filtered as n}<li>
                  <button
                    class:active={selected === n.id}
                    onclick={() => choose(n)}
                    ><span>{name(n)}</span><small
                      >{n.kind}{n.source ? " / linked" : ""}</small
                    ></button
                  >
                </li>{/each}
            </ul>
            <p class="hint">
              {current.graph.nodes.length}/300 nodes · {current.graph.edges
                .length}/600 connections
            </p>
          </Panel>
        </aside>
        <main class="graph-pane">
          <div class="toolbar">
            <Button onclick={() => zoom(1.2)} disabled={!editable}
              >Zoom +</Button
            ><Button onclick={() => zoom(1 / 1.2)} disabled={!editable}
              >Zoom −</Button
            ><Button onclick={fit} disabled={!editable}>Fit graph</Button
            ><Button
              onclick={() => arrange("force")}
              disabled={!editable || !current.graph.nodes.length}
              title="Spread nodes with a force-directed layout. Undo restores positions."
              >Auto-layout: force</Button
            ><Button
              onclick={() => arrange("tree")}
              disabled={!editable || !current.graph.nodes.length}
              title="Arrange as a layered tree following connection direction. Undo restores positions."
              >Auto-layout: tree</Button
            ><span>{Math.round(current.viewport.zoom * 100)}%</span>
          </div>
          <svg
            bind:this={stage}
            class="stage"
            aria-label="Schema graph. Drag nodes to position; drag empty space to pan. Use node list and inspector for keyboard editing."
            role="group"
            onpointerdown={pick}
            onpointermove={move}
            onpointerup={(e) => end(e)}
            onpointercancel={(e) => end(e, true)}
            onlostpointercapture={(e) => end(e, true)}
            onwheel={(e) => {
              e.preventDefault();
              zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX, e.clientY);
            }}
          >
            <defs
              ><marker
                id="schema-arrow"
                markerWidth="10"
                markerHeight="8"
                refX="9"
                refY="4"
                orient="auto"
                markerUnits="strokeWidth"
                ><path d="M0,0 L10,4 L0,8 z" fill="var(--violet)" /></marker
              ><marker
                id="schema-arrow-active"
                markerWidth="10"
                markerHeight="8"
                refX="9"
                refY="4"
                orient="auto"
                markerUnits="strokeWidth"
                ><path d="M0,0 L10,4 L0,8 z" fill="var(--accent)" /></marker
              ></defs
            >
            <g
              transform={`translate(${current.viewport.x},${current.viewport.y}) scale(${current.viewport.zoom})`}
            >
              {#each current.graph.edges as e (e.id)}
                {@const shape = geometry.get(e.id)}
                {#if shape}<path
                    d={shape.d}
                    class="edge"
                    class:active={selectedEdge === e.id}
                    marker-end={selectedEdge === e.id
                      ? "url(#schema-arrow-active)"
                      : "url(#schema-arrow)"}
                  /><path d={shape.d} class="edge-hit" data-edge={e.id} />{/if}
              {/each}
              {#each current.graph.nodes as n (n.id)}
                <g
                  transform={`translate(${n.x},${n.y})`}
                  data-node={n.id}
                  role="button"
                  tabindex="0"
                  aria-label={`${n.kind}: ${name(n)}. Arrow keys move; Shift moves faster; Enter opens inspector; Delete removes with undo.`}
                  class:selected={selected === n.id}
                  class="graph-node"
                  onpointerdown={(e) => start(e, n.id)}
                  onkeydown={(e) => nodeKey(e, n)}
                  onclick={() => choose(n)}
                >
                  <rect width="200" height="64" />
                  <text x="10" y="20" class="node-kind"
                    >{n.kind.toUpperCase()} {n.source ? "↗" : ""}</text
                  >
                  <text x="10" y="43"
                    >{name(n).length > 24
                      ? name(n).slice(0, 23) + "…"
                      : name(n)}</text
                  >
                </g>
              {/each}
              {#each current.graph.edges as e (e.id)}
                {@const shape = geometry.get(e.id)}
                {#if shape && e.label}<text
                    x={shape.lx}
                    y={shape.ly}
                    class="edge-label"
                    class:active={selectedEdge === e.id}
                    data-edge={e.id}
                    text-anchor="middle"
                    >{e.label.length > 32
                      ? e.label.slice(0, 31) + "…"
                      : e.label}</text
                  >{/if}
              {/each}
            </g>
          </svg>
          <p class="hint">
            Mouse, touch and pen: drag nodes; drag blank space to pan; click a
            connection or its label to edit it below. Wheel or buttons zoom. Tab
            to a node, Enter to inspect, arrows to move, Delete to remove.
            Auto-layout is applied only when you choose it and can be undone.
            Save persists positions and viewport.
          </p>
          <Panel title="[02] Directional connections">
            <div class="toolbar">
              <label
                >From<select bind:value={edgeFrom} disabled={!editable}
                  ><option value="">Choose node</option
                  >{#each current.graph.nodes as n}<option value={n.id}
                      >{name(n)}</option
                    >{/each}</select
                ></label
              ><span>→</span><label
                >To<select bind:value={edgeTo} disabled={!editable}
                  ><option value="">Choose node</option
                  >{#each current.graph.nodes as n}<option value={n.id}
                      >{name(n)}</option
                    >{/each}</select
                ></label
              ><label
                >Relationship<input
                  bind:value={edgeLabel}
                  maxlength="200"
                  disabled={!editable}
                /></label
              ><Button
                onclick={connect}
                disabled={!editable || !edgeFrom || !edgeTo}>Connect</Button
              >
            </div>
            <ul class="edges">
              {#each current.graph.edges as e}<li>
                  <button
                    onclick={() => {
                      selectedEdge = selectedEdge === e.id ? "" : e.id;
                    }}
                    >{name(current.graph.nodes.find((n) => n.id === e.from)!)} → {e.label ||
                      "(unlabeled)"} → {name(
                      current.graph.nodes.find((n) => n.id === e.to)!,
                    )}</button
                  >{#if selectedEdge === e.id}<div class="edge-edit">
                      <label
                        >From<select
                          value={e.from}
                          onchange={(event) =>
                            editEdge(e.id, "from", event.currentTarget.value)}
                          disabled={!editable}
                          >{#each current.graph.nodes as n}<option value={n.id}
                              >{name(n)}</option
                            >{/each}</select
                        ></label
                      ><label
                        >To<select
                          value={e.to}
                          onchange={(event) =>
                            editEdge(e.id, "to", event.currentTarget.value)}
                          disabled={!editable}
                          >{#each current.graph.nodes as n}<option value={n.id}
                              >{name(n)}</option
                            >{/each}</select
                        ></label
                      ><label
                        >Label<input
                          value={e.label}
                          maxlength="200"
                          onchange={(event) =>
                            editEdge(e.id, "label", event.currentTarget.value)}
                          disabled={!editable}
                        /></label
                      ><Button
                        onclick={() => removeEdge(e.id)}
                        disabled={!editable}>Remove connection</Button
                      >
                    </div>{/if}
                </li>{/each}
            </ul>
          </Panel>
        </main>
        <aside>
          <Panel title="[03] Readable inspector">
            {#if node}
              <h2>{name(node)}</h2>
              <Badge label={node.kind} />
              {#if node.source}<p>
                  Linked {node.source.kind} #{node.source.id}
                </p>
                {#if current.live[node.id]?.missing}<p class="error">
                    Source deleted. Unlink this node before saving.
                  </p>{/if}{#if node.kind === "job"}<p>
                    Company: {current.live[node.id]?.company ?? "Unavailable"}
                  </p>
                  <p>
                    Application: {current.live[node.id]?.status ??
                      "No application recorded"}
                  </p>
                  <p>
                    Queue: {current.live[node.id]?.queue_state ??
                      "Unavailable"}{current.live[node.id]?.closed_at
                      ? " / closed"
                      : ""}
                  </p>{/if}
                <p class="hint">
                  Source title/status refresh on reload. Map notes do not modify
                  source records.
                </p>
                <Button onclick={unlink} disabled={!editable}
                  >Unlink / keep as map note</Button
                >{/if}
              <label
                >{node.source ? "Local fallback label" : "Label"}<input
                  bind:value={draftLabel}
                  maxlength="200"
                  disabled={!editable}
                /></label
              >
              <label
                >Notes<textarea
                  bind:value={draftNotes}
                  maxlength="10000"
                  rows="7"
                  disabled={!editable}
                ></textarea></label
              >
              <label
                >Local reference URL<input
                  bind:value={draftUrl}
                  type="url"
                  maxlength="4000"
                  disabled={!editable}
                  placeholder="https://…"
                /></label
              >
              {#if safeUrl((node.source ? current.live[node.id]?.url : undefined) ?? node.url)}<a
                  href={safeUrl(
                    (node.source ? current.live[node.id]?.url : undefined) ??
                      node.url,
                  )}
                  target="_blank"
                  rel="noopener noreferrer">Open safe source ↗</a
                >{/if}
              <Button
                onclick={updateNode}
                disabled={!editable || !draftLabel.trim()}
                >Apply node edits</Button
              >
              <div class="toolbar">
                <Button
                  onclick={() => {
                    edgeFrom = node.id;
                  }}
                  disabled={!editable}>Use as From</Button
                ><Button
                  onclick={() => {
                    edgeTo = node.id;
                  }}
                  disabled={!editable}>Use as To</Button
                >
              </div>
              <Button onclick={deleteNode} disabled={!editable}
                >Delete node + its connections</Button
              >
            {:else}<p>
                Select a node in the graph or searchable list. Full labels,
                notes, safe source links and live job status appear here.
              </p>{/if}
            <p class="hint">
              Undo keeps up to 30 local actions and one previous saved snapshot.
              Export is portable: linked local IDs are detached to avoid linking
              unrelated records on another installation. Import tracked jobs or
              add classes to link real sources again.
            </p>
          </Panel>
        </aside>
      </div>
    {/if}
    {#if showJobs && current.mode === "jobs"}<Panel
        title="Import selected tracked jobs / factual sources only"
        ><div class="toolbar">
          <label
            >Search title or company<input
              bind:value={jobSearch}
              maxlength="200"
              onkeydown={(e) => {
                if (e.key === "Enter") void findJobs();
              }}
            /></label
          ><Button onclick={() => findJobs()} disabled={busy}>Search</Button>
        </div>
        {#if dirty}<p class="error">
            Save your graph before importing tracked jobs.
          </p>{/if}
        <ul class="jobs">
          {#each jobs as job}<li>
              <label
                ><input
                  type="checkbox"
                  checked={jobIds.includes(job.id)}
                  disabled={busy ||
                    (!jobIds.includes(job.id) && jobIds.length >= 50)}
                  onchange={(e) => {
                    jobIds = e.currentTarget.checked
                      ? [...jobIds, job.id]
                      : jobIds.filter((id) => id !== job.id);
                  }}
                /><span
                  >{job.title} / {job.company}<small
                    >{job.status ?? "No application"} · {job.queue_state}</small
                  ></span
                ></label
              >
            </li>{/each}
        </ul>
        <div class="toolbar">
          <Button
            onclick={() => findJobs(Math.max(0, jobOffset - 50))}
            disabled={busy || jobOffset === 0}>Previous</Button
          ><Button
            onclick={() => findJobs(jobOffset + 50)}
            disabled={busy || !moreJobs}>Next</Button
          ><Button
            intent="primary"
            onclick={importJobs}
            disabled={!editable || dirty || !jobIds.length}
            >Import {jobIds.length} selected jobs</Button
          >
        </div>
        <p class="hint">
          Repeated imports reuse the same job/company nodes and “at”
          connections. Application statuses remain live; skills and requirements
          are never invented.
        </p></Panel
      >{/if}
  {:else}<p class="empty">
      Create a study, general or job-focused map — or import a portable JSON
      map.
    </p>{/if}
</section>

<style>
  .schema {
    display: grid;
    gap: 14px;
    color: var(--ink);
    font-family: var(--br-font-ui, monospace);
  }
  .intro,
  .hint {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.65;
  }
  .intro {
    margin: 0 0 12px;
  }
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 8px;
  }
  .mapbar {
    padding: 12px;
    border: 1px solid var(--border);
    background: var(--surface);
  }
  .mapbar span {
    font-size: 11px;
    color: var(--muted);
    align-self: center;
  }
  label {
    display: grid;
    gap: 5px;
    font-size: 11px;
    min-width: 0;
    color: var(--muted);
  }
  input,
  select,
  textarea {
    width: 100%;
    min-width: 0;
    border: 1px solid var(--border);
    border-radius: 0;
    background: var(--surface);
    color: var(--ink);
    padding: 8px;
    font: inherit;
  }
  textarea {
    resize: vertical;
  }
  .workspace {
    display: grid;
    grid-template-columns: minmax(200px, 235px) minmax(320px, 1fr) minmax(
        210px,
        260px
      );
    gap: 12px;
    align-items: start;
  }
  aside {
    min-width: 0;
  }
  aside :global(.br-panel-body) {
    display: grid;
    gap: 10px;
  }
  aside label {
    margin: 10px 0;
  }
  h2 {
    font-size: 16px;
    overflow-wrap: anywhere;
  }
  .graph-pane {
    min-width: 0;
  }
  .stage {
    display: block;
    width: 100%;
    height: 510px;
    margin: 8px 0;
    border: 1px solid var(--border);
    background-color: var(--surface);
    background-image: radial-gradient(var(--border) 1px, transparent 1px);
    background-size: 20px 20px;
    touch-action: none;
    user-select: none;
    cursor: grab;
  }
  .graph-node {
    cursor: move;
  }
  .graph-node rect {
    fill: var(--surface);
    stroke: var(--border);
    stroke-width: 2;
  }
  .graph-node.selected rect,
  .graph-node:focus rect {
    stroke: var(--accent);
    stroke-width: 3;
  }
  .graph-node:focus {
    outline: none;
  }
  .graph-node text {
    fill: var(--ink);
    font: 12px var(--br-font-ui, monospace);
    pointer-events: none;
  }
  .graph-node .node-kind {
    fill: var(--violet);
    font-size: 9px;
  }
  .edge {
    fill: none;
    stroke: var(--violet);
    stroke-width: 2;
    pointer-events: none;
  }
  .edge.active {
    stroke: var(--accent);
    stroke-width: 3;
  }
  .edge-hit {
    fill: none;
    stroke: transparent;
    stroke-width: 14;
    pointer-events: stroke;
    cursor: pointer;
  }
  .edge-label {
    fill: var(--ink);
    font: 10px var(--br-font-ui, monospace);
    paint-order: stroke;
    stroke: var(--surface);
    stroke-width: 5px;
    cursor: pointer;
  }
  .edge-label.active {
    fill: var(--accent);
  }
  .views {
    align-items: center;
  }
  .views .hint {
    margin: 0;
  }
  .map3d-workspace {
    grid-template-columns: minmax(320px, 1fr) minmax(220px, 280px);
  }
  .network-workspace {
    grid-template-columns: minmax(0, 1fr) minmax(230px, 290px);
  }
  .network-workspace > main {
    grid-column: 1;
    grid-row: 1 / span 2;
  }
  .network-workspace > aside {
    grid-column: 2;
  }
  fieldset {
    border: 1px solid var(--border);
    margin: 0;
    padding: 10px;
    display: grid;
    gap: 6px;
    max-height: 260px;
    overflow: auto;
  }
  legend {
    font-size: 11px;
    color: var(--muted);
    padding: 0 4px;
  }
  aside label.check {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    color: var(--ink);
  }
  label.check input {
    width: auto;
  }
  label.check small {
    display: inline;
    margin: 0;
  }
  h3 {
    font-size: 12px;
    color: var(--violet);
    margin: 12px 0 4px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .members,
  .clusters {
    list-style: none;
    padding: 0;
    margin: 0;
    display: grid;
    gap: 6px;
    font-size: 12px;
    max-height: 340px;
    overflow: auto;
  }
  .clusters {
    color: var(--muted);
    font-size: 11px;
  }
  .members li {
    border: 1px solid var(--border);
    padding: 8px;
    overflow-wrap: anywhere;
    display: grid;
    gap: 6px;
  }
  .notes {
    white-space: pre-wrap;
    color: var(--muted);
    font-size: 12px;
    margin: 4px 0;
    overflow-wrap: anywhere;
  }
  .node-list,
  .edges,
  .jobs {
    list-style: none;
    padding: 0;
    margin: 10px 0;
    max-height: 320px;
    overflow: auto;
  }
  .node-list button,
  .edges > li > button {
    width: 100%;
    text-align: left;
    border: 1px solid var(--border);
    border-radius: 0;
    background: var(--surface);
    color: var(--ink);
    padding: 9px;
    margin-bottom: 4px;
    font: inherit;
    overflow-wrap: anywhere;
    cursor: pointer;
  }
  .node-list button.active {
    border-color: var(--accent);
  }
  small {
    display: block;
    color: var(--muted);
    margin-top: 5px;
    font-size: 10px;
  }
  .edge-edit {
    display: grid;
    gap: 8px;
    padding: 10px;
    border: 1px solid var(--violet);
    margin-bottom: 10px;
  }
  .error {
    padding: 12px;
    border: 1px solid var(--accent);
    color: var(--ink);
    overflow-wrap: anywhere;
  }
  .file input {
    max-width: 210px;
  }
  .jobs li {
    padding: 7px;
    border-bottom: 1px solid var(--border);
  }
  .jobs label {
    display: flex;
    align-items: start;
    gap: 10px;
  }
  .jobs input {
    width: auto;
    margin-top: 3px;
  }
  a {
    color: var(--violet);
    overflow-wrap: anywhere;
    display: block;
    margin: 10px 0;
  }
  .empty {
    padding: 40px;
    border: 1px solid var(--border);
    color: var(--muted);
    text-align: center;
  }
  @media (max-width: 1200px) {
    .workspace {
      grid-template-columns: 210px minmax(300px, 1fr);
    }
    .map3d-workspace,
    .network-workspace {
      grid-template-columns: minmax(0, 1fr);
    }
    .workspace > aside:last-child {
      grid-column: 1 / -1;
    }
    .network-workspace > aside,
    .network-workspace > main {
      grid-column: 1;
      grid-row: auto;
    }
  }
  @media (max-width: 760px) {
    .workspace,
    .network-workspace {
      grid-template-columns: minmax(0, 1fr);
    }
    .workspace > aside:last-child,
    .network-workspace > aside,
    .network-workspace > main {
      grid-column: auto;
      grid-row: auto;
    }
    .stage {
      height: 430px;
    }
  }
</style>
