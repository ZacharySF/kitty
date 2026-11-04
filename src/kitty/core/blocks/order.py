"""Fractional order keys: a Python port of rocicorp/fractional-indexing (CC0).

A key is a base-62 string; sorting keys as plain strings (SQLite's BINARY collation)
gives sibling order. A new key always fits between two neighbours, so inserting or
moving one block rewrites only that block, never its siblings.
"""
from __future__ import annotations

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ZERO = "a0"
_SMALLEST = "A" + "0" * 26


def _midpoint(a: str, b: str | None) -> str:
    zero = DIGITS[0]
    if b is not None and a >= b:
        raise ValueError(f"{a!r} must sort before {b!r}")
    if a[-1:] == zero or (b and b[-1:] == zero):
        raise ValueError("Order key fraction has a trailing zero")
    if b:
        n = 0
        while (a[n] if n < len(a) else zero) == b[n]:
            n += 1
        if n > 0:
            return b[:n] + _midpoint(a[n:], b[n:])
    low = DIGITS.index(a[0]) if a else 0
    high = DIGITS.index(b[0]) if b is not None else len(DIGITS)
    if high - low > 1:
        return DIGITS[(low + high + 1) // 2]
    if b and len(b) > 1:
        return b[:1]
    return DIGITS[low] + _midpoint(a[1:], None)


def _integer_length(head: str) -> int:
    if "a" <= head <= "z":
        return ord(head) - ord("a") + 2
    if "A" <= head <= "Z":
        return ord("Z") - ord(head) + 2
    raise ValueError(f"Invalid order key head {head!r}")


def _integer_part(key: str) -> str:
    length = _integer_length(key[0])
    if length > len(key):
        raise ValueError(f"Invalid order key {key!r}")
    return key[:length]


def validate(key: str) -> str:
    """Return `key` if it is a well-formed order key, else raise ValueError."""
    if not isinstance(key, str) or not key or key == _SMALLEST or any(c not in DIGITS for c in key):
        raise ValueError(f"Invalid order key {key!r}")
    fraction = key[len(_integer_part(key)):]
    if fraction[-1:] == DIGITS[0]:
        raise ValueError(f"Invalid order key {key!r}")
    return key


def _increment(value: str) -> str | None:
    head, digits = value[0], list(value[1:])
    for index in reversed(range(len(digits))):
        digit = DIGITS.index(digits[index]) + 1
        if digit == len(DIGITS):
            digits[index] = DIGITS[0]
        else:
            digits[index] = DIGITS[digit]
            return head + "".join(digits)
    if head == "Z":
        return _ZERO
    if head == "z":
        return None
    head = chr(ord(head) + 1)
    if head > "a":
        digits.append(DIGITS[0])
    else:
        digits.pop()
    return head + "".join(digits)


def _decrement(value: str) -> str | None:
    head, digits = value[0], list(value[1:])
    for index in reversed(range(len(digits))):
        digit = DIGITS.index(digits[index]) - 1
        if digit == -1:
            digits[index] = DIGITS[-1]
        else:
            digits[index] = DIGITS[digit]
            return head + "".join(digits)
    if head == "a":
        return "Z" + DIGITS[-1]
    if head == "A":
        return None
    head = chr(ord(head) - 1)
    if head < "Z":
        digits.append(DIGITS[-1])
    else:
        digits.pop()
    return head + "".join(digits)


def key_between(a: str | None, b: str | None) -> str:
    """A key sorting strictly after `a` and before `b` (None means open-ended)."""
    if a is not None:
        validate(a)
    if b is not None:
        validate(b)
    if a is not None and b is not None and a >= b:
        raise ValueError(f"{a!r} must sort before {b!r}")
    if a is None:
        if b is None:
            return _ZERO
        integer = _integer_part(b)
        fraction = b[len(integer):]
        if integer == _SMALLEST:
            return integer + _midpoint("", fraction)
        if integer < b:
            return integer
        result = _decrement(integer)
        if result is None:
            raise ValueError("Cannot place a key before the smallest key")
        return result
    if b is None:
        integer = _integer_part(a)
        result = _increment(integer)
        return integer + _midpoint(a[len(integer):], None) if result is None else result
    integer_a, integer_b = _integer_part(a), _integer_part(b)
    if integer_a == integer_b:
        return integer_a + _midpoint(a[len(integer_a):], b[len(integer_b):])
    result = _increment(integer_a)
    if result is None:
        raise ValueError("Cannot place a key after the largest key")
    return result if result < b else integer_a + _midpoint(a[len(integer_a):], None)


def keys_between(a: str | None, b: str | None, count: int) -> list[str]:
    """`count` ascending keys between `a` and `b`, kept short by bisecting."""
    if count <= 0:
        return []
    if count == 1:
        return [key_between(a, b)]
    if b is None:
        result = [key_between(a, None)]
        while len(result) < count:
            result.append(key_between(result[-1], None))
        return result
    if a is None:
        result = [key_between(None, b)]
        while len(result) < count:
            result.append(key_between(None, result[-1]))
        return result[::-1]
    middle = count // 2
    pivot = key_between(a, b)
    return [*keys_between(a, pivot, middle), pivot, *keys_between(pivot, b, count - middle - 1)]
