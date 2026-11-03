/**
 * Reading a typed number answer on the device (design Q4 A). Integers, decimals,
 * fractions and percentages are read as you type so the box can echo the value
 * ("3/7 ≈ 0.4286"); the backend still decides correctness.
 */
export type Reading =
  | { kind: "integer"; text: string; value: number; sig: number }
  | { kind: "decimal"; text: string; value: number; sig: number }
  | { kind: "percent"; text: string; value: number; sig: number }
  | { kind: "fraction"; text: string; value: number; numerator: number; denominator: number };

/** Significant figures of an unsigned decimal literal ("0.0430" → 3). */
function significant(digits: string): number {
  const bare = digits.replace(".", "").replace(/^0+/, "");
  return bare.length || 1;
}

export function readAnswer(raw: string): Reading | null {
  const text = raw.trim().replace(/\s+/g, "").replace(/[−–]/g, "-");
  let match: RegExpExecArray | null;
  if ((match = /^(-?\d+)\/(-?\d+)$/.exec(text))) {
    const [numerator, denominator] = [Number(match[1]), Number(match[2])];
    return denominator === 0 ? null : { kind: "fraction", text, value: numerator / denominator, numerator, denominator };
  }
  const number = text.replace(",", ".");
  if (/^-?\d+$/.test(number)) return { kind: "integer", text, value: Number(number), sig: significant(number.replace("-", "")) };
  if (/^-?\d*\.\d+$/.test(number)) return { kind: "decimal", text, value: Number(number), sig: significant(number.replace("-", "")) };
  if ((match = /^(-?\d*\.?\d+)%$/.exec(number))) {
    return { kind: "percent", text, value: Number((Number(match[1]) / 100).toPrecision(12)), sig: significant(match[1].replace("-", "")) };
  }
  return null;
}

/** Four significant figures, without trailing zeros: 0.428571… → "0.4286". */
const short = (value: number) => String(Number(value.toPrecision(4)));
const minus = (text: string) => text.replace(/-/g, "−");

/** The live echo beside the box. */
export function echo(reading: Reading | null): string {
  if (!reading) return "";
  const text = minus(reading.text);
  if (reading.kind === "fraction") {
    const exact = Number(reading.value.toPrecision(12));
    return `${text} ${short(exact) === String(exact) ? "=" : "≈"} ${minus(short(exact))}`;
  }
  if (reading.kind === "percent") return `${text} = ${minus(String(reading.value))}`;
  if (reading.kind === "decimal") return `${text} · ${reading.sig} s.f.`;
  return text;
}
