/** The goals and brief routes (kitty.web_goals; docs/rika-design.md Q14 A, Q15 A). */
import { api } from "../api";
import type { Brief, Goal, GoalStatus, Id, LinkKind, Linkable, MeasureInput } from "./goals";

const goalPath = (id: Id) => `/goals/${encodeURIComponent(String(id))}`;

export type GoalFields = { title: string; why: string; target_date: string | null; measures: MeasureInput[] };
export type GoalUpdate = Partial<GoalFields> & { revision: number; status?: GoalStatus };

export const listGoals = async () => (await api<{ goals: Goal[] }>("/goals")).goals;
export const loadGoal = async (id: Id) => (await api<{ goal: Goal }>(goalPath(id))).goal;
export const createGoal = async (fields: GoalFields) => (await api<{ goal: Goal }>("/goals", fields)).goal;
/** A stale `revision` fails with status 409. */
export const updateGoal = async (id: Id, update: GoalUpdate) => (await api<{ goal: Goal }>(goalPath(id), update)).goal;
export const deleteGoal = (id: Id, revision: number) => api<{ deleted: Id }>(`${goalPath(id)}/delete`, { revision });
export const linkItem = async (id: Id, kind: LinkKind, ref: string) => (await api<{ goal: Goal }>(`${goalPath(id)}/links`, { kind, ref })).goal;
export const unlinkItem = async (id: Id, kind: LinkKind, ref: string) => (await api<{ goal: Goal }>(`${goalPath(id)}/unlink`, { kind, ref })).goal;
/** Manual measures only; an auto measure fails with status 409. */
export const setMeasure = async (id: Id, measure: string, current: number) =>
  (await api<{ goal: Goal }>(`${goalPath(id)}/measures/${encodeURIComponent(measure)}`, { current })).goal;
export const decideMatch = async (match: Id, decision: "link" | "dismiss") =>
  (await api<{ goal: Goal }>(`/goals/matches/${encodeURIComponent(String(match))}`, { decision })).goal;
export async function findLinkable(q: string, kind: LinkKind | "" = "") {
  const query = new URLSearchParams({ q, ...(kind ? { kind } : {}) });
  return (await api<{ items: Linkable[] }>(`/goals/linkable?${query}`)).items;
}

export const loadBrief = async () => (await api<{ brief: Brief | null }>("/rika/brief")).brief;
export const markBriefSeen = async () => (await api<{ brief: Brief }>("/rika/brief/seen", {})).brief;
export const runBrief = async () => (await api<{ brief: Brief }>("/rika/brief/run", {})).brief;
