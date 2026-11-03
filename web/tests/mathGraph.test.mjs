// Run with Node >=22.6: node --experimental-strip-types --test tests/mathGraph.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { isDue, layoutGraph, reason, upNext } from "../src/lib/math/graph.ts";

const NOW = Date.parse("2026-11-02T12:00:00Z");
const T = (id, strand, prereqs, state, extra = {}) => ({ id, strand, title: id.toUpperCase(), prereqs, state, ...extra });
const TOPICS = [
  T("add", "arithmetic", [], "mastered", { due_at: "2026-11-01T00:00:00Z" }),
  T("frac", "arithmetic", ["add"], "mastered", { due_at: "2026-11-09T00:00:00Z" }),
  T("perm", "counting", ["add"], "learning", { progress: 0.5 }),
  T("prob", "probability", ["frac", "perm"], "locked"),
  T("mean", "statistics", ["frac"], "ready", { why: "Unlocked because you mastered FRAC" }),
];

test("layers follow the longest prerequisite path, so every edge points down", () => {
  const graph = layoutGraph(TOPICS);
  const at = Object.fromEntries(graph.nodes.map((node) => [node.id, node]));
  assert.deepEqual(["add", "frac", "perm", "prob", "mean"].map((id) => at[id].layer), [0, 1, 1, 2, 2]);
  for (const edge of graph.edges) assert.ok(at[edge.from].y < at[edge.to].y, `${edge.from} → ${edge.to} points up`);
  assert.equal(graph.edges.length, 5);
  // No two nodes share a spot, and everything fits the reported box.
  const spots = new Set(graph.nodes.map((node) => `${node.x},${node.y}`));
  assert.equal(spots.size, graph.nodes.length);
  for (const node of graph.nodes) assert.ok(node.x > 0 && node.x < graph.width && node.y > 0 && node.y < graph.height);
});

test("a layer is ordered by strand, arithmetic first", () => {
  const at = Object.fromEntries(layoutGraph(TOPICS).nodes.map((node) => [node.id, node]));
  assert.ok(at.frac.x < at.perm.x);
  assert.ok(at.prob.x < at.mean.x);
});

test("an edge from a mastered prerequisite into an open topic is live", () => {
  const live = Object.fromEntries(layoutGraph(TOPICS).edges.map((edge) => [`${edge.from}-${edge.to}`, edge.live]));
  assert.deepEqual(live, { "add-frac": true, "add-perm": true, "frac-prob": false, "perm-prob": false, "frac-mean": true });
});

test("only a mastered topic past its review date is due", () => {
  assert.equal(isDue(TOPICS[0], NOW), true);
  assert.equal(isDue(TOPICS[1], NOW), false);
  assert.equal(isDue(T("x", "arithmetic", [], "learning", { due_at: "2026-11-01T00:00:00Z" }), NOW), false);
});

test("every topic says why it is open or locked", () => {
  const byId = Object.fromEntries(TOPICS.map((topic) => [topic.id, topic]));
  assert.equal(reason(TOPICS[3], byId), "Locked until you master PERM");
  assert.equal(reason(TOPICS[4], byId), "Unlocked because you mastered FRAC");
  assert.equal(reason(TOPICS[0], byId), "A starting point: no prerequisites");
  assert.equal(reason(TOPICS[2], byId), "Unlocked because you mastered ADD");
});

test("up next: started lessons, then due reviews, then new topics", () => {
  assert.deepEqual(upNext(TOPICS, NOW).map((item) => [item.topic.id, item.why]), [
    ["perm", "learning · 50%"], ["add", "review due"], ["mean", "new · prerequisites done"],
  ]);
});
