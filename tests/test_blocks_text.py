"""Executable parity with CPython's block text rules, including Unicode regexp case fixes."""
from __future__ import annotations

import _sre
import json
import random
import re
import subprocess
from re._casefix import _EXTRA_CASES

import pytest

from block_tables import TABLES, tables
from terminal_support import RIKA_SERVER
from kitty.core.blocks import actor, autotag, history, tokens


PIECES = [
    "", "a", "Z", "_", "-", ".", "+", "@", "/", "\\", ":", " ", "  ", "\n", "\t", "\r", "\x01", "\x07", "\x1c", "\x85",
    "\u00a0", "\u200b", "\u2028", "\u3000", "é", "ß", "İıſKK", "Σσς", "µΜμ", "ᾀ", "ﬃ", "日本", "😀", "𐐀𐐨", "\u0301", "\u0345", "\u2160", "\u0663", "²",
    "#tag", "a#b", "##x", "#_", "#-x", "#a/b-c", "#" + "x" * 101, "#" + "日" * 100, "#a#b", " #é/#Σ", "#a\u0301b",
    "[", "]", "[[", "]]", "|", "[[title]]", "[[a|]]", "[[a|b|c]]", "[[a\nb]]", "[[a\rb]]", "[[[inside]]]",
    "[[" + "x" * 201 + "]]", "[[" + "日" * 200 + "|" + "😀" * 200 + "]]", "[[x|" + "y" * 201 + "]]",
    "((01234567-89ab-cdef-0123-456789abcdef))", "((01234567-89AB-CDEF-0123-456789ABCDEF@123))",
    "((" + "-" * 36 + "@" + "9" * 18 + "))", "((" + "a" * 36 + "@" + "9" * 19 + "))",
    "((" + "g" * 36 + "))", "{{embed:01234567-89ab-cdef-0123-456789abcdef}}", "{{EMBED:" + "a" * 36 + "}}",
    "`", "``", "```", "````", "`code`", "`a\nb`", "```code\n```", "```unclosed\n", "``````", "```````",
    "http://a", "HTTPS://é", "fİle://x", "httpſ://x", "K://x", "ı://x", "1http://x", "_http://x", "ht+.-0://x",
    "www.a", "WWW.a", "xwww.a", "www.", "www.<a", "www.'a", "www.\u001ca", "http://", "://", "<", ">", "\"", "'",
    "a.b+c@é.é", "x@y.z.", "x@y..z", "x@y.z..", "..@--.-", "a@b.c@d.e", "a@@b.c", "@", "me@ex", "@ex.am", ".co",
]


def test_the_rust_block_tables_match_this_python():
    assert TABLES.read_text(encoding="utf-8") == tables(), \
        "server/src/blocks/tables.rs is stale: run `uv run python tests/block_tables.py`"


def _cases():
    rng = random.Random(292813)
    cases = []
    for _ in range(6000):
        text = "".join(rng.choice(PIECES) for _ in range(rng.randint(0, 35)))
        start = rng.randint(0, len(text))
        title = text[start:rng.randint(start, len(text))] if rng.randrange(2) else rng.choice(PIECES)
        title = rng.choice([str, str.lower, str.upper, str.swapcase])(title).strip()
        other = text[:start] + rng.choice(PIECES) + text[start:] if rng.randrange(2) else "".join(rng.choices(PIECES, k=10))
        n = len(text.encode("utf-16-le")) // 2
        marks = [
            {"t": "c", "s": rng.randint(-10, n + 10), "e": rng.randint(-10, n + 10)},
            {"t": "c", "s": 0, "e": n + 100}, {"t": "c", "s": n + 1, "e": n + 2},
            {"t": "b", "s": 0, "e": n}, {"t": "c", "s": True, "e": 2},
            {"t": "c", "s": 1.0, "e": 2}, {"t": "c", "s": "1", "e": 2},
            {"t": "c", "s": 0, "e": 18446744073709551615},
            {"t": "c", "s": 9223372036854775808, "e": 18446744073709551615},
            {"t": "c", "s": -9223372036854775808, "e": -1},
            {"t": "c"}, {}, [], None, "c",
        ]
        cases.append({"text": text, "title": title, "other": other,
                      "marks": marks if rng.randrange(4) else rng.choice([None, {}, "c", 7])})
    for piece in PIECES:
        cases.append({"text": piece, "title": piece.strip(), "other": piece[::-1], "marks": []})
    # Every changed simple lower mapping, and every exceptional literal equivalence.
    # In particular this rejects using full casefold or Rust's newer Unicode tables.
    for code in range(0x110000):
        if 0xD800 <= code <= 0xDFFF:
            continue
        low = _sre.unicode_tolower(code)
        if low != code:
            cases.append({"text": chr(low), "title": chr(code), "other": chr(code), "marks": []})
    for code, others in _EXTRA_CASES.items():
        for other in others:
            cases.append({"text": chr(other), "title": chr(code), "other": "", "marks": []})
    # Adjacent block merging, ambiguous longest-match ties and autojunk=False (>200 tokens).
    for before, after in [("a b", "b a"), ("tide", "diet"), ("a " * 300, "b " + "a " * 300),
                          ("a b a c " * 80, "a c a b " * 80), ("", ""), ("😀😀", "😀")]:
        cases.append({"text": before, "title": "", "other": after, "marks": [{"t": "c", "s": 1, "e": 3}]})
    return cases


def test_the_text_rules_match_python():
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER}); run cargo build --release in server/")
    cases = _cases()
    done = subprocess.run([str(RIKA_SERVER), "blocks-text"],
                          input="\n".join(json.dumps(case) for case in cases) + "\n",
                          capture_output=True, text=True, check=True, timeout=180)
    answers = [json.loads(line) for line in done.stdout.split("\n") if line]
    assert len(answers) == len(cases)
    for case, answer in zip(cases, answers, strict=True):
        text = case["text"]
        marks = autotag.code_marks(text, case["marks"])
        wanted = {
            "refs": [[*m.span(), m[1] or m[2]] for m in tokens.REF.finditer(text)],
            "wikis": [[*m.span(), m[1]] for m in tokens.WIKI.finditer(text)],
            "tags": [[*m.span(), m[1]] for m in tokens.TAG.finditer(text)],
            "without_wikis": tokens.WIKI.sub("", text),
            "skip": [list(m.span()) for m in autotag._SKIP.finditer(text)],
            "skip_spans": [list(span) for span in autotag.skip_spans(text, marks)],
            "utf16": autotag.utf16(text, range(len(text) + 1)),
            "code_points": autotag.code_points(text, range(len(text.encode("utf-16-le")) // 2 + 1)),
            "code_marks": [list(span) for span in marks],
            "normalize": autotag.normalize_term(text), "words": history._WORDS.findall(text),
            "word_diff": history.word_diff(text, case["other"]),
            "mentions": re.search(r"(?<!\w)" + re.escape(case["title"]) + r"(?!\w)", text, re.I) is not None,
            "clean_name": actor.clean_name(text),
        }
        assert answer.keys() == wanted.keys()
        for field in wanted:
            assert answer[field] == wanted[field], (field, case, answer[field], wanted[field])
    print(f"{len(cases)} block text cases matched CPython")
