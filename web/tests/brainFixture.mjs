// Synthetic `GET /api/brain` payloads for the Brain browser tests and visual QA.
// Deterministic for a given seed; shaped like the backend contract (records first,
// edits newest-first, every edit carries `at`). No real workspace data.

const SECTIONS = [
  { id: "notes", label: "Notes", group: "Study", weight: 9, types: ["page", "notebook", "folder"], edits: ["save", "rename", "move"] },
  { id: "flashcards", label: "Flashcards", group: "Study", weight: 8, types: ["card", "deck"], edits: ["review", "save"] },
  { id: "calendar", label: "Calendar", group: "Plan", weight: 6, types: ["event", "task"], edits: ["reschedule", "complete", "save"] },
  { id: "study", label: "Study", group: "Study", weight: 5, types: ["session", "subject"], edits: ["session", "save"] },
  { id: "jobs", label: "Jobs", group: "Job search", weight: 5, types: ["job"], edits: ["status", "save"] },
  { id: "quick-notes", label: "Quick notes", group: "Plan", weight: 4, types: ["quick note"], edits: ["save"] },
  { id: "documents", label: "Documents", group: "Study", weight: 3, types: ["document"], edits: ["import", "save"] },
  { id: "applications", label: "Applications", group: "Job search", weight: 3, types: ["application"], edits: ["status"] },
  { id: "reminders", label: "Reminders", group: "Plan", weight: 2, types: ["reminder"], edits: ["complete", "save"] },
  { id: "opportunities", label: "Opportunities", group: "Job search", weight: 2, types: ["opportunity"], edits: ["status"] },
  { id: "schema", label: "Mind maps", group: "Study", weight: 1, types: ["mind map"], edits: ["save"] },
];
const WORDS = ["Linear algebra", "Thesis draft", "Cell culture", "Sourdough", "Running log", "Grant", "Ceramics", "Imaging",
  "Conference", "Side project", "Weekly plan", "Reading list", "Lab meeting", "Interview prep", "Portfolio", "Budget"];

function random(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A brain response with `records` records and `edits` edits (about a quarter records by default). */
export function syntheticBrain({ total = 20000, records = Math.round(total * 0.26), seed = 7, now = Date.UTC(2026, 9, 31) } = {}) {
  const rand = random(seed);
  const pick = (items) => items[Math.floor(rand() * items.length)];
  const weightTotal = SECTIONS.reduce((sum, section) => sum + section.weight, 0);
  const weighted = () => {
    let roll = rand() * weightTotal;
    for (const section of SECTIONS) if ((roll -= section.weight) <= 0) return section;
    return SECTIONS[0];
  };
  const span = 730 * 86_400_000;
  const nodes = [];
  const bySection = new Map(SECTIONS.map((section) => [section.id, []]));
  const counters = new Map();
  for (let index = 0; index < records; index += 1) {
    // The first record of each section is pinned so tests can address it.
    const section = index < SECTIONS.length ? SECTIONS[index] : weighted();
    const type = section.types[0] === "page" && index >= SECTIONS.length ? pick(section.types) : section.types[0];
    const key = `${section.id}.${type}`;
    const number = counters.get(key) ?? 0;
    counters.set(key, number + 1);
    const siblings = bySection.get(section.id);
    const created = now - Math.pow(rand(), 0.7) * span;
    const node = {
      id: `${key}:${number}`,
      kind: "record",
      section: section.id,
      type,
      label: index < SECTIONS.length ? `${section.label} anchor` : `${pick(WORDS)} ${number}`,
      at: rand() < 0.05 ? null : new Date(created).toISOString(),
      parent: siblings.length && rand() < 0.3 ? pick(siblings).id : null,
    };
    siblings.push(node);
    nodes.push(node);
  }
  const edits = [];
  for (let index = 0; index < total - records; index += 1) {
    const section = weighted();
    const parent = rand() < 0.9 ? pick(bySection.get(section.id)) : null;
    const verb = pick(section.edits);
    edits.push({
      kind: "edit",
      section: section.id,
      type: verb,
      label: `${verb[0].toUpperCase()}${verb.slice(1)} · ${parent ? parent.label : section.label}`.slice(0, 80),
      at: new Date(now - Math.pow(rand(), 1.6) * span).toISOString(),
      parent: parent?.id ?? null,
    });
  }
  edits.sort((a, b) => b.at.localeCompare(a.at));
  edits.forEach((edit, index) => nodes.push({ id: `edit:${index}`, ...edit }));
  const sections = SECTIONS.map(({ id, label, group }) => ({
    id, label, group,
    records: nodes.filter((node) => node.section === id && node.kind === "record").length,
    edits: nodes.filter((node) => node.section === id && node.kind === "edit").length,
  }));
  return {
    generated_at: new Date(now).toISOString(),
    sections,
    nodes,
    total: { records, edits: total - records },
    truncated: false,
  };
}
