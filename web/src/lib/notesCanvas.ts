export type Point = [number, number, number];
export type Viewport = { x: number; y: number; zoom: number };
export type Layer = {
  id: string;
  name: string;
  visible: boolean;
  locked: boolean;
};
export type ElementKind =
  | "pen"
  | "highlighter"
  | "text"
  | "sticky"
  | "rect"
  | "ellipse"
  | "line"
  | "arrow"
  | "connector"
  | "image";
export type CanvasElement = {
  id: string;
  kind: ElementKind;
  layer: string;
  x: number;
  y: number;
  w: number;
  h: number;
  color: string;
  fill: string;
  width: number;
  points?: Point[];
  text?: string;
  fontSize?: number;
  group?: string;
  attachment?: string;
  from?: string;
  to?: string;
};
export type CanvasDocument = {
  version: 1;
  grid: boolean;
  snap: boolean;
  layers: Layer[];
  elements: CanvasElement[];
};
export type Bounds = { x: number; y: number; w: number; h: number };
// Documents are bounded JSON. JSON cloning also unwraps Svelte's reactive proxies.
export const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
export const uid = () => crypto.randomUUID().replaceAll("-", "");
/** Charcoal paper between black and grey; the default ink is a light lavender. */
export const CANVAS_PAPER = "#1f1d25";
export const CANVAS_INK = "#e7dfef";
const hexColor = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;
/**
 * Stored colours never change. Dark inks (from earlier light-paper notes or a
 * dark pick) are drawn with inverted lightness so they stay legible on the dark
 * paper — the approach dark-mode note apps use for ink.
 */
export function displayColor(color: string): string {
  if (!hexColor.test(color)) return color;
  let hex = color.slice(1);
  if (hex.length === 3) hex = [...hex].map((c) => c + c).join("");
  const [r, g, b] = [0, 2, 4].map(
    (i) => parseInt(hex.slice(i, i + 2), 16) / 255,
  );
  const linear = (c: number) =>
    c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  if (0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b) >= 0.12)
    return color;
  const max = Math.max(r, g, b),
    min = Math.min(r, g, b),
    l = (max + min) / 2,
    d = max - min;
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
  const h =
    d === 0
      ? 0
      : max === r
        ? ((g - b) / d) % 6
        : max === g
          ? (b - r) / d + 2
          : (r - g) / d + 4;
  const light = Math.max(0.72, 1 - l),
    chroma = (1 - Math.abs(2 * light - 1)) * s;
  const x = chroma * (1 - Math.abs((h % 2) - 1)),
    m = light - chroma / 2;
  const [rr, gg, bb] =
    h < 1
      ? [chroma, x, 0]
      : h < 2
        ? [x, chroma, 0]
        : h < 3
          ? [0, chroma, x]
          : h < 4
            ? [0, x, chroma]
            : h < 5
              ? [x, 0, chroma]
              : [chroma, 0, x];
  return (
    "#" +
    [rr, gg, bb]
      .map((c) =>
        Math.round((c + m) * 255)
          .toString(16)
          .padStart(2, "0"),
      )
      .join("")
  );
}
/** Text on a sticky sits on its light fill, so it keeps the stored colour. */
const inkOf = (e: { kind: string; color: string }) =>
  e.kind === "sticky" ? e.color : displayColor(e.color);
export const emptyDocument = (): CanvasDocument => ({
  version: 1,
  grid: true,
  snap: false,
  layers: [{ id: "default", name: "Drawing", visible: true, locked: false }],
  elements: [],
});
// Palm contacts must never interrupt an active pen stroke.
export function drawingInput(
  event: { pointerType: string; button: number; buttons: number },
  tool: string,
  penOnly: boolean,
  penActive: boolean,
  recentPen: boolean,
): string {
  if (event.pointerType !== "pen" && (penActive || recentPen)) return "ignore";
  if (
    event.pointerType === "pen" &&
    (event.button === 5 || (event.buttons & 34) !== 0)
  )
    return "eraser";
  if (
    event.button === 1 ||
    tool === "pan" ||
    (event.pointerType === "touch" && penOnly)
  )
    return "pan";
  return tool;
}
export function world(x: number, y: number, v: Viewport): Point {
  return [(x - v.x) / v.zoom, (y - v.y) / v.zoom, 1];
}
export function zoomAt(
  v: Viewport,
  x: number,
  y: number,
  factor: number,
): Viewport {
  const zoom = Math.max(0.05, Math.min(20, v.zoom * factor));
  const p = world(x, y, v);
  return { x: x - p[0] * zoom, y: y - p[1] * zoom, zoom };
}
export function bounds(elements: CanvasElement[]): Bounds | null {
  if (!elements.length) return null;
  const x = Math.min(...elements.map((e) => e.x)),
    y = Math.min(...elements.map((e) => e.y));
  return {
    x,
    y,
    w: Math.max(...elements.map((e) => e.x + e.w)) - x,
    h: Math.max(...elements.map((e) => e.y + e.h)) - y,
  };
}
export function fit(
  elements: CanvasElement[],
  width: number,
  height: number,
): Viewport {
  const b = bounds(elements);
  if (!b) return { x: 0, y: 0, zoom: 1 };
  const zoom = Math.max(
    0.05,
    Math.min(
      3,
      (width - 100) / Math.max(b.w, 50),
      (height - 100) / Math.max(b.h, 50),
    ),
  );
  return {
    x: (width - b.w * zoom) / 2 - b.x * zoom,
    y: (height - b.h * zoom) / 2 - b.y * zoom,
    zoom,
  };
}
export function finishStroke(e: CanvasElement): CanvasElement {
  if (!e.points?.length) return e;
  const left = Math.min(...e.points.map((p) => p[0])),
    top = Math.min(...e.points.map((p) => p[1]));
  const right = Math.max(...e.points.map((p) => p[0])),
    bottom = Math.max(...e.points.map((p) => p[1]));
  return {
    ...e,
    x: e.x + left,
    y: e.y + top,
    w: right - left,
    h: bottom - top,
    points: e.points.map((p) => [p[0] - left, p[1] - top, p[2]]),
  };
}
export function endpoints(
  e: CanvasElement,
  all: CanvasElement[],
): [Point, Point] {
  if (e.kind === "connector") {
    const from = all.find((v) => v.id === e.from),
      to = all.find((v) => v.id === e.to);
    if (from && to)
      return [
        [from.x + from.w / 2, from.y + from.h / 2, 1],
        [to.x + to.w / 2, to.y + to.h / 2, 1],
      ];
  }
  const a = e.points?.[0] ?? [0, 0, 1],
    b = e.points?.[1] ?? [e.w, e.h, 1];
  return [
    [a[0] + e.x, a[1] + e.y, a[2]],
    [b[0] + e.x, b[1] + e.y, b[2]],
  ];
}
export function distanceSegment(p: Point, a: Point, b: Point): number {
  const dx = b[0] - a[0],
    dy = b[1] - a[1],
    d = dx * dx + dy * dy;
  const t = d
    ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d))
    : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}
export function editable(e: CanvasElement, doc: CanvasDocument): boolean {
  const l = doc.layers.find((l) => l.id === e.layer);
  return !!l?.visible && !l.locked;
}
export function hit(
  e: CanvasElement,
  p: Point,
  tolerance: number,
  all: CanvasElement[],
): boolean {
  if (e.kind === "pen" || e.kind === "highlighter") {
    const pts = e.points ?? [];
    if (pts.length === 1)
      return (
        Math.hypot(p[0] - e.x - pts[0][0], p[1] - e.y - pts[0][1]) <=
        tolerance + e.width / 2
      );
    for (let i = 1; i < pts.length; i++)
      if (
        distanceSegment(
          p,
          [pts[i - 1][0] + e.x, pts[i - 1][1] + e.y, 1],
          [pts[i][0] + e.x, pts[i][1] + e.y, 1],
        ) <=
        tolerance + (e.width * Math.max(pts[i][2], 0.1)) / 2
      )
        return true;
    return false;
  }
  if (["line", "arrow", "connector"].includes(e.kind)) {
    const [a, b] = endpoints(e, all);
    return distanceSegment(p, a, b) <= tolerance + e.width / 2;
  }
  return (
    p[0] >= e.x - tolerance &&
    p[0] <= e.x + e.w + tolerance &&
    p[1] >= e.y - tolerance &&
    p[1] <= e.y + e.h + tolerance
  );
}
export function hitTop(
  doc: CanvasDocument,
  p: Point,
  tolerance: number,
): CanvasElement | undefined {
  return ordered(doc)
    .reverse()
    .find((e) => editable(e, doc) && hit(e, p, tolerance, doc.elements));
}
export function ordered(doc: CanvasDocument): CanvasElement[] {
  return doc.layers.flatMap((layer) =>
    layer.visible ? doc.elements.filter((e) => e.layer === layer.id) : [],
  );
}
export function removeElements(
  doc: CanvasDocument,
  ids: Set<string>,
): CanvasDocument {
  return {
    ...doc,
    elements: doc.elements.filter(
      (e) =>
        !ids.has(e.id) &&
        !(
          e.kind === "connector" &&
          (ids.has(e.from ?? "") || ids.has(e.to ?? ""))
        ),
    ),
  };
}
export function insidePolygon(p: Point, polygon: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i],
      b = polygon[j];
    if (
      a[1] > p[1] !== b[1] > p[1] &&
      p[0] < ((b[0] - a[0]) * (p[1] - a[1])) / (b[1] - a[1]) + a[0]
    )
      inside = !inside;
  }
  return inside;
}
export function lassoSelection(
  doc: CanvasDocument,
  polygon: Point[],
): string[] {
  return doc.elements
    .filter(
      (e) =>
        editable(e, doc) &&
        (e.points?.some((p) =>
          insidePolygon([e.x + p[0], e.y + p[1], 1], polygon),
        ) ||
          insidePolygon([e.x + e.w / 2, e.y + e.h / 2, 1], polygon)),
    )
    .map((e) => e.id);
}
export function expandGroups(doc: CanvasDocument, ids: string[]): string[] {
  const groups = new Set(
    doc.elements
      .filter((e) => ids.includes(e.id) && e.group)
      .map((e) => e.group),
  );
  return doc.elements
    .filter(
      (e) =>
        editable(e, doc) &&
        (ids.includes(e.id) || (!!e.group && groups.has(e.group))),
    )
    .map((e) => e.id);
}
export function resizeElement(
  e: CanvasElement,
  old: Bounds,
  next: Bounds,
): CanvasElement {
  const sx = next.w / Math.max(old.w, 1),
    sy = next.h / Math.max(old.h, 1);
  return {
    ...e,
    x: next.x + (e.x - old.x) * sx,
    y: next.y + (e.y - old.y) * sy,
    w: e.w * sx,
    h: e.h * sy,
    points: e.points?.map((p) => [p[0] * sx, p[1] * sy, p[2]]),
    fontSize: e.fontSize
      ? Math.max(8, Math.min(120, e.fontSize * Math.min(sx, sy)))
      : undefined,
  };
}
// A cancelled gesture always restores its pre-pointer snapshot; it never commits a half stroke.
export function finishGesture(
  before: CanvasDocument,
  current: CanvasDocument,
  cancelled: boolean,
): CanvasDocument {
  return cancelled ? clone(before) : current;
}
export function wrapText(text: string, maxChars: number): string[] {
  return text.split("\n").flatMap((line) => {
    if (!line) return [""];
    const out: string[] = [];
    while (line.length > maxChars) {
      let end = line.lastIndexOf(" ", maxChars);
      if (end < 1) end = maxChars;
      out.push(line.slice(0, end));
      line = line.slice(end).trimStart();
    }
    out.push(line);
    return out;
  });
}
function drawElement(
  ctx: CanvasRenderingContext2D,
  e: CanvasElement,
  all: CanvasElement[],
  images: Map<string, HTMLImageElement>,
) {
  ctx.save();
  const ink = inkOf(e);
  ctx.strokeStyle = ink;
  ctx.fillStyle = e.fill === "none" ? "transparent" : e.fill;
  ctx.lineWidth = e.width;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  if (e.kind === "pen" || e.kind === "highlighter") {
    const pts = e.points ?? [];
    if (e.kind === "highlighter") ctx.globalAlpha = 0.3;
    if (pts.length === 1) {
      ctx.fillStyle = ink;
      ctx.beginPath();
      ctx.arc(
        e.x + pts[0][0],
        e.y + pts[0][1],
        (e.width * Math.max(0.15, pts[0][2])) / 2,
        0,
        Math.PI * 2,
      );
      ctx.fill();
    }
    for (let i = 1; i < pts.length; i++) {
      ctx.lineWidth = e.width * Math.max(0.15, (pts[i - 1][2] + pts[i][2]) / 2);
      ctx.beginPath();
      ctx.moveTo(e.x + pts[i - 1][0], e.y + pts[i - 1][1]);
      ctx.lineTo(e.x + pts[i][0], e.y + pts[i][1]);
      ctx.stroke();
    }
  } else if (["line", "arrow", "connector"].includes(e.kind)) {
    const [a, b] = endpoints(e, all);
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
    if (e.kind !== "line") {
      const angle = Math.atan2(b[1] - a[1], b[0] - a[0]),
        n = Math.max(10, e.width * 4);
      ctx.fillStyle = ink;
      ctx.beginPath();
      ctx.moveTo(b[0], b[1]);
      ctx.lineTo(
        b[0] - n * Math.cos(angle - 0.5),
        b[1] - n * Math.sin(angle - 0.5),
      );
      ctx.lineTo(
        b[0] - n * Math.cos(angle + 0.5),
        b[1] - n * Math.sin(angle + 0.5),
      );
      ctx.closePath();
      ctx.fill();
    }
  } else if (e.kind === "image") {
    const image = images.get(e.attachment ?? "");
    if (image?.complete && image.naturalWidth)
      ctx.drawImage(image, e.x, e.y, e.w, e.h);
    else {
      ctx.fillStyle = "#2b2833";
      ctx.fillRect(e.x, e.y, e.w, e.h);
      ctx.strokeRect(e.x, e.y, e.w, e.h);
      ctx.fillStyle = ink;
      ctx.font = "16px sans-serif";
      ctx.fillText("Loading local reference…", e.x + 12, e.y + 25);
    }
  } else if (e.kind === "ellipse") {
    ctx.beginPath();
    ctx.ellipse(
      e.x + e.w / 2,
      e.y + e.h / 2,
      e.w / 2,
      e.h / 2,
      0,
      0,
      Math.PI * 2,
    );
    ctx.fill();
    ctx.stroke();
  } else if (e.kind === "rect") {
    ctx.fillRect(e.x, e.y, e.w, e.h);
    ctx.strokeRect(e.x, e.y, e.w, e.h);
  } else {
    if (e.kind === "sticky") {
      ctx.fillStyle = e.fill === "none" ? "#fff2a8" : e.fill;
      ctx.fillRect(e.x, e.y, e.w, e.h);
      ctx.strokeRect(e.x, e.y, e.w, e.h);
    }
    ctx.fillStyle = ink;
    const size = e.fontSize ?? 20;
    ctx.font = `${size}px sans-serif`;
    ctx.textBaseline = "top";
    wrapText(
      e.text ?? "",
      Math.max(1, Math.floor((e.w - 16) / (size * 0.6))),
    ).forEach((line, i) =>
      ctx.fillText(line, e.x + 8, e.y + 8 + i * size * 1.3),
    );
  }
  ctx.restore();
}
export function renderCanvas(
  canvas: HTMLCanvasElement,
  doc: CanvasDocument,
  v: Viewport,
  images: Map<string, HTMLImageElement>,
  selection: string[],
  overlay: Point[],
  cssWidth: number,
  cssHeight: number,
  ink: string = CANVAS_INK,
  background: string = CANVAS_PAPER,
) {
  const dpr = window.devicePixelRatio || 1;
  if (
    canvas.width !== Math.round(cssWidth * dpr) ||
    canvas.height !== Math.round(cssHeight * dpr)
  ) {
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, cssWidth, cssHeight);
  if (doc.grid && v.zoom > 0.1) {
    const step = 25 * v.zoom,
      ox = ((v.x % step) + step) % step,
      oy = ((v.y % step) + step) % step;
    ctx.fillStyle = ink;
    ctx.globalAlpha = 0.18;
    for (let x = ox; x < cssWidth; x += step)
      for (let y = oy; y < cssHeight; y += step) {
        ctx.beginPath();
        ctx.arc(x, y, 0.8, 0, Math.PI * 2);
        ctx.fill();
      }
    ctx.globalAlpha = 1;
  }
  ctx.translate(v.x, v.y);
  ctx.scale(v.zoom, v.zoom);
  ordered(doc).forEach((e) => drawElement(ctx, e, doc.elements, images));
  const b = bounds(doc.elements.filter((e) => selection.includes(e.id)));
  if (b) {
    ctx.strokeStyle = "#5570ee";
    ctx.lineWidth = 1.5 / v.zoom;
    ctx.setLineDash([5 / v.zoom, 4 / v.zoom]);
    ctx.strokeRect(
      b.x - 3 / v.zoom,
      b.y - 3 / v.zoom,
      b.w + 6 / v.zoom,
      b.h + 6 / v.zoom,
    );
    ctx.setLineDash([]);
    ctx.fillStyle = "#5570ee";
    ctx.fillRect(
      b.x + b.w - 5 / v.zoom,
      b.y + b.h - 5 / v.zoom,
      10 / v.zoom,
      10 / v.zoom,
    );
  }
  if (overlay.length) {
    ctx.strokeStyle = "#5570ee";
    ctx.lineWidth = 1 / v.zoom;
    ctx.setLineDash([5 / v.zoom, 4 / v.zoom]);
    ctx.beginPath();
    overlay.forEach((p, i) =>
      i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]),
    );
    ctx.stroke();
  }
}
const escapeXML = (s: string) =>
  s.replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&apos;",
      })[c]!,
  );
export async function exportSVG(doc: CanvasDocument): Promise<string> {
  const b = bounds(ordered(doc)) ?? { x: 0, y: 0, w: 800, h: 600 };
  const view = {
    x: b.x - 30,
    y: b.y - 30,
    w: Math.max(100, b.w + 60),
    h: Math.max(100, b.h + 60),
  };
  const refs = new Map<string, string>();
  for (const e of doc.elements.filter((e) => e.kind === "image"))
    if (!refs.has(e.attachment!)) {
      const response = await fetch(
        `/api/notes/attachments/${encodeURIComponent(e.attachment!)}`,
      );
      if (!response.ok)
        throw new Error("Reference unavailable; export stopped");
      const blob = await response.blob();
      const data = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error("Cannot read reference"));
        reader.readAsDataURL(blob);
      });
      refs.set(e.attachment!, data);
    }
  const parts = ordered(doc).map((e) => {
    const ink = escapeXML(inkOf(e));
    const style = `stroke="${ink}" stroke-width="${e.width}" fill="${escapeXML(e.fill)}" stroke-linecap="round" stroke-linejoin="round"`;
    if (e.kind === "pen" || e.kind === "highlighter") {
      const pts = e.points ?? [];
      const marks =
        pts.length === 1
          ? `<circle cx="${e.x + pts[0][0]}" cy="${e.y + pts[0][1]}" r="${(e.width * Math.max(0.15, pts[0][2])) / 2}" fill="${ink}"/>`
          : pts
              .slice(1)
              .map(
                (p, i) =>
                  `<path d="M ${e.x + pts[i][0]} ${e.y + pts[i][1]} L ${e.x + p[0]} ${e.y + p[1]}" stroke="${ink}" stroke-width="${e.width * Math.max(0.15, (pts[i][2] + p[2]) / 2)}" stroke-linecap="round"/>`,
              )
              .join("");
      return `<g opacity="${e.kind === "highlighter" ? 0.3 : 1}">${marks}</g>`;
    }
    if (["line", "arrow", "connector"].includes(e.kind)) {
      const [a, z] = endpoints(e, doc.elements);
      let head = "";
      if (e.kind !== "line") {
        const angle = Math.atan2(z[1] - a[1], z[0] - a[0]),
          n = Math.max(10, e.width * 4);
        head = `<polygon points="${z[0]},${z[1]} ${z[0] - n * Math.cos(angle - 0.5)},${z[1] - n * Math.sin(angle - 0.5)} ${z[0] - n * Math.cos(angle + 0.5)},${z[1] - n * Math.sin(angle + 0.5)}" fill="${ink}"/>`;
      }
      return `<path d="M ${a[0]} ${a[1]} L ${z[0]} ${z[1]}" ${style}/>${head}`;
    }
    if (e.kind === "ellipse")
      return `<ellipse cx="${e.x + e.w / 2}" cy="${e.y + e.h / 2}" rx="${e.w / 2}" ry="${e.h / 2}" ${style}/>`;
    if (e.kind === "rect")
      return `<rect x="${e.x}" y="${e.y}" width="${e.w}" height="${e.h}" ${style}/>`;
    if (e.kind === "image")
      return `<image x="${e.x}" y="${e.y}" width="${e.w}" height="${e.h}" href="${escapeXML(refs.get(e.attachment!)!)}"/>`;
    const size = e.fontSize ?? 20;
    const lines = wrapText(
      e.text ?? "",
      Math.max(1, Math.floor((e.w - 16) / (size * 0.6))),
    );
    const rect =
      e.kind === "sticky"
        ? `<rect x="${e.x}" y="${e.y}" width="${e.w}" height="${e.h}" fill="${e.fill === "none" ? "#fff2a8" : e.fill}" stroke="${e.color}" stroke-width="${e.width}"/>`
        : "";
    return `${rect}<text font-family="sans-serif" font-size="${size}" fill="${ink}">${lines.map((line, i) => `<tspan x="${e.x + 8}" y="${e.y + 8 + size + i * size * 1.3}">${escapeXML(line)}</tspan>`).join("")}</text>`;
  });
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${view.w}" height="${view.h}" viewBox="${view.x} ${view.y} ${view.w} ${view.h}"><rect x="${view.x}" y="${view.y}" width="${view.w}" height="${view.h}" fill="${CANVAS_PAPER}"/>${parts.join("")}</svg>`;
}
export async function svgPNG(svg: string): Promise<Blob> {
  const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("Cannot rasterize SVG"));
      image.src = url;
    });
    const scale = Math.min(1, 4096 / Math.max(image.width, image.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.width * scale));
    canvas.height = Math.max(1, Math.round(image.height * scale));
    canvas
      .getContext("2d")!
      .drawImage(image, 0, 0, canvas.width, canvas.height);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (b) => (b ? resolve(b) : reject(new Error("PNG export failed"))),
        "image/png",
      ),
    );
  } finally {
    URL.revokeObjectURL(url);
  }
}
export function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
