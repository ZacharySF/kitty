/**
 * rika!'s icon set: 24px hairline outlines (stroke ICON_STROKE). Navigation icons
 * turn one detail into a tiny cherry blossom, listed in `accents` as [x, y, radius];
 * control glyphs (arrows, close, check…) stay plain so they read as controls.
 * Paths drop the line a blossom sits on, so petals never cross strokes.
 */
export type IconDef = { path: string; accents: [number, number, number][] };

export const ICON_STROKE = 1.25;

/** One sakura petal pointing up from the origin, length 10; five at 72° make a blossom. */
export const PETAL = "M0 0C-4.6-2.4-5.2-7.6-1.9-10.2L0-8.6L1.9-10.2C5.2-7.6 4.6-2.4 0 0Z";
export const PETAL_ANGLES = [0, 72, 144, 216, 288] as const;

const plain = (path: string): IconDef => ({ path, accents: [] });

export const ICONS: Record<string, IconDef> = {
  today: { path: "M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z", accents: [[18.8, 4.2, 3.1]] },
  atlas: {
    path: "M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18ZM3.5 9.5c4 2 13 2 17 0M3.5 14.5c4-2 13-2 17 0M12 3c-3.5 4-3.5 14 0 18M12 3c3.5 4 3.5 14 0 18",
    accents: [[19.6, 4.4, 2.6]],
  },
  goals: { path: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM17.5 12a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0", accents: [[12, 12, 2.8]] },
  calendar: { path: "M4 5h16v16H4ZM4 10h16M8 3v4M16 3v4M8 14h2M14 14h2M8 18h2", accents: [[15, 18, 3]] },
  opportunities: { path: "m12 3 3 6 6 3-6 3-3 6-3-6-6-3 6-3Z", accents: [[19.2, 4.8, 2.8]] },
  study: { path: "M3 4h7l2 2 2-2h7v16h-7l-2 2-2-2H3ZM12 6v16M6 9h3M15 9h3", accents: [[16.5, 15, 3.2]] },
  math: { path: "M2.5 13H5l3.5 7L13.5 4h8M14.5 9.5l4 5M18.5 9.5l-4 5", accents: [[19.5, 19.4, 2.6]] },
  flashcards: { path: "M7 3h14v14H7ZM3 7v14h14M10 8h8M10 12h5", accents: [[17.9, 13.9, 2.9]] },
  notes: { path: "M4 3h16v18H4ZM8 7h8M8 11h8M8 15h4", accents: [[15.8, 16.4, 3]] },
  "quick-notes": { path: "M5 4h14v11l-5 5H5ZM14 20v-5h5M8 9h8M8 13h4", accents: [[18.6, 4.6, 2.6]] },
  schema: { path: "M3 16h4v4H3ZM17 16h4v4h-4ZM12 9.6V12M5 16v-4h14v4", accents: [[12, 5.6, 3.9]] },
  ai: { path: "M5 7h14v13H5ZM8 11h.01M16 11h.01M8 16h8M2 10v6M22 10v6", accents: [[12, 3.6, 2.6]] },
  terminal: { path: "M3 4h18v16H3Zm4 5 3 3-3 3", accents: [[15.6, 14.8, 3]] },
  // The brand mark sits on a pastel squircle in the top bar; a blossom there would vanish.
  kitty: plain("M4 10V3l5 4a10 10 0 0 1 6 0l5-4v7a8 8 0 1 1-16 0ZM8 12h.01M16 12h.01m-6 4 2 1 2-1"),
  jobs: { path: "m21 21-4.4-4.4M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0", accents: [[10.5, 10.5, 3.8]] },
  queue: { path: "M4 5h16M4 12h10M4 19h10", accents: [[19, 12, 3]] },
  applications: { path: "M8 5V3h8v2M3 7h18v14H3ZM3 12h9.6M14.4 12H21", accents: [[12, 13.6, 2.4]] },
  favorites: { path: "m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z", accents: [[12, 12.6, 2.6]] },
  resumes: { path: "M14 2H5v20h14V7Zm0 0v5h5M8 12h8M8 16h8", accents: [[16.6, 4.4, 2.3]] },
  sources: { path: "M20 12a8 8 0 1 1-16 0 8 8 0 0 1 16 0", accents: [[12, 12, 4]] },
  inbox: { path: "M4 3h16l2 13v5H2v-5Zm-2 13h6l2 3h4l2-3h6", accents: [[12, 9, 3]] },
  settings: { path: "M4 7h.6M11.4 7H20M4 17h8.6M19.4 17H20", accents: [[8, 7, 3.2], [16, 17, 3.2]] },
  reports: { path: "M4 3v18h17M8 16v-4M13 16V7M18 16v-5", accents: [[18, 6.2, 2.6]] },
  mail: { path: "M3 5h18v14H3Zm0 0 9 8 9-8", accents: [[18.4, 16.2, 2.5]] },
  questions: { path: "M8 8a4 4 0 1 1 7 3c-2 1-3 2-3 4", accents: [[12, 19.2, 2.4]] },
  arrow: plain("M4 12h16m-6-6 6 6-6 6"),
  external: plain("M14 3h7v7m0-7L10 14M10 3H3v18h18v-7"),
  close: plain("m6 6 12 12M6 18 18 6"),
  refresh: plain("M20 7v5h-5M4 17v-5h5M5.2 7a8 8 0 0 1 13.2-2L20 8M4 16l1.6 3A8 8 0 0 0 19 17"),
  check: plain("m5 12 4 4L19 6"),
  download: plain("M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"),
  location: plain("M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 0 1 14 0ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0"),
  menu: plain("M4 6h16M4 12h16M4 18h16"),
  moon: plain("M20 14a8.5 8.5 0 0 1-10-10 8.5 8.5 0 1 0 10 10"),
  shield: plain("m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Zm-4 9 3 3 5-6"),
  undo: plain("M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3"),
  redo: plain("m15 14 5-5-5-5m5 5H9a5 5 0 0 0 0 10h3"),
  plus: plain("M12 5v14M5 12h14"),
  more: plain("M5 11a1 1 0 1 1 0 2 1 1 0 0 1 0-2ZM12 11a1 1 0 1 1 0 2 1 1 0 0 1 0-2ZM19 11a1 1 0 1 1 0 2 1 1 0 0 1 0-2Z"),
  // Canvas drawing tools (Notes), keyed `tool-<tool id>`.
  "tool-select": plain("M5 3l13 8-6 1.4L9 18.5Z"),
  "tool-lasso": plain("M12 4c4.4 0 8 2.4 8 5.5S16.4 15 12 15s-8-2.4-8-5.5S7.6 4 12 4ZM7 13.5c-1.6 1.6-.6 3.5 1.2 3.5 1.5 0 1.8 1.9.3 3.5"),
  "tool-pan": plain("M8 13V6.5a1.5 1.5 0 0 1 3 0V11m0-6.5a1.5 1.5 0 0 1 3 0V11m0-4.5a1.5 1.5 0 0 1 3 0V14c0 3.9-2.5 7-6 7-2.4 0-3.9-1.1-5.3-3.3L3.9 13.9a1.5 1.5 0 0 1 2.5-1.7L8 14"),
  "tool-pen": plain("M4 20l1.2-4.2L15.8 5.2a2 2 0 0 1 3 3L8.2 18.8ZM14 7l3 3"),
  "tool-highlighter": plain("M15 4l5 5-8 8H7v-5ZM7 17l-3 3M4 21h9"),
  "tool-eraser": plain("M8 20h12M4.6 15.4l9.8-9.8a2 2 0 0 1 2.8 0l2.2 2.2a2 2 0 0 1 0 2.8L10 20H9.2ZM9 11l4 4"),
  "tool-text": plain("M5 7V5h14v2M12 5v14M9 19h6"),
  "tool-sticky": plain("M5 4h14v10l-6 6H5ZM13 20v-6h6"),
  "tool-rect": plain("M4 6h16v12H4Z"),
  "tool-ellipse": plain("M12 5c4.4 0 8 3.1 8 7s-3.6 7-8 7-8-3.1-8-7 3.6-7 8-7Z"),
  "tool-line": plain("M5 19 19 5"),
  "tool-arrow": plain("M5 19 19 5M10 5h9v9"),
  "tool-connector": plain("M6 18a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM18 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM8 15.5c4 0 4-7.5 8-7.5"),
  // Who made an edit (actors.ts), keyed `actor-<look id>`: the person, an agent and the clients
  // that run agents (omp's π, a starburst, code brackets, the cat), an app, rika!'s gear, a device.
  "actor-user": plain("M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4.5 20.5a7.5 7.5 0 0 1 15 0"),
  "actor-agent": plain("M5 8h14v12H5ZM12 4v4M9 13h.01M15 13h.01M9.5 17h5"),
  "actor-omp": plain("M4 7h16M9 7v13M15 7v10a3 3 0 0 0 3 3h1"),
  "actor-claude": plain("M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4"),
  "actor-codex": plain("M8 6 3 12l5 6M16 6l5 6-5 6M13.5 4l-3 16"),
  "actor-rika": plain("M4 10V3l5 4a10 10 0 0 1 6 0l5-4v7a8 8 0 1 1-16 0ZM9 12h.01M15 12h.01"),
  "actor-client": plain("M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0ZM12 17v4"),
  "actor-system": plain("M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.2 2.2M16.2 16.2l2.2 2.2M5.6 18.4l2.2-2.2M16.2 7.8l2.2-2.2"),
  "actor-peer": plain("M3 5h13v9H3ZM1 17h17M19 9h3v11h-3Z"),
};
