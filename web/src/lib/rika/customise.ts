/**
 * Rika's page → Customise: plain choices kept as preference sentences in Rika's memory
 * (they are part of her instructions, so they take effect on the next message). Pure, so
 * Node tests can import it.
 */
export type Choice = { id: string; label: string; sentence: string | null };
export type Group = { id: "length" | "tone"; label: string; choices: Choice[] };

export const GROUPS: Group[] = [
  { id: "length", label: "Replies", choices: [
    { id: "short", label: "Short", sentence: "Keep replies short" },
    { id: "normal", label: "Normal", sentence: null },
    { id: "detailed", label: "Detailed", sentence: "Give detailed replies and explain the reasoning" },
  ] },
  { id: "tone", label: "Tone", choices: [
    { id: "plain", label: "Plain", sentence: null },
    { id: "friendly", label: "Friendly", sentence: "Use a warm, friendly tone" },
    { id: "formal", label: "Formal", sentence: "Use a formal tone" },
  ] },
];

const same = (a: string, b: string) => a.trim().replace(/[.!]+$/, "").toLowerCase() === b.toLowerCase();

/** The group's choice the saved preferences say: the first of its sentences found, else the one without a sentence. */
export function chosen(group: Group, preferences: readonly { text: string }[]): Choice {
  return group.choices.find((choice) => choice.sentence !== null && preferences.some((item) => same(item.text, choice.sentence!)))
    ?? group.choices.find((choice) => choice.sentence === null)!;
}

/** What picking `choice` changes: the group's other sentences to forget, and the sentence to add (if it is not there). */
export function change<P extends { id: number; text: string }>(group: Group, choice: Choice, preferences: readonly P[]): { forget: P[]; add: string | null } {
  const others = group.choices.filter((item) => item !== choice && item.sentence !== null).map((item) => item.sentence!);
  const forget = preferences.filter((item) => others.some((sentence) => same(item.text, sentence)));
  const present = choice.sentence !== null && preferences.some((item) => same(item.text, choice.sentence!));
  return { forget, add: choice.sentence !== null && !present ? choice.sentence : null };
}
