import { forceSimulation, forceManyBody, forceLink, forceX, forceY, forceCollide, type SimulationNodeDatum, type SimulationLinkDatum } from "d3-force";
import { select } from "d3-selection";
import { zoom, zoomIdentity, type ZoomTransform } from "d3-zoom";
import { DEFAULT_PHYSICS, PHYSICS_LIMITS, type GraphNode, type GraphOptions, type GraphRenderer } from "./graph3d";

type Node = SimulationNodeDatum & { id: string; data: GraphNode; radius: number; spiked: boolean; color: string; tx: number; ty: number };
type Link = SimulationLinkDatum<Node> & { directed?: boolean; label?: string; weight?: number };
type Point = { x: number; y: number };
type Box = { x: number; y: number; w: number; h: number };

/** A node with at least this many links is a hub: it carries a four-point spike. */
const HUB_LINKS = 4;
/** Groups take this many lightness steps of one hue at most; more groups repeat them. */
const MAX_STEPS = 6;
/** Where a name may sit round its point, in order of preference: over it first. */
const LABEL_DIRECTIONS: [number, number][] = [[0, -1], [0, 1], [1, 0], [-1, 0], [0.75, 0.66], [-0.75, 0.66], [0.75, -0.66], [-0.75, -0.66]];
/** How many points get a name at most. */
const NAMED = 60;
const overlaps = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/** Mixes two #rrggbb colours; anything else falls back to `a`. */
function mixHex(a: string, b: string, amount: number): string {
  const parse = (hex: string) => (/^#[0-9a-f]{6}$/i.test(hex) ? [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)) : null);
  const [from, to] = [parse(a), parse(b)];
  if (!from || !to) return a;
  return `#${from.map((value, i) => Math.round(value + (to[i] - value) * amount).toString(16).padStart(2, "0")).join("")}`;
}

/**
 * The 2D map (design labs, 2D nodes): a constellation. Every node is a point sized by how
 * linked it is, hubs carry a crisp four-point spike, groups are lightness steps of one
 * hue, links inside a group are thin lines and links across groups show only for the
 * node in focus, and names show only for the focused node and its neighbours.
 * Fresh rika! Canvas renderer; no LearnHarness source or assets are used.
 */
export async function mountGraph2D(el: HTMLElement, opts: GraphOptions): Promise<GraphRenderer> {
  await document.fonts?.ready;
  const canvas = document.createElement("canvas");
  canvas.dataset.graphLook = "2d";
  canvas.setAttribute("aria-hidden", "true");
  canvas.style.cssText = "display:block;width:100%;height:100%;touch-action:none";
  const ctx = canvas.getContext("2d")!;
  el.replaceChildren(canvas);
  let nodes: Node[] = [];
  let links: Link[] = [];
  let byId = new Map<string, Node>();
  /** Links per node: sizes its point and decides whether it is a hub. */
  let degree = new Map<string, number>();
  const groups = new Map<string, { nodes: Node[]; color: string }>();
  const groupOrder: string[] = [];
  let layout = opts.layout;
  const physics = { ...DEFAULT_PHYSICS, ...opts.physics };
  let width = 1, height = 1, dpr = 1;
  let transform = zoomIdentity;
  /** The view still shows the automatic whole-graph fit (no user pan/zoom or node focus since). */
  let framed = false;
  let selected: string | null = null;
  let hovered: string | null = null;
  let active = opts.active ?? true;
  let intersecting = true;
  let destroyed = false;
  let frame = 0;
  let ink = "", chrome = "", muted = "", font = "", hue = "";
  let palette: string[] = [];
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const simulation = forceSimulation<Node>().stop().alphaDecay(0.035);
  const selection = select(canvas);
  let drag: { id: string | null; pointer: number; start: Point; moved: boolean } | null = null;
  const visible = () => !destroyed && active && intersecting && !document.hidden && el.clientWidth > 0 && el.clientHeight > 0;

  /** Any CSS colour as #rrggbb (the canvas serialises opaque colours that way). */
  function hex(value: string, fallback: string) {
    ctx.fillStyle = fallback; ctx.fillStyle = value || fallback;
    const read = String(ctx.fillStyle);
    return /^#[0-9a-f]{6}$/i.test(read) ? read : fallback;
  }
  function colors() {
    const style = getComputedStyle(el);
    ink = hex(style.getPropertyValue("--ink").trim(), "#e7dfef");
    chrome = hex(style.getPropertyValue("--chrome").trim(), "#0e0c14");
    muted = hex(style.getPropertyValue("--muted").trim(), "#8f86a0");
    hue = hex(style.getPropertyValue("--c1").trim(), ink);
    font = style.getPropertyValue("--br-font-ui").trim() || "sans-serif";
    tint();
    requestDraw();
  }
  /** One hue in lightness steps, from near the ink down to half way to the page: a step per group. */
  function tint() {
    const steps = Math.min(MAX_STEPS, Math.max(2, groupOrder.length));
    const top = mixHex(hue, ink, 0.72), bottom = mixHex(hue, chrome, 0.5);
    palette = Array.from({ length: steps }, (_, step) => mixHex(top, bottom, step / (steps - 1)));
    for (const [name, group] of groups) {
      group.color = palette[groupOrder.indexOf(name) % steps];
      for (const node of group.nodes) node.color = group.color;
    }
  }
  const behavior = zoom<HTMLCanvasElement, unknown>()
    .extent(() => [[0, 0], [width, height]])
    .scaleExtent([0.12, 8])
    .filter(event => {
      if (!visible()) return false;
      // Leave node gestures to the pointer handler; background gestures pan.
      if (event.type === "mousedown") return !event.button && !handle.nodeAt(event.clientX, event.clientY);
      if (event.type === "touchstart" && event.touches.length === 1) return !handle.nodeAt(event.touches[0].clientX, event.touches[0].clientY);
      return !event.button;
    })
    .on("zoom", event => { transform = event.transform as ZoomTransform; if (event.sourceEvent) framed = false; requestDraw(); });
  selection.call(behavior);

  function requestDraw() {
    if (frame || !visible()) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (!visible()) return;
      if (!motion.matches && !opts.reducedMotion && simulation.alpha() > simulation.alphaMin() && !drag) {
        simulation.tick();
      }
      draw();
      if (!motion.matches && !opts.reducedMotion && simulation.alpha() > simulation.alphaMin()) requestDraw();
    });
  }
  function targets() {
    const names = [...groups.keys()];
    const reach = Math.max(100, Math.sqrt(nodes.length) * 43);
    const levels = new Map<string, number>();
    if (layout === "tree") {
      const degree = new Map(nodes.map(node => [node.id, 0]));
      const neighbors = new Map(nodes.map(node => [node.id, [] as string[]]));
      for (const link of links) {
        const a = typeof link.source === "string" ? link.source : (link.source as Node).id;
        const b = typeof link.target === "string" ? link.target : (link.target as Node).id;
        degree.set(a, degree.get(a)! + 1); degree.set(b, degree.get(b)! + 1);
        neighbors.get(a)!.push(b); neighbors.get(b)!.push(a);
      }
      const roots = [...nodes].sort((a, b) => degree.get(b.id)! - degree.get(a.id)!);
      for (const root of roots) {
        if (levels.has(root.id)) continue;
        levels.set(root.id, 0);
        const queue = [root.id];
        for (let i = 0; i < queue.length; i++) for (const id of neighbors.get(queue[i])!) {
          if (levels.has(id)) continue;
          levels.set(id, levels.get(queue[i])! + 1); queue.push(id);
        }
      }
    }
    for (const [name, group] of groups) group.nodes.forEach((node, index) => {
      const angle = index * Math.PI * (3 - Math.sqrt(5));
      const groupAngle = names.indexOf(name) / names.length * Math.PI * 2 - Math.PI / 2;
      if (layout === "clusters") {
        const radius = names.length === 1 ? 0 : reach;
        node.tx = Math.cos(groupAngle) * radius;
        node.ty = Math.sin(groupAngle) * radius;
      } else if (layout === "tree" || layout === "sphere") {
        const radius = layout === "tree" ? (levels.get(node.id) ?? 0) * 90 : node.data.kind === "hub" ? reach * 0.45 : reach;
        const theta = nodes.indexOf(node) / nodes.length * Math.PI * 2;
        node.tx = Math.cos(theta) * radius; node.ty = Math.sin(theta) * radius;
      } else { node.tx = 0; node.ty = 0; }
      if (node.x === undefined) {
        node.x = node.tx + Math.cos(angle) * (20 + Math.sqrt(index) * 22);
        node.y = node.ty + Math.sin(angle) * (20 + Math.sqrt(index) * 22);
      }
    });
  }
  function forces() {
    simulation.force("charge", forceManyBody<Node>().strength(-physics.repulsion * 1.6).distanceMax(nodes.length >= 400 ? 400 : 320))
      .force("link", forceLink<Node, Link>(links).id(node => node.id)
        .distance(link => layout === "clusters" && (link.source as Node).data.group !== (link.target as Node).data.group ? 190 : 58)
        .strength(link => physics.spring * (layout === "clusters" && (link.source as Node).data.group !== (link.target as Node).data.group ? 0.025 : 0.4)))
      .force("x", forceX<Node>(node => node.tx).strength(layout === "force" ? 0.015 : 0.15))
      .force("y", forceY<Node>(node => node.ty).strength(layout === "force" ? 0.015 : 0.15))
      .force("collide", forceCollide<Node>(node => node.radius * (node.spiked ? 3.4 : 1.6) + 9))
      .velocityDecay(physics.damping).alpha(1).stop();
    simulation.tick(240);
    simulation.alpha(0);
    requestDraw();
  }
  function fit() {
    framed = true;
    if (!nodes.length) { selection.call(behavior.transform, zoomIdentity); return; }
    const minX = Math.min(...nodes.map(node => node.x!)), maxX = Math.max(...nodes.map(node => node.x!));
    const minY = Math.min(...nodes.map(node => node.y!)), maxY = Math.max(...nodes.map(node => node.y!));
    // Points and the names beside them keep their pixel size; leave room for both.
    const pad = 72;
    const k = Math.max(0.12, Math.min(2.4, Math.max(1, width - 2 * pad) / Math.max(1, maxX - minX), Math.max(1, height - 2 * pad) / Math.max(1, maxY - minY)));
    selection.call(behavior.transform, zoomIdentity.translate(width / 2, height / 2).scale(k).translate(-(minX + maxX) / 2, -(minY + maxY) / 2));
  }
  /** A point's radius on screen: it grows gently with zoom, never as fast as the map. */
  const sizeOf = (node: Node) => node.radius * Math.min(2.2, Math.max(0.8, Math.sqrt(transform.k)));
  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const focus = hovered ?? selected;
    const neighbors = new Set<string>();
    if (focus) for (const link of links) {
      const a = link.source as Node, b = link.target as Node;
      if (a.id === focus) neighbors.add(b.id);
      if (b.id === focus) neighbors.add(a.id);
    }
    const at = new Map(nodes.map(node => [node.id, transform.apply([node.x!, node.y!])] as const));
    // Links: straight thin lines. At rest only those inside a group; the focused node shows all of its own.
    ctx.lineCap = "round";
    for (const link of links) {
      const a = link.source as Node, b = link.target as Node;
      const same = a.data.group === b.data.group, touching = !!focus && (a.id === focus || b.id === focus);
      if (!same && !touching) continue;
      const [ax, ay] = at.get(a.id)!, [bx, by] = at.get(b.id)!;
      // Solid enough to stay a line under compositor upscaling (thin translucent hairlines smear).
      ctx.globalAlpha = touching ? 1 : focus ? 0.16 : 0.62;
      ctx.strokeStyle = same ? mixHex(a.color, ink, 0.2) : muted;
      ctx.lineWidth = touching ? 1.6 : 1.3;
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
      if (link.directed) {
        const angle = Math.atan2(by - ay, bx - ax), inset = sizeOf(b) + 3, size = 6;
        const tx = bx - Math.cos(angle) * inset, ty = by - Math.sin(angle) * inset;
        ctx.fillStyle = ctx.strokeStyle;
        ctx.beginPath(); ctx.moveTo(tx, ty);
        ctx.lineTo(tx - Math.cos(angle - 0.42) * size, ty - Math.sin(angle - 0.42) * size);
        ctx.lineTo(tx - Math.cos(angle + 0.42) * size, ty - Math.sin(angle + 0.42) * size);
        ctx.closePath(); ctx.fill();
      }
    }
    // Points: plain ones first, hubs over them, the focused one last.
    const rank = (node: Node) => (node.id === focus ? 2 : 0) + (node.spiked ? 1 : 0);
    for (const node of [...nodes].sort((a, b) => rank(a) - rank(b))) {
      const [x, y] = at.get(node.id)!;
      const lit = !focus || node.id === focus || neighbors.has(node.id);
      const r = sizeOf(node) * (node.id === focus ? 1.25 : 1);
      ctx.globalAlpha = lit ? 1 : 0.22;
      ctx.fillStyle = node.color;
      if (node.spiked) {
        // Four long points and four waists between them: a star with no glow.
        const long = r * 2.8 + 2, waist = r * 0.38;
        ctx.beginPath();
        for (let i = 0; i < 8; i++) {
          const angle = (i * Math.PI) / 4 - Math.PI / 2, reach = i % 2 ? waist : long;
          ctx.lineTo(x + Math.cos(angle) * reach, y + Math.sin(angle) * reach);
        }
        ctx.closePath(); ctx.fill();
      }
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
      if (node.id === focus) {
        ctx.strokeStyle = ink; ctx.lineWidth = 1.25;
        ctx.beginPath(); ctx.arc(x, y, r + 4, 0, Math.PI * 2); ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
    names(focus, neighbors, at);
  }
  /**
   * Every point carries its name, over it when there is room: the first of eight directions
   * (three rings out) that covers neither a point nor another name. A name with no free spot
   * is skipped, so the order matters: the focused node, its neighbours, hubs, then the rest
   * (at most NAMED of them, which keeps a crowded map legible and a frame cheap). With a
   * focus, names outside its neighbourhood are dimmed with their points.
   */
  function names(focus: string | null, neighbors: Set<string>, at: Map<string, [number, number]>) {
    ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.lineJoin = "round";
    const points: Box[] = nodes.map(node => {
      const [x, y] = at.get(node.id)!, r = sizeOf(node) * (node.spiked ? 2.8 : 1) + 3;
      return { x: x - r, y: y - r, w: 2 * r, h: 2 * r };
    });
    const placed: Box[] = [];
    const order = (node: Node) => (node.id === focus ? 0 : neighbors.has(node.id) ? 1 : node.spiked ? 2 : 3);
    const shown = [...nodes].sort((a, b) => order(a) - order(b) || sizeOf(b) - sizeOf(a)).slice(0, NAMED);
    for (const node of shown) {
      const main = node.id === focus;
      ctx.font = main ? `600 15px ${font}` : `400 13px ${font}`;
      const text = node.data.label.length > 30 ? `${node.data.label.slice(0, 29)}…` : node.data.label;
      const w = ctx.measureText(text).width + 10, h = main ? 22 : 18;
      const [nx, ny] = at.get(node.id)!;
      const half = sizeOf(node) * (node.spiked ? 2.8 : 1.25) + 4;
      let spot: Box | null = null;
      search: for (let ring = 0; ring < 3; ring++) for (const [dx, dy] of LABEL_DIRECTIONS) {
        const gap = 4 + ring * 18;
        const cx = nx + dx * (half + gap + w / 2 * Math.abs(dx)), cy = ny + dy * (half + gap + h / 2 * Math.abs(dy));
        const box = { x: cx - w / 2, y: cy - h / 2, w, h };
        if (box.x < 6 || box.y < 6 || box.x + w > width - 6 || box.y + h > height - 6) continue;
        if (placed.some(other => overlaps(other, box)) || points.some(other => overlaps(other, box))) continue;
        spot = box; break search;
      }
      if (!spot) continue;
      placed.push(spot);
      // A halo in the page colour keeps the name readable over lines.
      ctx.lineWidth = 5; ctx.strokeStyle = chrome; ctx.globalAlpha = 0.92;
      ctx.strokeText(text, spot.x + w / 2, spot.y + h / 2 + 0.5);
      ctx.globalAlpha = !focus || main || neighbors.has(node.id) ? 1 : 0.3; ctx.fillStyle = main || (!focus && node.spiked) ? ink : muted;
      ctx.fillText(text, spot.x + w / 2, spot.y + h / 2 + 0.5);
      ctx.globalAlpha = 1;
    }
  }
  function hover(node: GraphNode | null) {
    if (hovered === (node?.id ?? null)) return;
    hovered = node?.id ?? null;
    canvas.style.cursor = node ? "pointer" : "grab";
    canvas.title = node ? [node.label, node.group, node.detail].filter(Boolean).join(" · ") : "";
    opts.onHover?.(node); requestDraw();
  }
  const pointerDown = (event: PointerEvent) => {
    if (!visible() || event.button || (drag && drag.pointer !== event.pointerId)) { drag = null; return; }
    const node = handle.nodeAt(event.clientX, event.clientY);
    drag = { id: node?.id ?? null, pointer: event.pointerId, start: { x: event.clientX, y: event.clientY }, moved: false };
    if (node) canvas.setPointerCapture(event.pointerId);
  };
  const pointerMove = (event: PointerEvent) => {
    if (!visible()) return;
    if (drag && drag.pointer === event.pointerId) {
      if (Math.hypot(event.clientX - drag.start.x, event.clientY - drag.start.y) > 4) drag.moved = true;
      const node = drag.id ? byId.get(drag.id) : null;
      if (node && drag.moved) {
        const rect = canvas.getBoundingClientRect();
        const point = transform.invert([event.clientX - rect.left, event.clientY - rect.top]);
        node.x = node.fx = point[0]; node.y = node.fy = point[1]; requestDraw();
      }
    } else hover(handle.nodeAt(event.clientX, event.clientY));
  };
  const pointerUp = (event: PointerEvent) => {
    const gesture = drag;
    if (!gesture || gesture.pointer !== event.pointerId) return;
    drag = null;
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    if (!visible()) return;
    const node = gesture.id ? byId.get(gesture.id) : undefined;
    if (gesture.moved) { if (node) opts.onDragEnd?.(node.data, event); }
    else if (node) { selected = node.id; handle.focus(node.id); opts.onSelect?.(node.data); }
    else { selected = null; requestDraw(); }
  };
  const cancel = () => { drag = null; hover(null); };
  const leave = () => { if (!drag) hover(null); };
  const context = (event: MouseEvent) => {
    const node = handle.nodeAt(event.clientX, event.clientY);
    if (node) opts.onContextMenu?.(node, event);
  };
  canvas.addEventListener("pointerdown", pointerDown);
  canvas.addEventListener("pointermove", pointerMove);
  canvas.addEventListener("pointerup", pointerUp);
  canvas.addEventListener("pointercancel", cancel);
  canvas.addEventListener("pointerleave", leave);
  canvas.addEventListener("contextmenu", context);
  const syncVisibility = () => {
    if (visible()) requestDraw();
    else { cancelAnimationFrame(frame); frame = 0; cancel(); }
  };
  const resize = new ResizeObserver(() => handle.resize());
  const intersection = new IntersectionObserver(entries => { intersecting = entries.some(entry => entry.isIntersecting); syncVisibility(); });
  const handle: GraphRenderer = {
    nodePoint(id) {
      const node = byId.get(id);
      if (destroyed || !node) return null;
      const rect = canvas.getBoundingClientRect();
      const [x, y] = transform.apply([node.x!, node.y!]);
      return { x: x + rect.left, y: y + rect.top };
    },
    nodeAt(x, y) {
      if (!visible()) return null;
      const rect = canvas.getBoundingClientRect();
      if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) return null;
      // A point is hit a little beyond its edge (a hub across its spikes); the nearest wins.
      let closest: Node | null = null, distance = Infinity;
      for (const node of nodes) {
        const [px, py] = transform.apply([node.x!, node.y!]);
        const away = Math.hypot(px + rect.left - x, py + rect.top - y);
        const reach = Math.max(11, sizeOf(node) * (node.spiked ? 2.8 : 1) + 6);
        if (away <= reach && away < distance) { closest = node; distance = away; }
      }
      return closest?.data ?? null;
    },
    update(data) {
      if (destroyed) return;
      const old = byId;
      byId = new Map(); groups.clear();
      for (const item of data.nodes) {
        if (byId.has(item.id)) continue;
        if (!groupOrder.includes(item.group)) groupOrder.push(item.group);
        const previous = old.get(item.id);
        const node: Node = { id: item.id, data: item, radius: 2.4, spiked: false, color: ink, tx: 0, ty: 0, x: previous?.x, y: previous?.y, fx: previous?.fx, fy: previous?.fy };
        byId.set(item.id, node);
        if (!groups.has(item.group)) groups.set(item.group, { nodes: [], color: ink });
        groups.get(item.group)!.nodes.push(node);
      }
      nodes = [...byId.values()];
      links = data.links.filter(link => byId.has(link.source) && byId.has(link.target)).map(link => ({ ...link }));
      degree = new Map();
      for (const link of links) for (const id of [link.source as string, link.target as string]) degree.set(id, (degree.get(id) ?? 0) + 1);
      for (const node of nodes) {
        const count = degree.get(node.id) ?? 0;
        node.radius = 2.4 + 0.75 * Math.sqrt(Math.min(30, count));
        node.spiked = count >= HUB_LINKS;
      }
      tint();
      if (selected && !byId.has(selected)) selected = null;
      if (hovered && !byId.has(hovered)) hover(null);
      simulation.force("link", null).nodes(nodes);
      targets(); forces();
      if (!old.size) fit();
    },
    setLayout(next) { if (destroyed || next === layout) return; layout = next; for (const node of nodes) { node.fx = null; node.fy = null; } targets(); forces(); fit(); },
    setPhysics(next) {
      if (destroyed) return;
      for (const key of Object.keys(PHYSICS_LIMITS) as (keyof typeof physics)[]) {
        const value = next[key], limit = PHYSICS_LIMITS[key];
        if (typeof value === "number" && Number.isFinite(value)) physics[key] = Math.min(limit.max, Math.max(limit.min, value));
      }
      forces();
    },
    focus(id) {
      if (destroyed) return;
      const node = id ? byId.get(id) : null;
      selected = node?.id ?? null;
      if (!node) fit();
      else {
        selection.call(behavior.transform, zoomIdentity.translate(width / 2, height / 2).scale(Math.max(1.2, transform.k)).translate(-node.x!, -node.y!));
        framed = false;
      }
      requestDraw();
    },
    setActive(next) { active = next; syncVisibility(); },
    resize() {
      if (destroyed) return;
      const oldWidth = width, oldHeight = height;
      width = el.clientWidth || 1; height = el.clientHeight || 1; dpr = Math.min(2, devicePixelRatio || 1);
      canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
      // A fitted view refits the new size; a view the user moved keeps its scale, recentred.
      if (framed) fit();
      else selection.call(behavior.transform, zoomIdentity.translate(transform.x + (width - oldWidth) / 2, transform.y + (height - oldHeight) / 2).scale(transform.k));
      requestDraw();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true; cancelAnimationFrame(frame); simulation.stop(); resize.disconnect(); intersection.disconnect();
      selection.on(".zoom", null);
      canvas.removeEventListener("pointerdown", pointerDown); canvas.removeEventListener("pointermove", pointerMove); canvas.removeEventListener("pointerup", pointerUp); canvas.removeEventListener("pointercancel", cancel); canvas.removeEventListener("pointerleave", leave); canvas.removeEventListener("contextmenu", context);
      window.removeEventListener("kitty:theme", colors); document.removeEventListener("visibilitychange", syncVisibility); motion.removeEventListener("change", syncVisibility);
      canvas.remove(); el.style.cursor = "";
    },
  };
  colors(); handle.resize(); handle.update(opts);
  resize.observe(el); intersection.observe(el);
  window.addEventListener("kitty:theme", colors); document.addEventListener("visibilitychange", syncVisibility); motion.addEventListener("change", syncVisibility);
  return handle;
}
