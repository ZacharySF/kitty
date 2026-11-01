// Fractional order keys, a port of src/kitty/core/blocks/order.py (itself a port of
// rocicorp/fractional-indexing, CC0). Keys are base-62 strings that sort as plain
// strings; a new key always fits between two neighbours, so placing a block never
// rewrites its siblings. The editor makes the same keys the server would.
const DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
const ZERO = "a0";
const SMALLEST = "A" + "0".repeat(26);

function midpoint(a: string, b: string | null): string {
  const zero = DIGITS[0];
  if (b !== null && a >= b) throw new Error(`${a} must sort before ${b}`);
  if (a.slice(-1) === zero || (b && b.slice(-1) === zero)) throw new Error("Order key fraction has a trailing zero");
  if (b) {
    let n = 0;
    while ((n < a.length ? a[n] : zero) === b[n]) n += 1;
    if (n > 0) return b.slice(0, n) + midpoint(a.slice(n), b.slice(n));
  }
  const low = a ? DIGITS.indexOf(a[0]) : 0;
  const high = b !== null ? DIGITS.indexOf(b[0]) : DIGITS.length;
  if (high - low > 1) return DIGITS[Math.floor((low + high + 1) / 2)];
  if (b && b.length > 1) return b.slice(0, 1);
  return DIGITS[low] + midpoint(a.slice(1), null);
}

function integerLength(head: string): number {
  if (head >= "a" && head <= "z") return head.charCodeAt(0) - 97 + 2;
  if (head >= "A" && head <= "Z") return 90 - head.charCodeAt(0) + 2;
  throw new Error(`Invalid order key head ${head}`);
}

function integerPart(key: string): string {
  const length = integerLength(key[0]);
  if (length > key.length) throw new Error(`Invalid order key ${key}`);
  return key.slice(0, length);
}

export function validKey(key: unknown): key is string {
  if (typeof key !== "string" || !key || key === SMALLEST || [...key].some((c) => !DIGITS.includes(c))) return false;
  try {
    return key.slice(integerPart(key).length).slice(-1) !== DIGITS[0];
  } catch {
    return false;
  }
}

function check(key: string | null): void {
  if (key !== null && !validKey(key)) throw new Error(`Invalid order key ${key}`);
}

function increment(value: string): string | null {
  const head = value[0];
  const digits = value.slice(1).split("");
  for (let index = digits.length - 1; index >= 0; index -= 1) {
    const digit = DIGITS.indexOf(digits[index]) + 1;
    if (digit === DIGITS.length) digits[index] = DIGITS[0];
    else {
      digits[index] = DIGITS[digit];
      return head + digits.join("");
    }
  }
  if (head === "Z") return ZERO;
  if (head === "z") return null;
  const next = String.fromCharCode(head.charCodeAt(0) + 1);
  if (next > "a") digits.push(DIGITS[0]);
  else digits.pop();
  return next + digits.join("");
}

function decrement(value: string): string | null {
  const head = value[0];
  const digits = value.slice(1).split("");
  for (let index = digits.length - 1; index >= 0; index -= 1) {
    const digit = DIGITS.indexOf(digits[index]) - 1;
    if (digit === -1) digits[index] = DIGITS[DIGITS.length - 1];
    else {
      digits[index] = DIGITS[digit];
      return head + digits.join("");
    }
  }
  if (head === "a") return "Z" + DIGITS[DIGITS.length - 1];
  if (head === "A") return null;
  const next = String.fromCharCode(head.charCodeAt(0) - 1);
  if (next < "Z") digits.push(DIGITS[DIGITS.length - 1]);
  else digits.pop();
  return next + digits.join("");
}

/** A key sorting strictly after `a` and before `b` (null means open-ended). */
export function keyBetween(a: string | null, b: string | null): string {
  check(a);
  check(b);
  if (a !== null && b !== null && a >= b) throw new Error(`${a} must sort before ${b}`);
  if (a === null) {
    if (b === null) return ZERO;
    const integer = integerPart(b);
    const fraction = b.slice(integer.length);
    if (integer === SMALLEST) return integer + midpoint("", fraction);
    if (integer < b) return integer;
    const result = decrement(integer);
    if (result === null) throw new Error("Cannot place a key before the smallest key");
    return result;
  }
  if (b === null) {
    const integer = integerPart(a);
    const result = increment(integer);
    return result === null ? integer + midpoint(a.slice(integer.length), null) : result;
  }
  const integerA = integerPart(a);
  const integerB = integerPart(b);
  if (integerA === integerB) return integerA + midpoint(a.slice(integerA.length), b.slice(integerB.length));
  const result = increment(integerA);
  if (result === null) throw new Error("Cannot place a key after the largest key");
  return result < b ? result : integerA + midpoint(a.slice(integerA.length), null);
}

/** `count` ascending keys between `a` and `b`, kept short by bisecting. */
export function keysBetween(a: string | null, b: string | null, count: number): string[] {
  if (count <= 0) return [];
  if (count === 1) return [keyBetween(a, b)];
  if (b === null) {
    const result = [keyBetween(a, null)];
    while (result.length < count) result.push(keyBetween(result[result.length - 1], null));
    return result;
  }
  if (a === null) {
    const result = [keyBetween(null, b)];
    while (result.length < count) result.push(keyBetween(null, result[result.length - 1]));
    return result.reverse();
  }
  const middle = Math.floor(count / 2);
  const pivot = keyBetween(a, b);
  return [...keysBetween(a, pivot, middle), pivot, ...keysBetween(pivot, b, count - middle - 1)];
}
