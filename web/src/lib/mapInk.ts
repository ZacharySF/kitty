/** Q69: pen input, ink, lasso and eraser geometry for the mind-map editor. Ink points are [x, y, pressure]. */
export type InkPoint = [number, number, number];
export type Box = { id: string; x: number; y: number; w: number; h: number };
export type PenAction = "ink" | "lasso" | "erase" | "point" | "ignore";

/**
 * What a pointer press does. The pen's eraser end (button 5 / buttons 32) erases ink, its barrel
 * button (button 2) draws a lasso, and in ink mode the pen or a mouse draws; a finger keeps
 * selecting and panning, except while the pen is near, when it is a resting palm.
 */
export function penAction(event: { pointerType: string; button: number; buttons: number }, inkMode: boolean, penNear: boolean): PenAction {
  if (event.pointerType === "touch") return penNear ? "ignore" : "point";
  if (event.pointerType === "pen") {
    if (event.button === 5 || (event.buttons & 32) !== 0) return "erase";
    if (event.button === 2 || (event.buttons & 2) !== 0) return "lasso";
  }
  return inkMode && event.button === 0 ? "ink" : "point";
}

function segmentDistance(x: number, y: number, a: readonly number[], b: readonly number[]) {
  const dx = b[0] - a[0], dy = b[1] - a[1], length = dx * dx + dy * dy;
  const t = length ? Math.max(0, Math.min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / length)) : 0;
  return Math.hypot(x - a[0] - t * dx, y - a[1] - t * dy);
}

/** Ramer–Douglas–Peucker: drops points within `epsilon` of the simplified line; kept points keep their pressure. */
export function simplify(points: InkPoint[], epsilon: number): InkPoint[] {
  if (points.length < 3) return points.map((p) => [...p] as InkPoint);
  const keep = new Uint8Array(points.length);
  keep[0] = keep[points.length - 1] = 1;
  const spans: [number, number][] = [[0, points.length - 1]];
  while (spans.length) {
    const [a, b] = spans.pop()!;
    let far = -1, distance = epsilon;
    for (let i = a + 1; i < b; i++) {
      const d = segmentDistance(points[i][0], points[i][1], points[a], points[b]);
      if (d > distance) { distance = d; far = i; }
    }
    if (far >= 0) { keep[far] = 1; spans.push([a, far], [far, b]); }
  }
  return points.filter((_, i) => keep[i]).map((p) => [...p] as InkPoint);
}

const n = (value: number) => Math.round(value * 100) / 100;

/** A filled outline whose width follows pen pressure, with round ends; `width` is the full-pressure width. */
export function inkOutline(points: InkPoint[], width: number): string {
  if (!points.length) return "";
  const radius = (p: InkPoint) => (width / 2) * (0.2 + 0.8 * Math.max(0, Math.min(1, p[2])));
  if (points.length === 1) {
    const [x, y] = points[0], r = n(radius(points[0]));
    return `M${n(x - r)},${n(y)} A${r} ${r} 0 1 0 ${n(x + r)},${n(y)} A${r} ${r} 0 1 0 ${n(x - r)},${n(y)} Z`;
  }
  const left: string[] = [], right: string[] = [];
  let nx = 0, ny = 1;
  for (let i = 0; i < points.length; i++) {
    const a = points[Math.max(0, i - 1)], b = points[Math.min(points.length - 1, i + 1)];
    const tx = b[0] - a[0], ty = b[1] - a[1], length = Math.hypot(tx, ty);
    if (length) { nx = -ty / length; ny = tx / length; }
    const r = radius(points[i]), [x, y] = points[i];
    left.push(`${n(x + nx * r)},${n(y + ny * r)}`);
    right.push(`${n(x - nx * r)},${n(y - ny * r)}`);
  }
  const end = n(radius(points[points.length - 1])), start = n(radius(points[0]));
  return `M${left.join(" L")} A${end} ${end} 0 0 0 ${right.reverse().join(" L")} A${start} ${start} 0 0 0 ${left[0]} Z`;
}

/** The box nearest the middle of a stroke (0 when inside it); ties go to the closer box centre. */
export function nearestBox(boxes: Box[], points: InkPoint[]): string | undefined {
  if (!points.length) return undefined;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  let best: string | undefined, edge = Infinity, middle = Infinity;
  for (const b of boxes) {
    const d = Math.hypot(Math.max(b.x - cx, 0, cx - b.x - b.w), Math.max(b.y - cy, 0, cy - b.y - b.h));
    const m = Math.hypot(b.x + b.w / 2 - cx, b.y + b.h / 2 - cy);
    if (d < edge || (d === edge && m < middle)) { best = b.id; edge = d; middle = m; }
  }
  return best;
}

/** Boxes whose centre lies inside the closed loop. */
export function lassoed(boxes: Box[], loop: readonly (readonly number[])[]): string[] {
  const inside = (x: number, y: number) => {
    let hit = false;
    for (let i = 0, j = loop.length - 1; i < loop.length; j = i++) {
      const a = loop[i], b = loop[j];
      if (a[1] > y !== b[1] > y && x < ((b[0] - a[0]) * (y - a[1])) / (b[1] - a[1]) + a[0]) hit = !hit;
    }
    return hit;
  };
  return loop.length < 3 ? [] : boxes.filter((b) => inside(b.x + b.w / 2, b.y + b.h / 2)).map((b) => b.id);
}

/** Whether the eraser at `point` touches a stroke drawn through `points` (same coordinates). */
export function strokeHit(points: InkPoint[], point: readonly number[], tolerance: number): boolean {
  if (points.length === 1) return Math.hypot(points[0][0] - point[0], points[0][1] - point[1]) <= tolerance;
  for (let i = 1; i < points.length; i++) if (segmentDistance(point[0], point[1], points[i - 1], points[i]) <= tolerance) return true;
  return false;
}
