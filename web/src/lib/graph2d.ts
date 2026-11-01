import { forceSimulation, forceManyBody, forceLink, forceX, forceY, forceCollide, type SimulationNodeDatum, type SimulationLinkDatum } from "d3-force";
import { select } from "d3-selection";
import { zoom, zoomIdentity, type ZoomTransform } from "d3-zoom";
import { DEFAULT_PHYSICS, PHYSICS_LIMITS, type GraphNode, type GraphOptions, type GraphRenderer } from "./graph3d";
import { harmonize } from "./graphPalette";

type Node = SimulationNodeDatum & { id: string; data: GraphNode; radius: number; w: number; h: number; color: string; tx: number; ty: number };
type Link = SimulationLinkDatum<Node> & { directed?: boolean; label?: string; weight?: number };
type Point = { x: number; y: number };
type Box = { x: number; y: number; w: number; h: number };

/** Card size in graph units: hubs are a little larger. */
const cardSize = (data: GraphNode) => (data.kind === "hub" ? { w: 128, h: 44 } : { w: 112, h: 38 });
/** How far focus and its neighbours rise (scale and shadow), 0–1. */
const LIFT = { focus: 1, neighbor: 0.65 };
/** Below this zoom card text would be under ~7px, so cards go blank and labels move outside. */
const TEXT_ZOOM = 0.67;
const LABEL_DIRECTIONS: [number, number][] = [[0, 1], [0, -1], [1, 0], [-1, 0], [0.75, 0.66], [-0.75, 0.66], [0.75, -0.66], [-0.75, -0.66]];
const overlaps = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/** Mixes two #rrggbb colours; anything else falls back to `a`. */
function mixHex(a: string, b: string, amount: number): string {
  const parse = (hex: string) => (/^#[0-9a-f]{6}$/i.test(hex) ? [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)) : null);
  const [from, to] = [parse(a), parse(b)];
  if (!from || !to) return a;
  return `#${from.map((value, i) => Math.round(value + (to[i] - value) * amount).toString(16).padStart(2, "0")).join("")}`;
}

/** Fresh kitty! Canvas renderer; no LearnHarness source or assets are used. */
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
  /** Links per node, shown on hub cards. */
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
  let ink = "", chrome = "", muted = "", font = "", heading = "", mono = "", raised = "", shadow = "";
  let palette: string[] = [];
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const simulation = forceSimulation<Node>().stop().alphaDecay(0.035);
  const selection = select(canvas);
  let drag: { id: string | null; pointer: number; start: Point; moved: boolean } | null = null;
  const visible = () => !destroyed && active && intersecting && !document.hidden && el.clientWidth > 0 && el.clientHeight > 0;

  function colors() {
    const style = getComputedStyle(el);
    palette = harmonize([1, 2, 3, 4, 5, 6].map(i => style.getPropertyValue(`--c${i}`).trim()).filter(Boolean));
    if (!palette.length) palette = ["#b2a6c0"];
    ink = style.getPropertyValue("--ink").trim() || "#e7dfef";
    chrome = style.getPropertyValue("--chrome").trim() || "#0e0c14";
    muted = style.getPropertyValue("--muted").trim() || "#8f86a0";
    font = style.getPropertyValue("--br-font-ui").trim() || "ui-monospace, monospace";
    heading = style.getPropertyValue("--heading").trim() || font;
    mono = style.getPropertyValue("--font-mono").trim() || "ui-monospace, monospace";
    raised = style.getPropertyValue("--solid-raised").trim() || chrome;
    shadow = style.getPropertyValue("--shadow-tint").trim() || "#000000";
    for (const [name, group] of groups) {
      group.color = palette[groupOrder.indexOf(name) % palette.length];
      for (const node of group.nodes) node.color = group.color;
    }
    requestDraw();
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
    simulation.force("charge", forceManyBody<Node>().strength(-physics.repulsion * 5).distanceMax(nodes.length >= 400 ? 400 : Infinity))
      .force("link", forceLink<Node, Link>(links).id(node => node.id)
        .distance(link => layout === "clusters" && (link.source as Node).data.group !== (link.target as Node).data.group ? 240 : 96)
        .strength(link => physics.spring * (layout === "clusters" && (link.source as Node).data.group !== (link.target as Node).data.group ? 0.025 : 0.4)))
      .force("x", forceX<Node>(node => node.tx).strength(layout === "force" ? 0.015 : 0.15))
      .force("y", forceY<Node>(node => node.ty).strength(layout === "force" ? 0.015 : 0.15))
      .force("collide", forceCollide<Node>(node => node.w * 0.56))
      .velocityDecay(physics.damping).alpha(1).stop();
    simulation.tick(240);
    simulation.alpha(0);
    requestDraw();
  }
  function fit() {
    framed = true;
    if (!nodes.length) { selection.call(behavior.transform, zoomIdentity); return; }
    // Cards (lifted up to 24%) plus the group labels above them.
    const minX = Math.min(...nodes.map(node => node.x! - node.w * 0.62));
    const maxX = Math.max(...nodes.map(node => node.x! + node.w * 0.62));
    const minY = Math.min(...nodes.map(node => node.y! - node.h * 0.62)) - 30;
    const maxY = Math.max(...nodes.map(node => node.y! + node.h * 0.62));
    // Labels outside zoomed-out cards keep their pixel size; reserve room for them.
    const pad = 28;
    const k = Math.max(0.12, Math.min(2, Math.max(1, width - 2 * pad) / (maxX - minX), Math.max(1, height - 2 * pad) / (maxY - minY)) * 0.9);
    selection.call(behavior.transform, zoomIdentity.translate(width / 2, height / 2).scale(k).translate(-(minX + maxX) / 2, -(minY + maxY) / 2));
  }
  /** Lift of a node right now: 1 for the focus, 0.65 for its neighbours, else 0. */
  function liftOf(node: Node, focus: string | null, neighbors: Set<string>) {
    return node.id === focus ? LIFT.focus : neighbors.has(node.id) ? LIFT.neighbor : 0;
  }
  /** Distance from a card's centre to its edge along the unit vector (ux, uy). */
  function edgeInset(node: Node, scale: number, ux: number, uy: number) {
    const hw = (node.w * scale) / 2, hh = (node.h * scale) / 2;
    return Math.min(Math.abs(ux) > 1e-6 ? hw / Math.abs(ux) : Infinity, Math.abs(uy) > 1e-6 ? hh / Math.abs(uy) : Infinity);
  }
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
    const scaleOf = (node: Node) => 1 + 0.24 * liftOf(node, focus, neighbors);
    const k = transform.k;
    ctx.save(); ctx.translate(transform.x, transform.y); ctx.scale(k, k);
    // Links: gentle arcs, source-to-target gradient, clipped to the card edges.
    ctx.lineCap = "round";
    for (const link of links) {
      const a = link.source as Node, b = link.target as Node;
      const dx = b.x! - a.x!, dy = b.y! - a.y!;
      const cx = (a.x! + b.x!) / 2 - dy * 0.2, cy = (a.y! + b.y!) / 2 + dx * 0.2;
      const la = Math.hypot(cx - a.x!, cy - a.y!) || 1, lb = Math.hypot(cx - b.x!, cy - b.y!) || 1;
      const ua = [(cx - a.x!) / la, (cy - a.y!) / la], ub = [(cx - b.x!) / lb, (cy - b.y!) / lb];
      const ia = edgeInset(a, scaleOf(a), ua[0], ua[1]) + 2.5, ib = edgeInset(b, scaleOf(b), ub[0], ub[1]) + 2.5;
      const ax = a.x! + ua[0] * ia, ay = a.y! + ua[1] * ia, bx = b.x! + ub[0] * ib, by = b.y! + ub[1] * ib;
      const gradient = ctx.createLinearGradient(ax, ay, bx, by);
      gradient.addColorStop(0, a.color); gradient.addColorStop(1, b.color);
      // Solid, legible strokes: thin translucent hairlines smear under compositor upscaling
      // (e.g. XWayland at 125%), so links keep ~1.75 CSS px and strong alpha at rest.
      const touching = !!focus && (a.id === focus || b.id === focus);
      ctx.globalAlpha = !focus ? 0.8 : touching ? 1 : 0.14;
      ctx.strokeStyle = gradient;
      ctx.lineWidth = (touching ? 2.25 : 1.75) * Math.min(1.4, Math.max(1, link.weight ?? 1)) / k;
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.quadraticCurveTo(cx, cy, bx, by); ctx.stroke();
      if (link.directed) {
        const angle = Math.atan2(-ub[1], -ub[0]), size = 5.5 / Math.max(0.6, k);
        ctx.fillStyle = b.color;
        ctx.beginPath(); ctx.moveTo(bx, by);
        ctx.lineTo(bx - Math.cos(angle - 0.42) * size, by - Math.sin(angle - 0.42) * size);
        ctx.lineTo(bx - Math.cos(angle + 0.42) * size, by - Math.sin(angle + 0.42) * size);
        ctx.closePath(); ctx.fill();
      }
    }
    ctx.lineCap = "butt";
    // Cards: lifted ones are drawn last, on a deeper shadow.
    const withText = k >= TEXT_ZOOM;
    const order = [...nodes].sort((a, b) => liftOf(a, focus, neighbors) - liftOf(b, focus, neighbors));
    for (const node of order) {
      const lift = liftOf(node, focus, neighbors), scale = 1 + 0.24 * lift, hub = node.data.kind === "hub";
      const w = node.w * scale, h = node.h * scale, x = node.x! - w / 2, y = node.y! - h / 2;
      ctx.globalAlpha = !focus || lift > 0 ? 1 : 0.4;
      ctx.save();
      ctx.shadowColor = /^#[0-9a-f]{6}$/i.test(shadow) ? `${shadow}99` : shadow;
      ctx.shadowBlur = (12 + 14 * lift) * k; ctx.shadowOffsetY = (3 + 5 * lift) * k;
      ctx.fillStyle = mixHex(raised, node.color, hub ? 0.16 : 0.06);
      ctx.beginPath(); ctx.roundRect(x, y, w, h, 8); ctx.fill();
      ctx.restore();
      ctx.save();
      ctx.beginPath(); ctx.roundRect(x, y, w, h, 8); ctx.clip();
      ctx.fillStyle = node.color; ctx.fillRect(x, y, 3 * scale, h);
      ctx.restore();
      const alpha = ctx.globalAlpha;
      ctx.globalAlpha = alpha * (hub || lift > 0 ? 0.85 : 0.45);
      ctx.strokeStyle = node.color; ctx.lineWidth = (hub ? 1.25 : 1) / Math.max(1, k * 0.8);
      ctx.beginPath(); ctx.roundRect(x + 0.5, y + 0.5, w - 1, h - 1, 7.5); ctx.stroke();
      ctx.globalAlpha = alpha;
      if (withText) {
        ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
        ctx.font = `600 ${7.5 * scale}px ${mono}`; ctx.letterSpacing = `${0.9 * scale}px`;
        ctx.fillStyle = node.color;
        // Eyebrow: the entity kind, or a hub's link count; an unlinked hub has none and centres its title.
        const count = degree.get(node.id) ?? 0;
        const eyebrow = node.data.kind && !hub ? node.data.kind : count ? `${count} ${count === 1 ? "link" : "links"}` : "";
        if (eyebrow) ctx.fillText(eyebrow.toUpperCase().slice(0, 18), x + 11 * scale, y + h * 0.4);
        ctx.letterSpacing = "0px";
        ctx.font = hub ? `700 ${12 * scale}px ${heading}` : `600 ${10.5 * scale}px ${font}`;
        ctx.fillStyle = ink;
        let label = node.data.label;
        while (ctx.measureText(label).width > w - 20 * scale && label.length > 4) label = `${label.slice(0, -2)}…`;
        ctx.textBaseline = eyebrow ? "alphabetic" : "middle";
        ctx.fillText(label, x + 11 * scale, eyebrow ? y + h * 0.78 : y + h / 2);
      }
    }
    ctx.restore(); ctx.globalAlpha = 1;
    // Group names: tracked mono capitals with a short underline above each cluster; no regions.
    ctx.font = `650 10px ${mono}`; ctx.letterSpacing = "1.6px"; ctx.textAlign = "center"; ctx.textBaseline = "bottom";
    const focusGroup = focus ? byId.get(focus)?.data.group : undefined;
    const cardBoxes: Box[] = nodes.map(node => {
      const [cx, cy] = transform.apply([node.x!, node.y!]), w = node.w * scaleOf(node) * k, h = node.h * scaleOf(node) * k;
      return { x: cx - w / 2, y: cy - h / 2, w, h };
    });
    for (const [name, group] of groups) {
      // A one-card group named like its card would only repeat the title.
      if (!group.nodes.length || (group.nodes.length === 1 && group.nodes[0].data.label.toLowerCase() === name.toLowerCase())) continue;
      const top = Math.min(...group.nodes.map(node => node.y! - node.h * scaleOf(node) / 2));
      const centre = group.nodes.reduce((sum, node) => sum + node.x!, 0) / group.nodes.length;
      const label = name.toUpperCase();
      const w = ctx.measureText(label).width;
      // Centred over the group, then raised in 14px steps until it clears every card.
      let [x, y] = transform.apply([centre, top]);
      for (let step = 0; step < 6 && cardBoxes.some(card => overlaps(card, { x: x - w / 2, y: y - 24, w, h: 16 })); step++) y -= 14;
      const alpha = focusGroup && focusGroup !== name ? 0.33 : 1;
      halo(label, x, y - 12, group.color, alpha, 5);
      ctx.globalAlpha = 0.6 * alpha; ctx.fillStyle = group.color;
      ctx.beginPath(); ctx.roundRect(x - w / 2, y - 9, Math.min(w, 22), 2, 1); ctx.fill();
    }
    ctx.letterSpacing = "0px"; ctx.globalAlpha = 1;
    if (!withText) outsideLabels(focus, neighbors, scaleOf);
  }
  /** Text with a halo in the chrome colour so it stays readable over links. */
  function halo(text: string, x: number, y: number, color: string, alpha: number, width: number) {
    ctx.globalAlpha = alpha * 0.9; ctx.lineJoin = "round"; ctx.lineWidth = width; ctx.strokeStyle = chrome;
    ctx.strokeText(text, x, y);
    ctx.globalAlpha = alpha; ctx.fillStyle = color; ctx.fillText(text, x, y);
  }
  /**
   * Zoomed out, cards carry no text: names sit beside them instead, each in the first
   * of eight directions (three rings out) that overlaps neither a card nor another
   * name; displaced names get a leader line. Names with no free spot are skipped.
   */
  function outsideLabels(focus: string | null, neighbors: Set<string>, scaleOf: (node: Node) => number) {
    const k = transform.k;
    ctx.font = `500 10.5px ${font}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    const cards: Box[] = nodes.map(node => {
      const [x, y] = transform.apply([node.x!, node.y!]), w = node.w * scaleOf(node) * k, h = node.h * scaleOf(node) * k;
      return { x: x - w / 2 - 2, y: y - h / 2 - 2, w: w + 4, h: h + 4 };
    });
    const placed: Box[] = [];
    const rank = (node: Node) => (node.id === focus ? 2 : 0) + (node.data.kind === "hub" ? 1 : 0);
    for (const node of [...nodes].sort((a, b) => rank(b) - rank(a))) {
      const alpha = !focus || node.id === focus || neighbors.has(node.id) ? 1 : 0.35;
      const text = node.data.label.length > 26 ? `${node.data.label.slice(0, 25)}…` : node.data.label;
      const w = ctx.measureText(text).width + 12, h = 17;
      const [nx, ny] = transform.apply([node.x!, node.y!]);
      const hw = (node.w * scaleOf(node) * k) / 2, hh = (node.h * scaleOf(node) * k) / 2;
      let spot: Box | null = null, ring = 0;
      search: for (ring = 0; ring < 3; ring++) for (const [dx, dy] of LABEL_DIRECTIONS) {
        const gap = 4 + ring * 20;
        const cx = nx + dx * (hw + gap + w / 2 * Math.abs(dx)), cy = ny + dy * (hh + gap + h / 2 * Math.abs(dy));
        const box = { x: cx - w / 2, y: cy - h / 2, w, h };
        if (box.x < 8 || box.y < 8 || box.x + w > width - 8 || box.y + h > height - 8) continue;
        if (placed.some(other => overlaps(other, box)) || cards.some(other => overlaps(other, box))) continue;
        spot = box; break search;
      }
      if (!spot) continue;
      placed.push(spot);
      if (ring > 0) {
        const lx = Math.max(spot.x, Math.min(nx, spot.x + w)), ly = Math.max(spot.y, Math.min(ny, spot.y + h));
        ctx.globalAlpha = alpha * 0.7; ctx.strokeStyle = node.color; ctx.lineWidth = 0.9;
        ctx.beginPath(); ctx.moveTo(nx, ny); ctx.lineTo(lx, ly); ctx.stroke();
      }
      ctx.globalAlpha = alpha * 0.92; ctx.fillStyle = chrome;
      ctx.beginPath(); ctx.roundRect(spot.x, spot.y, w, h, 5); ctx.fill();
      ctx.globalAlpha = alpha; ctx.fillStyle = node.data.kind === "hub" ? ink : muted;
      ctx.fillText(text, spot.x + w / 2, spot.y + h / 2 + 0.5);
    }
    ctx.globalAlpha = 1;
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
      // Cards are hit across their whole face; the nearest centre wins where cards overlap.
      let closest: Node | null = null, distance = Infinity;
      for (const node of nodes) {
        const [px, py] = transform.apply([node.x!, node.y!]);
        const dx = Math.abs(px + rect.left - x), dy = Math.abs(py + rect.top - y);
        const hw = Math.max(7, (node.w * transform.k) / 2 + 2), hh = Math.max(7, (node.h * transform.k) / 2 + 2);
        if (dx <= hw && dy <= hh && dx + dy < distance) { closest = node; distance = dx + dy; }
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
        const color = palette[groupOrder.indexOf(item.group) % palette.length];
        const previous = old.get(item.id);
        const node: Node = { id: item.id, data: item, radius: 2.5 + Math.sqrt(Math.min(10, Math.max(0.5, item.size ?? (item.kind === "hub" ? 4 : 1)))) * 2, ...cardSize(item), color, tx: 0, ty: 0, x: previous?.x, y: previous?.y, fx: previous?.fx, fy: previous?.fy };
        byId.set(item.id, node);
        if (!groups.has(item.group)) groups.set(item.group, { nodes: [], color });
        groups.get(item.group)!.nodes.push(node);
      }
      nodes = [...byId.values()];
      links = data.links.filter(link => byId.has(link.source) && byId.has(link.target)).map(link => ({ ...link }));
      degree = new Map();
      for (const link of links) for (const id of [link.source as string, link.target as string]) degree.set(id, (degree.get(id) ?? 0) + 1);
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
