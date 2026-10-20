export type MindMapFeedbackGraph = {
  nodes: { id: string; label: string; notes?: string }[];
  edges: { from: string; to: string; label?: string }[];
};

export type MindMapFeedbackRequest = {
  title: string;
  question: string;
  graph: MindMapFeedbackGraph;
};

/** Explicit allowlist: never share geometry, URLs, sources, or linked records. */
export function mindMapFeedbackRequest(
  title: string,
  question: string,
  graph: MindMapFeedbackGraph,
): MindMapFeedbackRequest {
  return {
    title,
    question,
    graph: {
      nodes: graph.nodes.map((node) => ({
        id: node.id,
        label: node.label,
        ...(node.notes !== undefined ? { notes: node.notes } : {}),
      })),
      edges: graph.edges.map((edge) => ({
        from: edge.from,
        to: edge.to,
        ...(edge.label !== undefined ? { label: edge.label } : {}),
      })),
    },
  };
}
