// Browser fixture for tests/graphRenderers.browser.test.mjs. Mounts the shared
// graph engine with synthetic nodes and records every callback it raises.
import { mountGraph } from "/src/lib/graph3d.ts";

const GROUPS = { Plan: ["Calendar", "Reminders", "Quick notes"], Study: ["Notes", "Flashcards", "Library"], "Job search": ["Jobs", "Queue", "Pipeline"] };

function sample() {
  const nodes = [];
  const links = [];
  for (const [group, items] of Object.entries(GROUPS)) {
    const hub = `group:${group}`;
    nodes.push({ id: hub, label: group, group, kind: "hub", size: 7 });
    for (const item of items) {
      const id = `view:${item.toLowerCase().replace(/ /g, "-")}`;
      nodes.push({ id, label: item, group, kind: "hub", size: 3 });
      links.push({ source: hub, target: id });
    }
  }
  links.push({ source: "view:notes", target: "view:calendar", weight: 0.8 });
  return { nodes, links };
}

const stage = document.getElementById("stage");
const events = [];
let data = sample();
let handle = null;

window.graphHarness = {
  events,
  async mount(look, options = {}) {
    handle?.destroy();
    events.length = 0;
    data = sample();
    handle = await mountGraph(stage, {
      ...data,
      layout: options.layout ?? "clusters",
      reducedMotion: options.reducedMotion ?? true,
      look,
      onSelect: (node) => events.push(["select", node.id]),
      onHover: (node) => events.push(["hover", node?.id ?? null]),
      onContextMenu: (node, event) => {
        event.preventDefault();
        events.push(["context", node.id]);
        if (options.deleteOnContextMenu) {
          data = { nodes: data.nodes.filter((item) => item.id !== node.id), links: data.links.filter((link) => link.source !== node.id && link.target !== node.id) };
          handle.update(data);
        }
      },
      onDragEnd: (node, point) => events.push(["dragend", node.id, Math.round(point.clientX), Math.round(point.clientY)]),
    });
    await window.graphHarness.settled();
    return handle.look;
  },
  /** Resolves once every node's viewport position is unchanged across several frames. */
  async settled() {
    const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
    let previous = "";
    let stable = 0;
    for (let index = 0; index < 600 && stable < 6; index += 1) {
      await frame();
      const current = JSON.stringify(data.nodes.map((node) => handle.nodePoint(node.id)));
      stable = current === previous && !current.includes("null") ? stable + 1 : 0;
      previous = current;
    }
    if (stable < 6) throw new Error("graph never settled");
  },
  ids: () => data.nodes.map((node) => node.id),
  point: (id) => handle.nodePoint(id),
  nodeAt: (x, y) => handle.nodeAt(x, y)?.id ?? null,
  focus: (id) => handle.focus(id),
  setActive: (active) => handle.setActive(active),
  setLook: async (look) => { await handle.setLook(look); await window.graphHarness.settled(); return handle.look; },
  look: () => handle.look,
  /** Resizes the stage as a layout change would (dock shown/hidden, fullscreen). */
  resize: async (width, height) => {
    stage.style.width = `${width}px`;
    stage.style.height = `${height}px`;
    await window.graphHarness.settled();
  },
  canvasKind: () => {
    const canvas = stage.querySelector("canvas");
    if (!canvas) return null;
    return canvas.dataset.graphLook ?? "3d";
  },
  stageRect: () => stage.getBoundingClientRect().toJSON(),
};
window.dispatchEvent(new Event("graph-harness-ready"));
