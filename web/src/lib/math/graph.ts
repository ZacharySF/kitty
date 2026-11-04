/**
 * The math topic graph (design Q3 B): a prerequisite DAG whose topics are locked until
 * their prerequisites are mastered, then ready, learning and mastered, with a spaced
 * review date once mastered. This lays it out top to bottom for the Topics drawer.
 */
export type TopicState = "locked" | "ready" | "learning" | "mastered";
export type Topic = {
  id: string;
  strand: string;
  title: string;
  prereqs: string[];
  state: TopicState;
  /** Learning progress 0–1. */
  progress?: number;
  due_at?: string | null;
  stability_days?: number | null;
  why?: string | null;
};
export type GraphNode = Topic & { x: number; y: number; layer: number; due: boolean };
export type GraphEdge = { from: string; to: string; d: string; live: boolean };

export const NODE_R = 13;
const COLUMN = 88;
const ROW = 92;
/** Room for a node's two-line label; edges leave below it. */
const LABEL = 30;
const PAD = 34;
/** Wider layers wrap so the graph keeps to the drawer's width. */
const PER_ROW = 4;
/** Past this many topics the drawer opens on one strand: the whole graph is too tall to read. */
const WHOLE = 30;
export const STRANDS: Record<string, string> = {
  arithmetic: "Arithmetic and estimation",
  counting: "Counting",
  probability: "Probability",
  ev: "Expected value and variance",
  statistics: "Statistics",
  linalg: "Linear algebra",
  calculus: "Calculus",
  discrete: "Discrete math",
};

export function strandLabel(strand: string): string {
  return STRANDS[strand] ?? strand.charAt(0).toUpperCase() + strand.slice(1);
}

/** One strand's topics with their prerequisites from other strands (`outside`, drawn as context); "" is every topic. */
export function strandView(topics: Topic[], strand: string): { topics: Topic[]; outside: string[] } {
  if (!strand) return { topics, outside: [] };
  const inside = new Set(topics.filter((topic) => topic.strand === strand).map((topic) => topic.id));
  const outside = new Set(topics.filter((topic) => inside.has(topic.id)).flatMap((topic) => topic.prereqs.filter((id) => !inside.has(id))));
  return { topics: topics.filter((topic) => inside.has(topic.id) || outside.has(topic.id)), outside: topics.filter((topic) => outside.has(topic.id)).map((topic) => topic.id) };
}

/** A small graph opens whole; a large one on the strand of the selected topic. */
export function defaultStrand(topics: Topic[], selected: string): string {
  if (topics.length <= WHOLE) return "";
  return (topics.find((topic) => topic.id === selected) ?? topics[0])?.strand ?? "";
}

export function isDue(topic: Topic, now: number): boolean {
  return topic.state === "mastered" && !!topic.due_at && Date.parse(topic.due_at) <= now;
}

function names(ids: string[], byId: Record<string, Topic>): string {
  const titles = ids.map((id) => byId[id]?.title ?? id);
  return titles.length > 1 ? `${titles.slice(0, -1).join(", ")} and ${titles[titles.length - 1]}` : titles.join("");
}

/** Every topic says why it is open or locked; the backend's reason wins when it gives one. */
export function reason(topic: Topic, byId: Record<string, Topic>): string {
  if (topic.why) return topic.why;
  if (topic.state === "locked") return `Locked until you master ${names(topic.prereqs.filter((id) => byId[id]?.state !== "mastered"), byId)}`;
  if (!topic.prereqs.length) return "A starting point: no prerequisites";
  return `Unlocked because you mastered ${names(topic.prereqs, byId)}`;
}

/** What to practise next: started topics, then due reviews, then new ones. */
export function upNext(topics: Topic[], now: number): { topic: Topic; why: string }[] {
  return [
    ...topics.filter((t) => t.state === "learning").sort((a, b) => (b.progress ?? 0) - (a.progress ?? 0))
      .map((topic) => ({ topic, why: `learning · ${Math.round((topic.progress ?? 0) * 100)}%` })),
    ...topics.filter((t) => isDue(t, now)).map((topic) => ({ topic, why: "review due" })),
    ...topics.filter((t) => t.state === "ready").map((topic) => ({ topic, why: "new · prerequisites done" })),
  ].slice(0, 3);
}

/** Layers by longest prerequisite path, ordered by strand then by where their prerequisites sit. */
export function layoutGraph(topics: Topic[], now = Date.now()): { nodes: GraphNode[]; edges: GraphEdge[]; width: number; height: number } {
  const byId = Object.fromEntries(topics.map((topic) => [topic.id, topic]));
  const strands = [...new Set(topics.map((topic) => topic.strand))];
  const depth: Record<string, number> = {};
  const layerOf = (id: string, trail: string[] = []): number => {
    if (depth[id] !== undefined) return depth[id];
    if (trail.includes(id)) throw new Error(`The topic graph has a cycle through ${id}`);
    const prereqs = byId[id].prereqs.filter((p) => byId[p]);
    return (depth[id] = prereqs.length ? 1 + Math.max(...prereqs.map((p) => layerOf(p, [...trail, id]))) : 0);
  };
  const layers: Topic[][] = [];
  for (const topic of topics) (layers[layerOf(topic.id)] ??= []).push(topic);

  const width = PAD * 2 + COLUMN * Math.min(PER_ROW, Math.max(1, ...layers.map((layer) => layer.length)));
  const placed: Record<string, GraphNode> = {};
  let row = 0;
  layers.forEach((layer, index) => {
    const centre = (topic: Topic) => {
      const xs = topic.prereqs.map((p) => placed[p]?.x).filter((x): x is number => x !== undefined);
      return xs.length ? xs.reduce((sum, x) => sum + x, 0) / xs.length : 0;
    };
    const ordered = [...layer].sort((a, b) => strands.indexOf(a.strand) - strands.indexOf(b.strand) || centre(a) - centre(b));
    for (let start = 0; start < ordered.length; start += PER_ROW, row += 1) {
      const line = ordered.slice(start, start + PER_ROW);
      const left = (width - line.length * COLUMN) / 2;
      line.forEach((topic, column) => {
        placed[topic.id] = { ...topic, layer: index, x: left + (column + 0.5) * COLUMN, y: PAD + row * ROW + NODE_R, due: isDue(topic, now) };
      });
    }
  });
  const nodes = topics.map((topic) => placed[topic.id]);
  const edges = nodes.flatMap((to) =>
    to.prereqs.filter((id) => placed[id]).map((id) => {
      const from = placed[id];
      const [y1, y2] = [from.y + NODE_R + LABEL, to.y - NODE_R - 5];
      const bend = (y2 - y1) / 2;
      return {
        from: id,
        to: to.id,
        d: `M${from.x} ${y1} C${from.x} ${y1 + bend} ${to.x} ${y2 - bend} ${to.x} ${y2}`,
        live: from.state === "mastered" && to.state !== "locked",
      };
    }),
  );
  return { nodes, edges, width, height: PAD + row * ROW + 6 };
}
