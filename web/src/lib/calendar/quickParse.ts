/** Natural-language event text: "lunch with Mira fri 12:30 at Café Rose". */
export type QuickKind = "day" | "time" | "dur" | "where" | "repeat";
export type QuickSpan = { start: number; end: number; kind: QuickKind };
export type QuickEvent = {
  /** Text left once recognised pieces are removed, first letter capitalised. */
  title: string;
  /** `YYYY-MM-DD`, or null when no day was given. */
  date: string | null;
  /** Minutes since midnight. */
  start: number | null;
  end: number | null;
  /** Explicit length ("for 90m", "1h"). */
  minutes: number | null;
  where: string;
  rrule: string;
  /** The repeat as said, e.g. "every week". */
  repeat: string;
  spans: QuickSpan[];
};

const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const WEEKDAY_RE = "monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thu|friday|fri|saturday|sat|sunday|sun";
const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const BYDAY = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];
type Rule = [QuickKind | "date" | "range", RegExp];
const RULES: Rule[] = [
  ["repeat", new RegExp(`\\b(every\\s+(?:day|week|weekday|month|${WEEKDAY_RE})|daily|weekly|monthly)\\b`, "gi")],
  ["date", new RegExp(`\\b(${MONTHS.join("|")})[a-z]*\\.?\\s+(\\d{1,2})\\b`, "gi")],
  ["day", new RegExp(`\\b(today|tonight|tomorrow|tmr|(?:next\\s+)?(?:${WEEKDAY_RE}))\\b`, "gi")],
  ["range", /\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*[-–]\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b/gi],
  ["dur", /\b(?:for\s+)?(\d+(?:\.\d+)?)\s*(m|mins?|minutes|h|hrs?|hours?)\b/gi],
  ["time", /\b(?:at\s+)?(?:(\d{1,2})(?::(\d{2}))?\s*(am|pm)|(\d{1,2}):(\d{2}))\b/gi],
  ["where", /(?:\bat|@)\s+(\p{Lu}[\p{L}'’-]*(?:\s+\p{Lu}[\p{L}'’-]*)*)/gu],
];

function addDays(day: string, count: number) {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + count);
  return d.toISOString().slice(0, 10);
}
/** Clock minutes; a bare 1–6 without am/pm reads as afternoon. */
function clock(h: string | undefined, m: string | undefined, ampm: string | undefined) {
  let hours = Number(h ?? 9) % 24;
  if (ampm) hours = (hours % 12) + (ampm.toLowerCase() === "pm" ? 12 : 0);
  else if (hours > 0 && hours < 7) hours += 12;
  return hours * 60 + Number(m ?? 0);
}
const weekday = (word: string) => WEEKDAYS.findIndex((w) => w.startsWith(word.slice(0, 3)));

/** Parses `text` relative to `today` (`YYYY-MM-DD`). */
export function parseQuickEvent(text: string, today: string): QuickEvent {
  const found: (QuickSpan & { m: RegExpExecArray; rule: Rule[0] })[] = [];
  for (const [rule, re] of RULES) {
    re.lastIndex = 0;
    for (let m = re.exec(text); m; m = re.exec(text)) {
      const end = m.index + m[0].length;
      if (found.some((s) => m!.index < s.end && end > s.start)) continue;
      const kind: QuickKind = rule === "date" ? "day" : rule === "range" ? "time" : rule;
      found.push({ start: m.index, end, kind, m, rule });
    }
  }
  found.sort((a, b) => a.start - b.start);

  const todayIndex = (new Date(`${today}T12:00:00Z`).getUTCDay() + 6) % 7;
  const result: QuickEvent = { title: "", date: null, start: null, end: null, minutes: null, where: "", rrule: "", repeat: "", spans: [] };
  for (const { m, rule } of found) {
    if (rule === "repeat") {
      const word = m[1].toLowerCase().replace(/^every\s+/, "");
      result.repeat = `every ${word.replace(/ly$/, "").replace(/^dai$/, "day")}`;
      const day = weekday(word);
      result.rrule = word === "day" || word === "daily" ? "FREQ=DAILY"
        : word === "weekday" ? "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
        : word === "month" || word === "monthly" ? "FREQ=MONTHLY"
        : day >= 0 ? `FREQ=WEEKLY;BYDAY=${BYDAY[day]}` : "FREQ=WEEKLY";
    } else if (rule === "where") result.where = m[1];
    else if (rule === "dur") result.minutes = Math.round(Number(m[1]) * (m[2].toLowerCase().startsWith("h") ? 60 : 1));
    else if (rule === "date") {
      const year = Number(today.slice(0, 4));
      const iso = (y: number) => `${y}-${String(MONTHS.indexOf(m[1].toLowerCase().slice(0, 3)) + 1).padStart(2, "0")}-${m[2].padStart(2, "0")}`;
      result.date = iso(year) < addDays(today, -1) ? iso(year + 1) : iso(year);
    } else if (rule === "day") {
      const word = m[1].toLowerCase();
      if (word === "tomorrow" || word === "tmr") result.date = addDays(today, 1);
      else if (word === "today" || word === "tonight") result.date = today;
      else {
        const target = weekday(word.replace(/^next\s+/, ""));
        result.date = addDays(today, ((target - todayIndex + 7) % 7 || 7) + (word.startsWith("next") ? 7 : 0));
      }
    } else if (rule === "range") {
      result.start = clock(m[1], m[2], m[3] ?? m[6]);
      result.end = clock(m[4], m[5], m[6]);
    } else if (rule === "time") result.start = clock(m[1] ?? m[4], m[2] ?? m[5], m[3]);
  }
  result.spans = found.map(({ start, end, kind }) => ({ start, end, kind }));

  let title = "";
  let cursor = 0;
  for (const span of found) {
    title += text.slice(cursor, span.start) + " ";
    cursor = span.end;
  }
  title = (title + text.slice(cursor)).replace(/\s+/g, " ").replace(/\s(at|on|@)\s*$/i, "").trim();
  result.title = title.charAt(0).toUpperCase() + title.slice(1);
  return result;
}
