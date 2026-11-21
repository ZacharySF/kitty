import type { Job } from "../api";
import type { View } from "../workspace";

/** One track's week in `GET /api/dashboard`. */
export type WeekTrack = { track: string; weekly_target: number; applied_this_week: number; remaining: number };
/** A due reminder in `GET /api/dashboard` (through today's end). */
export type DueReminder = { id: number; job_id: number | null; label: string; due_at: string };
/** A status change a mail check proposed, waiting for review (`GET /api/mail`). */
export type Proposal = { event_id: number; company: string; title: string; current_status: string; to_status: string; occurred_at: string; evidence: string | null };

export type WeekGoal = { sent: number; target: number; remaining: number; tracks: { name: string; label: string; sent: number; target: number }[] };
export type DeadlineRow = { key: string; date: string; title: string; detail: string; kind: "deadline" | "reminder"; jobId: number | null; overdue: boolean };
export type Suggestion = {
  title: string;
  body: string;
  /** What "Ask Rika" sends about it. */
  prompt: string;
  action: { label: string; view?: View; job?: number };
};

/** `software_engineering` -> `Software engineering`. */
export function trackLabel(name: string): string {
  const words = name.replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : name;
}

/** This week's application goal across the tracks that set one. */
export function weekGoal(weekly: readonly WeekTrack[]): WeekGoal {
  const tracks = weekly.filter((item) => item.weekly_target > 0)
    .map((item) => ({ name: item.track, label: trackLabel(item.track), sent: item.applied_this_week, target: item.weekly_target }));
  const sent = tracks.reduce((sum, item) => sum + item.sent, 0);
  const target = tracks.reduce((sum, item) => sum + item.target, 0);
  const remaining = tracks.reduce((sum, item) => sum + Math.max(0, item.target - item.sent), 0);
  return { sent, target, remaining, tracks };
}

function addDays(day: string, days: number): string {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

/** "Fri" for a YYYY-MM-DD day. */
export function weekday(day: string): string {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" });
}

/**
 * The next seven days: postings that close from today on, and reminders due by today's end
 * (an older one shows as overdue, on today), soonest first.
 */
export function deadlinesThisWeek(today: string, deadlines: readonly Job[], reminders: readonly DueReminder[]): DeadlineRow[] {
  const last = addDays(today, 6);
  const rows: DeadlineRow[] = [];
  for (const job of deadlines) {
    const date = job.deadline_at?.slice(0, 10);
    if (!date || date < today || date > last) continue;
    rows.push({ key: `job-${job.id}`, date, title: job.company, detail: `${job.title} · closes`, kind: "deadline", jobId: job.id, overdue: false });
  }
  for (const reminder of reminders) {
    const due = reminder.due_at.slice(0, 10);
    const [company, ...rest] = reminder.label.split(" - ");
    rows.push({
      key: `reminder-${reminder.id}`, date: due < today ? today : due, title: rest.length ? company : reminder.label,
      detail: rest.length ? `${rest.join(" - ")} · reminder` : "Reminder", kind: "reminder", jobId: reminder.job_id, overdue: due < today,
    });
  }
  return rows.sort((a, b) => a.date.localeCompare(b.date) || a.kind.localeCompare(b.kind));
}

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

/** Rika's one suggestion for the week: the most useful next step the overview's data shows. */
export function suggestion({ today, goal, matches, emails, deadlines }: {
  today: string;
  goal: WeekGoal;
  matches: readonly Job[];
  emails: number;
  deadlines: readonly DeadlineRow[];
}): Suggestion {
  const last = addDays(today, 6);
  const closing = matches.find((job) => {
    const date = job.deadline_at?.slice(0, 10);
    return date && date >= today && date <= last;
  });
  const short = goal.remaining > 0 ? `You're ${plural(goal.remaining, "application")} short of this week's goal. ` : "";
  if (closing && goal.remaining > 0) {
    const day = weekday(closing.deadline_at!.slice(0, 10));
    return {
      title: `Apply to ${closing.company} before ${day}`,
      body: `${short}${closing.company}'s ${closing.title} closes ${day} and is one of your strongest new matches.`,
      prompt: `Help me apply to ${closing.company}'s ${closing.title} before it closes.`,
      action: { label: "Open the job", job: closing.id },
    };
  }
  if (emails > 0)
    return {
      title: `Review ${plural(emails, "status email")}`,
      body: `A mail check found ${emails === 1 ? "a change" : "changes"} to your applications. Nothing moves until you accept ${emails === 1 ? "it" : "them"}.`,
      prompt: "Which of my waiting status emails should I accept?",
      action: { label: "Review emails", view: "mail" },
    };
  const due = deadlines.filter((row) => row.kind === "reminder").length;
  if (due > 0)
    return {
      title: `${plural(due, "reminder")} due`,
      body: "Clear what's due before looking at new jobs.",
      prompt: "What do my due reminders need from me?",
      action: { label: "Open reminders", view: "reminders" },
    };
  if (goal.remaining > 0 && matches.length) {
    const [best] = matches;
    return {
      title: `Look at ${best.company}'s ${best.title}`,
      body: `${short}It's your strongest new match.`,
      prompt: `Is ${best.company}'s ${best.title} a good fit for me?`,
      action: { label: "Open the job", job: best.id },
    };
  }
  if (goal.remaining > 0)
    return {
      title: `Find ${plural(goal.remaining, "more job")} to apply to`,
      body: `${short}Your new jobs are the place to start.`,
      prompt: "Which new jobs should I apply to this week?",
      action: { label: "See new jobs", view: "inbox" },
    };
  return {
    title: "You're on track this week",
    body: goal.target ? "This week's goal is met. Polish a resume for the next one." : "Set a weekly goal on a track to see your progress here.",
    prompt: "What should I do next in my job search?",
    action: { label: "Open resumes", view: "workshop" },
  };
}
