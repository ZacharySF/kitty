import { forceSimulation, forceManyBody, forceLink, forceX, forceY, forceCollide, type SimulationNodeDatum, type SimulationLinkDatum } from "d3-force";
import { select } from "d3-selection";
import { zoom, zoomIdentity, type ZoomTransform } from "d3-zoom";
import { DEFAULT_PHYSICS, PHYSICS_LIMITS, type GraphNode, type GraphOptions, type GraphRenderer } from "./graph3d";
import { harmonize } from "./graphPalette";

type Node = SimulationNodeDatum & { id: string; data: GraphNode; radius: number; color: string; tx: number; ty: number };
type Link = SimulationLinkDatum<Node> & { directed?: boolean; label?: string; weight?: number };
type Point = { x: number; y: number };

/** Monotone-chain hull of padded node discs, including singleton groups. */
function region(nodes: Node[]): Point[] {
  const points: Point[] = [];
  for (const node of nodes) for (let i = 0; i < 12; i++) {
    const angle = i * Math.PI / 6;
    points.push({ x: node.x! + Math.cos(angle) * (node.radius + 28), y: node.y! + Math.sin(angle) * (node.radius + 28) });
  }
  points.sort((a, b) => a.x - b.x || a.y - b.y);
  const cross = (a: Point, b: Point, c: Point) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
  const half = (ordered: Point[]) => {
    const result: Point[] = [];
    for (const point of ordered) {
      while (result.length > 1 && cross(result.at(-2)!, result.at(-1)!, point) <= 0) result.pop();
      result.push(point);
    }
    return result.slice(0, -1);
  };
  return [...half(points), ...half([...points].reverse())];
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
  const groups = new Map<string, { nodes: Node[]; hull: Point[]; color: string }>();
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
  let ink = "", chrome = "", muted = "", font = "";
  let palette: string[] = [];
  let dirtyHulls = true;
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
        dirtyHulls = true;
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
        .distance(link => layout === "clusters" && (link.source as Node).data.group !== (link.target as Node).data.group ? 180 : 68)
        .strength(link => physics.spring * (layout === "clusters" && (link.source as Node).data.group !== (link.target as Node).data.group ? 0.025 : 0.4)))
      .force("x", forceX<Node>(node => node.tx).strength(layout === "force" ? 0.015 : 0.15))
      .force("y", forceY<Node>(node => node.ty).strength(layout === "force" ? 0.015 : 0.15))
      .force("collide", forceCollide<Node>(node => node.radius + 12))
      .velocityDecay(physics.damping).alpha(1).stop();
    simulation.tick(240);
    simulation.alpha(0);
    dirtyHulls = true;
    requestDraw();
  }
  function fit() {
    framed = true;
    if (!nodes.length) { selection.call(behavior.transform, zoomIdentity); return; }
    const minX = Math.min(...nodes.map(node => node.x!)) - 65;
    const maxX = Math.max(...nodes.map(node => node.x!)) + 65;
    const minY = Math.min(...nodes.map(node => node.y!)) - 65;
    const maxY = Math.max(...nodes.map(node => node.y!)) + 65;
    // Group pills above and labels below keep their pixel size at any zoom; reserve room for them.
    const pad = 28;
    const k = Math.max(0.12, Math.min(2, Math.max(1, width - 2 * pad) / (maxX - minX), Math.max(1, height - 2 * pad) / (maxY - minY)) * 0.9);
    selection.call(behavior.transform, zoomIdentity.translate(width / 2, height / 2).scale(k).translate(-(minX + maxX) / 2, -(minY + maxY) / 2));
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
    ctx.save(); ctx.translate(transform.x, transform.y); ctx.scale(transform.k, transform.k);
    for (const group of groups.values()) {
      if (dirtyHulls) group.hull = region(group.nodes);
      const hull = group.hull;
      if (!hull.length) continue;
      ctx.beginPath();
      const last = hull.at(-1)!;
      ctx.moveTo((last.x + hull[0].x) / 2, (last.y + hull[0].y) / 2);
      hull.forEach((point, i) => { const next = hull[(i + 1) % hull.length]; ctx.quadraticCurveTo(point.x, point.y, (point.x + next.x) / 2, (point.y + next.y) / 2); });
      ctx.closePath(); ctx.fillStyle = group.color; ctx.globalAlpha = focus && !group.nodes.some(node => node.id === focus) ? 0.025 : 0.075; ctx.fill();
      ctx.strokeStyle = group.color; ctx.lineWidth = 1 / transform.k; ctx.globalAlpha *= 2.5; ctx.stroke();
    }
    dirtyHulls = false;
    for (const link of links) {
      const a = link.source as Node, b = link.target as Node;
      ctx.globalAlpha = !focus ? 0.28 : a.id === focus || b.id === focus ? 0.8 : 0.06;
      ctx.strokeStyle = a.color; ctx.lineWidth = Math.min(2, Math.max(0.6, link.weight ?? 1)) / transform.k;
      ctx.beginPath(); ctx.moveTo(a.x!, a.y!); ctx.lineTo(b.x!, b.y!); ctx.stroke();
      if (link.directed) {
        const angle = Math.atan2(b.y! - a.y!, b.x! - a.x!);
        const x = b.x! - Math.cos(angle) * (b.radius + 3), y = b.y! - Math.sin(angle) * (b.radius + 3);
        ctx.beginPath(); ctx.moveTo(x - Math.cos(angle - 0.5) * 8, y - Math.sin(angle - 0.5) * 8); ctx.lineTo(x, y); ctx.lineTo(x - Math.cos(angle + 0.5) * 8, y - Math.sin(angle + 0.5) * 8); ctx.stroke();
      }
    }
    for (const node of nodes) {
      ctx.globalAlpha = !focus || node.id === focus || neighbors.has(node.id) ? 1 : 0.25;
      ctx.fillStyle = node.color;
      ctx.beginPath(); ctx.arc(node.x!, node.y!, node.radius, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore(); ctx.globalAlpha = 1;
    ctx.font = `500 11px ${font}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const [name, group] of groups) {
      if (!group.hull.length) continue;
      const top = Math.min(...group.hull.map(p => p.y));
      const x = group.nodes.reduce((sum, node) => sum + node.x!, 0) / group.nodes.length;
      const point = transform.apply([x, top]);
      pill(name.toUpperCase(), point[0], point[1] - 10, group.color, 0.85);
    }
    for (const node of nodes) {
      const isFocus = node.id === focus;
      const alpha = isFocus ? 1 : Math.max(0, Math.min(1, (transform.k - 0.3) / 0.55)) * (!focus || neighbors.has(node.id) ? 1 : 0.3);
      if (alpha <= 0) continue;
      const point = transform.apply([node.x!, node.y!]);
      pill(node.data.label, point[0], point[1] + node.radius * transform.k + 15, node.color, alpha);
    }
    if (hovered) {
      const node = byId.get(hovered);
      if (node) {
        const [x, y] = transform.apply([node.x!, node.y!]);
        const halo = ctx.createRadialGradient(x, y, 4, x, y, 24);
        halo.addColorStop(0, node.color); halo.addColorStop(1, "transparent");
        ctx.globalAlpha = 0.35; ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(x, y, 24, 0, Math.PI * 2); ctx.fill(); ctx.globalAlpha = 1;
      }
    }
  }
  function pill(text: string, x: number, y: number, color: string, alpha: number) {
    const label = text.length > 42 ? `${text.slice(0, 41)}…` : text;
    const w = ctx.measureText(label).width + 16;
    ctx.globalAlpha = alpha; ctx.fillStyle = chrome; ctx.strokeStyle = color; ctx.lineWidth = 0.7;
    ctx.beginPath(); ctx.roundRect(x - w / 2, y - 10, w, 20, 9); ctx.fill(); ctx.globalAlpha = alpha * 0.5; ctx.stroke();
    ctx.globalAlpha = alpha; ctx.fillStyle = ink || muted; ctx.fillText(label, x, y); ctx.globalAlpha = 1;
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
        node.x = node.fx = point[0]; node.y = node.fy = point[1]; dirtyHulls = true; requestDraw();
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
      let closest: Node | null = null, distance = Infinity;
      for (const node of nodes) {
        const [px, py] = transform.apply([node.x!, node.y!]);
        const d = Math.hypot(px + rect.left - x, py + rect.top - y);
        if (d < Math.max(7, node.radius * transform.k + 3) && d < distance) { closest = node; distance = d; }
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
        const node: Node = { id: item.id, data: item, radius: 2.5 + Math.sqrt(Math.min(10, Math.max(0.5, item.size ?? (item.kind === "hub" ? 4 : 1)))) * 2, color, tx: 0, ty: 0, x: previous?.x, y: previous?.y, fx: previous?.fx, fy: previous?.fy };
        byId.set(item.id, node);
        if (!groups.has(item.group)) groups.set(item.group, { nodes: [], hull: [], color });
        groups.get(item.group)!.nodes.push(node);
      }
      nodes = [...byId.values()];
      links = data.links.filter(link => byId.has(link.source) && byId.has(link.target)).map(link => ({ ...link }));
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
