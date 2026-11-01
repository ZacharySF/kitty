/** `GET /api/flashcards/stats`: read-only memory statistics for a deck scope. */
export type MemoryStats = {
  forecast: { day: string; review: number; learning: number }[];
  retention: {
    decay: number;
    target: number;
    stability: { weak: number; medium: number; strong: number } | null;
    measured: { t: number; r: number; n: number }[];
  };
  maturity: { new: number; learning: number; young: number; mature: number };
  heatmap: { day: string; reviews: number }[];
  streak: number;
  queue: {
    learning: number;
    review: number;
    new: number;
    suspended: number;
    leeches: number;
    buried: number;
    seconds_per_card: number | null;
  };
};

/** "Mon 2 Nov" for an ISO calendar day. */
export const dayLabel = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
