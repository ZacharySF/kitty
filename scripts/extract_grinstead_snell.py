"""Extract the exercises of Grinstead & Snell's *Introduction to Probability* into
``third_party/grinstead-snell/exercises.json``.

The source is Peter G. Doyle's GFDL edition (``prob.tar.gz``, pinned by SHA-256). Every
exercise of every section is converted from LaTeX to Markdown with ``$…$``/``$$…$$`` TeX
that KaTeX renders: ``\\ref``s become the printed numbers (computed here the way LaTeX
numbers them, and checked against the official build's ``.aux`` files with ``--aux``),
``\\index``/``\\label`` are dropped, ``enumerate`` parts become (a), (b)…, plain-TeX
matrices become KaTeX environments and tables become KaTeX arrays. Any construct the
converter doesn't know raises :class:`Unsupported` naming the exercise, so nothing is
guessed. Exercises built around a figure that isn't bundled are skipped with a reason
(``SKIP``); ``FIXES`` lists the few exact source rewrites, each with its reason.

    uv run --locked python scripts/extract_grinstead_snell.py [--tarball prob.tar.gz] [--aux DIR]

This script is MIT-licensed rika! code; its output is GFDL (see the directory's SOURCE.txt).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tarfile
import unicodedata
import urllib.request
from collections import defaultdict
from pathlib import Path

URL = "https://math.dartmouth.edu/~doyle/docs/prob/prob.tar.gz"
SHA256 = "333226cc2c2a38bb41490568e865b5769436c5f70005710280a1301039ee8577"
VERSION = "Version dated 22 July 2006"
OUTPUT = Path(__file__).resolve().parents[1] / "third_party/grinstead-snell/exercises.json"
CHAPTERS = range(1, 13)

#: Exercises that can't stand without an unbundled figure, by printed label, with the reason.
SKIP = {
    "4.1.32": "the three road networks between Woodstock and Tunbridge are only drawn in Figure 4.6",
    "11.5.7": "the maze whose transition matrix it asks for is only drawn in Figure 11.7",
    "11.5.21": "the Monopoly board's squares and payoffs are only given in Figure 11.8",
}

#: Exact source rewrites ``(chapter, old, new, reason)``; each ``old`` must occur exactly once.
FIXES: list[tuple[int, str, str, str]] = [
    (11,
     ("\\pmatrix{$$\\begin{tabular}{l|r} \\mat{0} & \\mat{B} \\\\ \\hline \n"
      "                                \\mat{0} & \\mat{I} \\\\ \\end{tabular}$$ \\cr}"),
     "\\left(\\begin{array}{l|r} \\mat{0} & \\mat{B} \\\\ \\hline \\mat{0} & \\mat{I} \\end{array}\\right)",
     ("exercise 11.2.27 draws a block matrix as a tabular inside display math inside \\pmatrix; "
      "rewritten as the same ruled block matrix in a math array")),
]

THEOREMS = {"example": "examp", "definition": "defn", "theorem": "thm", "corollary": "cor",
            "lemma": "lem", "proposition": "prop"}
ACCENTS = {"'": "\u0301", "`": "\u0300", '"': "\u0308", "^": "\u0302", "~": "\u0303", "c": "\u0327",
           "v": "\u030c", "u": "\u0306", "=": "\u0304", ".": "\u0307", "H": "\u030b"}
SYMBOLS = {"ldots": "…", "dots": "…", "copyright": "©", "S": "§", "ss": "ß", "ae": "æ", "o": "ø",
           "NA": "NA", "TeX": "TeX", "LaTeX": "LaTeX", "%": "%", "&": "&", "#": "#", "{": "{", "}": "}",
           " ": " ", "\n": " ", ",": "\u2009", ";": " ", "quad": " ", "qquad": " ", "@": "", "/": "", "-": ""}
#: Text-mode commands that only affect layout.
DROP = {"noindent", "indent", "medskip", "bigskip", "smallskip", "medbreak", "smallbreak", "bigbreak",
        "protect", "hfill", "centering", "sloppy", "nolinebreak", "relax", "unskip", "hfil"}
#: Font switches for the rest of the group, ``{\bf …}``: "b"/"i" add bold/italic, "" is upright
#: and None a size change that Markdown doesn't show.
SWITCHES = {"bf": "b", "em": "i", "it": "i", "sl": "i", "rm": "", "sf": "", "tt": "", "sc": "",
            "footnotesize": None, "small": None, "normalsize": None, "large": None, "Large": None}
#: Commands whose argument is text in a style (None keeps the surrounding one).
STYLE_COMMANDS = {"emx": "i", "emph": "i", "textit": "i", "textbf": "b", "textrm": "", "mbox": None, "hbox": None}


class Unsupported(ValueError):
    """A construct the converter doesn't handle (it never guesses)."""


# ---------------------------------------------------------------------------- reading


def source(tarball: Path | None) -> bytes:
    if tarball is None:
        with urllib.request.urlopen(URL, timeout=60) as response:
            data = response.read()
    else:
        data = tarball.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        raise SystemExit(f"prob.tar.gz has SHA-256 {digest}, expected {SHA256}")
    return data


def chapters(data: bytes) -> dict[int, str]:
    """Each chapter's LaTeX with comments removed, ``\\choice`` resolved and FIXES applied."""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for number in CHAPTERS:
            raw = archive.extractfile(f"ch{number}.tex").read().decode("ascii")
            text = choose(strip_comments(raw.replace("\r\n", "\n").replace("\r", "\n")))
            for chapter, old, new, _ in FIXES:
                if chapter == number:
                    if text.count(old) != 1:
                        raise Unsupported(f"fix for chapter {chapter} matches {text.count(old)} times: {old[:60]!r}")
                    text = text.replace(old, new)
            # ch1 and ch10 set the chapter counter to the number before theirs, and ch1 the page.
            for match in re.finditer(r"\\(?:set|addto)counter(?:\{\w+\}\{\d+\})?", text):
                if match.group(0) != f"\\setcounter{{chapter}}{{{number - 1}}}" and \
                        not re.fullmatch(r"\\setcounter\{page\}\{\d+\}", match.group(0)):
                    raise Unsupported(f"chapter {number} has {match.group(0)}")
            out[number] = text
    return out


def strip_comments(text: str) -> str:
    """Drop ``%`` comments with their newline and the next line's indent, as TeX does."""
    out, at = [], 0
    while at < len(text):
        char = text[at]
        if char == "\\":
            out.append(text[at:at + 2])
            at += 2
        elif char == "%":
            end = text.find("\n", at)
            at = len(text) if end < 0 else end + 1
            while at < len(text) and text[at] in " \t":
                at += 1
        else:
            out.append(char)
            at += 1
    return "".join(out)


def choose(text: str) -> str:
    """``\\choice{discrete}{continuous}`` → the continuous edition's text (prob.tex says ``\\discretefalse``)."""
    while (at := find_command(text, "choice")) >= 0:
        _, end = read_arg(text, at + len("\\choice"))
        second, end = read_arg(text, end)
        text = text[:at] + second + text[end:]
    return text


# ---------------------------------------------------------------------------- TeX scanning


def find_command(text: str, name: str, start: int = 0) -> int:
    for match in re.finditer(r"\\" + name + r"(?![A-Za-z])", text[start:]):
        at = start + match.start()
        if backslashes_before(text, at) % 2 == 0:
            return at
    return -1


def backslashes_before(text: str, at: int) -> int:
    count = 0
    while at - count - 1 >= 0 and text[at - count - 1] == "\\":
        count += 1
    return count


def control(text: str, at: int) -> tuple[str, int]:
    """The control sequence at ``text[at] == '\\'`` and the index after it (and its spaces)."""
    match = re.match(r"\\([A-Za-z]+)", text[at:])
    if match:
        end = at + match.end()
        return match.group(1), skip_space(text, end)
    if at + 1 >= len(text):
        raise Unsupported("lone backslash")
    return text[at + 1], at + 2


def skip_space(text: str, at: int, newlines: int = 1) -> int:
    """Skip spaces and at most ``newlines`` newlines (a blank line stays a paragraph)."""
    while at < len(text) and text[at] in " \t\n":
        if text[at] == "\n":
            if newlines == 0:
                break
            newlines -= 1
        at += 1
    return at


def read_group(text: str, at: int) -> tuple[str, int]:
    """The contents of the brace group opening at ``text[at]`` and the index after it."""
    if text[at] != "{":
        raise Unsupported(f"expected a group at {text[at:at + 40]!r}")
    depth, pos = 0, at
    while pos < len(text):
        char = text[pos]
        if char == "\\":
            pos += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[at + 1:pos], pos + 1
        pos += 1
    raise Unsupported(f"unbalanced group at {text[at:at + 40]!r}")


def read_arg(text: str, at: int) -> tuple[str, int]:
    at = skip_space(text, at)
    if at >= len(text):
        raise Unsupported("missing argument")
    if text[at] == "{":
        return read_group(text, at)
    if text[at] == "\\":
        _, end = control(text, at)
        return text[at:end].rstrip(), end
    return text[at], at + 1


def read_optional(text: str, at: int) -> tuple[str | None, int]:
    pos = skip_space(text, at)
    if pos < len(text) and text[pos] == "[":
        depth = 0
        for end in range(pos, len(text)):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
            elif text[end] == "]" and depth == 0:
                return text[pos + 1:end], end + 1
        raise Unsupported("unclosed optional argument")
    return None, at


def environment(text: str, at: int, name: str) -> tuple[str, int]:
    """The body of ``\\begin{name}`` whose argument ends at ``at``, and the index after its ``\\end``."""
    depth, pos = 1, at
    pattern = re.compile(r"\\(begin|end)\s*\{" + re.escape(name) + r"\}")
    while match := pattern.search(text, pos):
        depth += 1 if match.group(1) == "begin" else -1
        if depth == 0:
            return text[at:match.start()], match.end()
        pos = match.end()
    raise Unsupported(f"unclosed environment {name}")


def clean_label(label: str) -> str:
    return " ".join(label.split())


# ---------------------------------------------------------------------------- numbering


def number_labels(chapter: int, text: str) -> dict[str, str]:
    """Every ``\\label`` → the text ``\\ref`` prints, following LaTeX's book class: theorem-like
    environments, equations, figures and tables are numbered chapter.n; enumerate items 1, 1a."""
    labels: dict[str, str] = {}
    counters: dict[str, int] = defaultdict(int)
    current = [str(chapter)]  # \@currentlabel, one entry per TeX group
    envs: list[str] = []
    enums: list[int] = []
    at = 0
    while at < len(text):
        char = text[at]
        if char == "{":
            current.append(current[-1])
            at += 1
            continue
        if char == "}":
            current.pop()
            at += 1
            continue
        if char != "\\":
            at += 1
            continue
        name, end = control(text, at)
        if name == "begin":
            env, end = read_arg(text, end)
            envs.append(env)
            current.append(current[-1])
            if env in THEOREMS:
                counters[THEOREMS[env]] += 1
                current[-1] = f"{chapter}.{counters[THEOREMS[env]]}"
            elif env == "equation":
                counters["equation"] += 1
                current[-1] = f"{chapter}.{counters['equation']}"
            elif env in ("enumerate", "LJSItem"):
                enums.append(0)
            elif env in ("eqnarray", "eqnarray*"):
                # Each row steps the equation counter and steps it back if unnumbered,
                # so a label in an unnumbered row prints the next equation's number.
                body, _ = environment(text, end, env)
                for row in re.split(r"\\\\", body):
                    number = counters["equation"] + 1
                    if env == "eqnarray" and "\\nonumber" not in row:
                        counters["equation"] = number
                    for label in re.findall(r"\\label\{([^}]*)\}", row):
                        labels[clean_label(label)] = f"{chapter}.{number}"
                end = end + len(body)
            elif env in ("itemize", "description") and "\\label" in environment(text, end, env)[0]:
                raise Unsupported(f"label inside {env} in chapter {chapter}")
        elif name == "end":
            env, end = read_arg(text, end)
            if not envs or envs.pop() != env:
                raise Unsupported(f"mismatched \\end{{{env}}} in chapter {chapter}")
            current.pop()
            if env in ("enumerate", "LJSItem"):
                enums.pop()
        elif name == "section":
            _, end = read_optional(text, end)
            if text[end:end + 1] != "*":
                counters["section"] += 1
                current[-1] = f"{chapter}.{counters['section']}"
        elif name in ("item", "i", "istar") and enums and envs[-1] in ("enumerate", "LJSItem"):
            option, end = read_optional(text, end)
            if option is None:
                enums[-1] += 1
                current[-1] = enum_label(enums)
        elif name in ("putfig", "putfigdouble", "nocaption"):
            args = []
            for _ in range(6 if name == "putfigdouble" else 4):
                arg, end = read_arg(text, end)
                args.append(arg)
            if name == "nocaption":
                labels[clean_label(args[-1])] = current[-1]
            else:
                counters["figure"] += 1
                labels[clean_label(args[-1])] = f"{chapter}.{counters['figure']}"
        elif name == "caption":
            kind = next((env for env in reversed(envs) if env in ("figure", "table")), None)
            if kind is None:
                raise Unsupported(f"caption outside a float in chapter {chapter}")
            counters[kind] += 1
            current[-1] = f"{chapter}.{counters[kind]}"
            _, end = read_optional(text, end)
            _, end = read_arg(text, end)
        elif name == "label":
            label, end = read_arg(text, end)
            labels[clean_label(label)] = current[-1]
        elif name == "chapter":
            _, end = read_optional(text, end)
            _, end = read_arg(text, end)
        at = end
    return labels


def enum_label(enums: list[int]) -> str:
    """What ``\\ref`` prints for a (nested) enumerate item: 1, 1a, 1(a)i, 1(a)iA."""
    if len(enums) > 4:
        raise Unsupported("enumerate nested more than four deep")
    marks = [str(enums[0])] + [mark(depth, value) for depth, value in enumerate(enums[1:], 1)]
    if len(marks) > 2:
        marks[1] = f"({marks[1]})"
    return "".join(marks)


def mark(depth: int, value: int) -> str:
    """``\\theenum…`` below the first level: alph, roman, Alph."""
    return ("abcdefghijklmnopqrstuvwxyz"[value - 1], roman(value), "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[value - 1])[depth - 1]


#: Labels whose official build prints something else for a LaTeX grouping accident: ours, aux.
#: ch5.tex wraps ``\chapter`` in ``\choice{…}{…}`` braces, so ``\label{chp 5}`` after it picks
#: up the last section of chapter 4. No exercise refers to it.
AUX_QUIRKS = {"chp 5": ("5", "4.3")}


def check_aux(labels: dict[int, dict[str, str]], aux: Path) -> None:
    """Every label we number must print the same as in the official build's .aux files."""
    wrong = []
    for chapter, mine in labels.items():
        printed = {}
        for match in re.finditer(r"\\newlabel\{([^}]*)\}\{\{([^}]*)\}", (aux / f"ch{chapter}.aux").read_text()):
            printed[clean_label(match.group(1))] = match.group(2)
        for label, value in mine.items():
            if printed.get(label) != value and AUX_QUIRKS.get(label) != (value, printed.get(label)):
                wrong.append(f"ch{chapter} {label!r}: ours {value!r}, aux {printed.get(label)!r}")
        for label in printed.keys() - mine.keys():
            wrong.append(f"ch{chapter} {label!r} missing")
    if wrong:
        raise SystemExit("Numbering differs from the .aux files:\n" + "\n".join(wrong))


# ---------------------------------------------------------------------------- conversion


class Converter:
    """LaTeX → pieces → Markdown for one exercise. Pieces are ``("t", text, style)``,
    ``("m", tex)`` inline math, ``("d", tex)`` display math, ``("p",)`` paragraph break and
    ``("n",)`` line break."""

    def __init__(self, label: str, refs: dict[str, str]):
        self.label = label
        self.refs = refs
        self.figure = False
        self.footnotes: list[list] = []

    def fail(self, what: str) -> Unsupported:
        return Unsupported(f"exercise {self.label}: {what}")

    # -- text mode

    def text(self, src: str, style: str = "", depth: int = 0) -> list:
        pieces: list = []
        at = 0
        plain: list[str] = []

        def flush():
            if plain:
                pieces.append(("t", "".join(plain), style))
                plain.clear()

        while at < len(src):
            char = src[at]
            if char == "\\":
                name, end = control(src, at)
                if name in SWITCHES:
                    flush()
                    pieces += self.text(src[end:], merge(style, SWITCHES[name]), depth)
                    return pieces
                if name == "[":
                    body, end = read_until(src, end, "\\]")
                    flush()
                    pieces.append(("d", self.math(body)))
                    at = end
                    continue
                if name == "(":
                    body, end = read_until(src, end, "\\)")
                    flush()
                    pieces.append(("m", self.math(body)))
                    at = end
                    continue
                flush()
                produced, at = self.command(name, src, end, style, depth)
                pieces += produced
                continue
            if char == "$":
                flush()
                if src.startswith("$$", at):
                    body, at = read_math(src, at + 2, "$$")
                    pieces.append(("d", self.math(body)))
                else:
                    body, at = read_math(src, at + 1, "$")
                    pieces.append(("m", self.math(body)))
                continue
            if char == "{":
                body, at = read_group(src, at)
                flush()
                pieces += self.text(body, style, depth)
                continue
            if char == "}":
                raise self.fail("unbalanced }")
            if char == "\n":
                stop = skip_space(src, at, newlines=99)
                if src.count("\n", at, stop) >= 2:
                    flush()
                    pieces.append(("p",))
                else:
                    plain.append(" ")
                at = stop
                continue
            for tex, out in (("---", "—"), ("--", "–"), ("``", "“"), ("''", "”"), ('"', "”"), ("`", "‘"),
                             ("'", "’"), ("~", "\u00a0"), ("<", None), (">", None), ("|", None),
                             ("&", None), ("#", None), ("^", None), ("_", None), ("*", None)):
                if src.startswith(tex, at):
                    if out is None:
                        raise self.fail(f"text-mode {tex!r}")
                    plain.append(out)
                    at += len(tex)
                    break
            else:
                plain.append(char)
                at += 1
        flush()
        return pieces

    def command(self, name: str, src: str, at: int, style: str, depth: int) -> tuple[list, int]:
        if name in DROP:
            return [], at
        if name in ("index", "label"):
            _, at = read_arg(src, at)
            return [], at
        if name == "vspace":
            _, at = read_arg(src, at + 1 if src[at:at + 1] == "*" else at)
            return [], at
        if name in ("pagebreak", "nopagebreak"):
            _, at = read_optional(src, at)
            return [], at
        if name in ("vskip", "hskip"):
            match = re.match(r"\s*[-+]?[\d.]+\s*(in|pt|cm|mm|em|ex)", src[at:])
            if not match:
                raise self.fail(f"\\{name} without a dimension")
            return [], at + match.end()
        if name == "linebreak":
            _, at = read_optional(src, at)
            return [("t", " ", style)], at
        if name == "hspace":
            if src[at:at + 1] == "*":
                at += 1
            _, at = read_arg(src, at)
            return [("t", " ", style)], at
        if name in ("par",):
            return [("p",)], at
        if name in ("\\", "break", "newline"):
            if name == "\\":
                _, at = read_optional(src, at)
            return [("n",)], at
        if name in ("ref", "exref"):
            label, at = read_arg(src, at)
            return [("t", self.ref(label), style)], at
        if name in STYLE_COMMANDS:
            body, at = read_arg(src, at)
            return self.text(body, merge(style, STYLE_COMMANDS[name]), depth), at
        if name == "mat":
            body, at = read_arg(src, at)
            return [("m", f"\\mathbf{{{self.math(body)}}}")], at
        if name == "footnote":
            body, at = read_arg(src, at)
            self.footnotes.append(self.text(body, "", depth))
            return [("t", superscript(len(self.footnotes)), style)], at
        if name in ("putfig", "putfigdouble"):
            # A float placed here; only a reference to a figure makes the text depend on it.
            for _ in range(6 if name == "putfigdouble" else 4):
                _, at = read_arg(src, at)
            return [], at
        if name in ACCENTS:
            arg, at = read_arg(src, at)
            if len(arg) != 1 and arg not in ("\\i", "\\j"):
                raise self.fail(f"accent on {arg!r}")
            base = {"\\i": "i", "\\j": "j"}.get(arg, arg)
            return [("t", unicodedata.normalize("NFC", base + ACCENTS[name]), style)], at
        if name in SYMBOLS:
            return [("t", SYMBOLS[name], style)], at
        if name == "$":
            return [("t", "$", style)], at
        if name == "begin":
            env, at = read_arg(src, at)
            _, at = read_optional(src, at) if env in ("table", "figure") else (None, at)
            body, at = environment(src, at, env)
            return self.env(env, body, style, depth), at
        raise self.fail(f"unhandled \\{name}")

    def env(self, env: str, body: str, style: str, depth: int) -> list:
        if env in ("quote", "center", "sloppypar", "quotation"):
            return [("p",), *self.text(body, style, depth), ("p",)]
        if env in ("enumerate", "itemize"):
            return self.items(env, body, style, depth + 1)
        if env in ("eqnarray", "eqnarray*"):
            return [("d", self.eqnarray(body))]
        if env in ("equation", "equation*", "displaymath"):
            return [("d", self.math(body))]
        if env == "table":
            return self.table(body)
        if env == "tabular":
            raise self.fail("tabular outside a table")
        raise self.fail(f"unhandled environment {env}")

    def items(self, env: str, body: str, style: str, depth: int) -> list:
        # prob.tex defines \i and \istar as \item with a bold label, so they can number parts too.
        parts = split_items(body, ("item", "i", "istar"))
        if parts[0].strip():
            raise self.fail(f"text before the first \\item in {env}")
        pieces: list = []
        for index, (_, option, part) in enumerate(parts[1:]):
            if option is not None:
                label = "".join(piece[1] for piece in self.text(option) if piece[0] == "t")
            elif env == "itemize":
                label = "•"
            else:
                label = item_mark(depth, index)
            pieces += [("p",) if depth == 1 else ("n",), ("t", label + " ", "")]
            pieces += self.text(part, style, depth)
        return [*pieces, ("p",)]

    def table(self, body: str) -> list:
        """A table float: "**Table 4.5.** caption", then its tabular as a KaTeX array."""
        found = {"caption": "", "label": None}
        for name in ("caption", "label", "centering"):
            while (at := find_command(body, name)) >= 0:
                end = at + len(name) + 1
                if name != "centering":
                    found[name], end = read_arg(body, end)
                body = body[:at] + body[end:]
        if found["label"] is None:
            raise self.fail("table without a label")
        pieces: list = [("p",), ("t", f"Table {self.ref(found['label'])}.", "b"), ("t", " ", ""),
                        *self.text(found["caption"]), ("n",)]
        rest = body.strip()
        if rest.startswith("\\begin{tabular}"):
            spec, at = read_arg(rest, len("\\begin{tabular}"))
            inner, at = environment(rest, at, "tabular")
            if rest[at:].strip():
                raise self.fail("text after a tabular")
            pieces.append(("d", self.tabular(spec, inner)))
        elif rest.startswith("$$") and rest.endswith("$$"):
            pieces.append(("d", self.math(rest[2:-2])))
        else:
            raise self.fail("table without a tabular")
        return [*pieces, ("p",)]

    def tabular(self, spec: str, body: str) -> str:
        """A simple tabular as a KaTeX array: text cells in ``\\text{}``, rules kept. KaTeX has
        no ``\\multicolumn`` or ``\\cline``: a spanning cell becomes its text followed by empty
        cells, and a partial rule a full one."""
        if re.search(r"[^lcr|]", spec.replace(" ", "")):
            raise self.fail(f"tabular column spec {spec!r}")
        rows = []
        for row in re.split(r"\\\\|\\cr(?![A-Za-z])", body):
            rules, row = [], row.strip()
            while match := re.match(r"\\hline|\\cline\{\d+-\d+\}", row):
                rules.append("\\hline")
                row = row[match.end():].strip()
            cells = []
            for cell in split_cells(row) if row else []:
                cell = cell.strip()
                if cell.startswith("\\multicolumn"):
                    span, end = read_arg(cell, len("\\multicolumn"))
                    _, end = read_arg(cell, end)
                    inner, end = read_arg(cell, end)
                    if cell[end:].strip():
                        raise self.fail("text after \\multicolumn")
                    cells += [self.math_text(inner)] + [""] * (int(span) - 1)
                elif re.fullmatch(r"[-+]?[\d.]+(?:/\d+)?", cell):
                    cells.append(cell)  # a number reads better as math than as \text
                else:
                    cells.append(self.math_text(cell))
            if rules or cells:
                rows.append(" ".join(rules + [" & ".join(cells)]).strip())
        return f"\\begin{{array}}{{{spec.replace(' ', '')}}} " + " \\\\ ".join(rows) + " \\end{array}"

    def ref(self, label: str) -> str:
        label = clean_label(label)
        if label not in self.refs:
            raise self.fail(f"unknown \\ref{{{label}}}")
        if label.startswith("fig"):
            self.figure = True
        return self.refs[label]

    # -- math mode

    def math(self, src: str) -> str:
        out: list[str] = []
        at = 0
        while at < len(src):
            char = src[at]
            if char == "\\":
                name, end = control(src, at)
                if name in ("label", "index"):
                    _, at = read_arg(src, end)
                    continue
                if name in ("nonumber", "protect"):
                    at = end
                    continue
                if name == "ref":
                    label, at = read_arg(src, end)
                    out.append(self.ref(label))
                    continue
                if name == "mat":
                    body, at = read_arg(src, end)
                    out.append(f"\\mathbf{{{self.math(body)}}}")
                    continue
                if name == "NA":
                    out.append("\\text{NA}")
                    at = end
                    continue
                if name in ("mbox", "hbox", "text", "emx", "textbf", "textit"):
                    body, at = read_arg(src, end)
                    style = {"emx": "i", "textit": "i", "textbf": "b"}.get(name, "")
                    out.append(self.math_text(body, style))
                    continue
                if name in ("pmatrix", "matrix"):
                    body, at = read_arg(src, end)
                    out.append(f"\\begin{{{name}}} {self.rows(body)} \\end{{{name}}}")
                    continue
                if name == "bordermatrix":
                    body, at = read_arg(src, end)
                    out.append(self.bordermatrix(body))
                    continue
                if name == "begin":
                    env, end = read_arg(src, end)
                    if env not in ("array", "matrix", "pmatrix", "bmatrix", "cases"):
                        raise self.fail(f"math environment {env}")
                    spec = ""
                    if env == "array":
                        spec, end = read_arg(src, end)
                        spec = "{" + spec + "}"
                    body, at = environment(src, end, env)
                    out.append(f"\\begin{{{env}}}{spec} {self.math(body)} \\end{{{env}}}")
                    continue
                if name in ("cr", "eqno", "leqno", "break", "par", "noalign", "halign", "atopwithdelims"):
                    raise self.fail(f"\\{name} in math")
                out.append(re.sub(r"\s+", " ", src[at:end]))
                at = end
                continue
            if char == "$":
                raise self.fail("nested $ in math")
            if char in "\n\t":
                out.append(" ")
                at += 1
                continue
            if char == "{":
                body, at = read_group(src, at)
                out.append("{" + self.math(body) + "}")
                continue
            out.append(char)
            at += 1
        return re.sub(r" {2,}", " ", "".join(out)).strip()

    def math_text(self, src: str, style: str = "") -> str:
        """Text inside math (``\\mbox``, table cells) as KaTeX ``\\text{}`` runs; nested math stays math."""
        out = []
        for piece in self.text(src, style):
            if piece[0] == "t":
                if not piece[1]:
                    continue
                text = piece[1].replace("\\", "\\textbackslash ").replace("{", "\\{").replace("}", "\\}") \
                    .replace("$", "\\$").replace("%", "\\%").replace("&", "\\&").replace("#", "\\#")
                if text.strip() == "":
                    out.append("\\ " if text else "")
                    continue
                wrap = {"b": "\\textbf", "i": "\\textit", "bi": "\\textbf"}.get(piece[2], "\\text")
                out.append(f"{wrap}{{{text}}}")
            elif piece[0] == "m":
                out.append(piece[1])
            else:
                raise self.fail(f"{piece[0]!r} inside math text")
        return "".join(out)

    def rows(self, body: str) -> str:
        rows = [row.strip() for row in re.split(r"\\cr(?![A-Za-z])", body)]
        if rows and not rows[-1]:
            rows.pop()
        return " \\\\ ".join(self.math(row) for row in rows)

    def bordermatrix(self, body: str) -> str:
        """Plain TeX ``\\bordermatrix``: first row and column are labels; drawn as a ruled array."""
        rows = [row.strip() for row in re.split(r"\\cr(?![A-Za-z])", body)]
        if rows and not rows[-1]:
            rows.pop()
        cells = [[self.math(cell.strip()) for cell in split_cells(row)] for row in rows]
        width = len(cells[0])
        if any(len(row) != width for row in cells) or cells[0][0]:
            raise self.fail("irregular \\bordermatrix")
        header, body = " & ".join(cells[0]), " \\\\ ".join(" & ".join(row) for row in cells[1:])
        return f"\\begin{{array}}{{c|{'c' * (width - 1)}}} {header} \\\\ \\hline {body} \\end{{array}}"

    def eqnarray(self, body: str) -> str:
        """``eqnarray`` rows ``a & = & b`` → ``aligned`` rows ``a &= b``."""
        rows = [row.strip() for row in re.split(r"\\\\(?:\[[^\]]*\])?", body)]
        if rows and not rows[-1]:
            rows.pop()
        out = []
        for row in rows:
            cells = split_cells(row)
            if len(cells) > 3:
                raise self.fail("eqnarray row with more than three cells")
            cells += [""] * (3 - len(cells))
            left, middle, right = (self.math(cell) for cell in cells)
            out.append(f"{left} &{middle} {right}".rstrip())
        return "\\begin{aligned} " + " \\\\ ".join(out) + " \\end{aligned}"

    # -- Markdown

    def markdown(self, pieces: list) -> str:
        if self.footnotes:
            for number, note in enumerate(self.footnotes, 1):
                pieces = [*pieces, ("p",), ("t", superscript(number) + " ", ""), *note]
        paragraphs: list[list[str]] = [[]]
        runs: list[tuple[str, str]] = []  # (style, text) of the current line, merged

        def end_runs():
            if runs:
                paragraphs[-1].append(("runs", list(runs)))
                runs.clear()

        for piece in pieces:
            kind = piece[0]
            if kind == "t":
                if runs and runs[-1][0] == piece[2]:
                    runs[-1] = (piece[2], runs[-1][1] + piece[1])
                else:
                    runs.append((piece[2], piece[1]))
            elif kind in ("m", "d"):
                runs.append(("math" if kind == "m" else "display", piece[1]))
            elif kind == "n":
                end_runs()
                paragraphs[-1].append(("break", None))
            elif kind == "p":
                end_runs()
                paragraphs.append([])
        end_runs()
        out = []
        for paragraph in paragraphs:
            text = ""
            for kind, value in paragraph:
                if kind == "break":
                    text = text.rstrip() + "\n"
                else:
                    text += self.line(value)
            text = re.sub(r"[ \u00a0]*\u00a0[ \u00a0]*", "\u00a0", text)
            lines = [re.sub(r"[ \u2009]+$", "", re.sub(r"^[ \u00a0\u2009]+", "", re.sub(r" {2,}", " ", line)))
                     for line in text.split("\n")]
            text = "\n".join(line for line in lines if line)
            if text:
                out.append(text)
        return "\n\n".join(out)

    def line(self, runs: list[tuple[str, str]]) -> str:
        out = ""
        for index, (style, value) in enumerate(runs):
            if style == "math":
                if not value:
                    out += " "
                    continue
                following = next((v for s, v in runs[index + 1:] if v), "")
                if following[:1].isdigit():
                    raise self.fail(f"inline math followed by a digit: {value!r}")
                out += f"${value}$"
            elif style == "display":
                out += f" $${value}$$ "
            else:
                if "*" in value or "_" in value or "\\" in value:
                    raise self.fail(f"Markdown-unsafe text {value!r}")
                value = value.replace("$", "\\$")
                if style and value.strip():
                    mark = "**" if "b" in style else "*"
                    lead = value[:len(value) - len(value.lstrip())]
                    trail = value[len(value.rstrip()):]
                    if out.endswith("*") and not lead:
                        raise self.fail("adjacent emphasis runs")
                    out += f"{lead}{mark}{value.strip()}{mark}{trail}"
                else:
                    out += value
        return out


def merge(style: str, extra: str | None) -> str:
    """The style after a switch: None keeps it, "" makes it upright, else adds bold/italic."""
    if extra is None:
        return style
    return "".join(sorted(set(style + extra))) if extra else ""


def superscript(number: int) -> str:
    return "".join("⁰¹²³⁴⁵⁶⁷⁸⁹"[int(digit)] for digit in str(number))


def item_mark(depth: int, index: int) -> str:
    """LaTeX's labels: (a) for parts, then i., then A. (exercises themselves are level 1)."""
    if depth == 1:
        return f"({'abcdefghijklmnopqrstuvwxyz'[index]})"
    if depth == 2:
        return roman(index + 1) + "."
    raise Unsupported("enumerate nested too deep")


def roman(number: int) -> str:
    return ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"][number - 1]


def read_until(src: str, at: int, close: str) -> tuple[str, int]:
    end = src.find(close, at)
    if end < 0:
        raise Unsupported(f"unclosed {close}")
    return src[at:end], end + len(close)


def read_math(src: str, at: int, close: str) -> tuple[str, int]:
    """Math up to the closing delimiter at brace depth 0 (``\\mbox{$x$}`` nests)."""
    depth, pos = 0, at
    while pos < len(src):
        char = src[pos]
        if char == "\\":
            pos += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0 and src.startswith(close, pos) and (close == "$$" or not src.startswith("$$", pos)):
            return src[at:pos], pos + len(close)
        elif depth == 0 and close == "$" and src.startswith("$$", pos):
            raise Unsupported(f"$$ inside inline math near {src[pos - 30:pos + 10]!r}")
        pos += 1
    raise Unsupported(f"unclosed {close} near {src[at:at + 40]!r}")


def split_cells(row: str) -> list[str]:
    """Split on ``&`` at brace and environment depth 0."""
    cells, depth, start, pos = [], 0, 0, 0
    while pos < len(row):
        char = row[pos]
        if char == "\\":
            if row.startswith("\\begin", pos):
                depth += 1
            elif row.startswith("\\end", pos):
                depth -= 1
            pos += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "&" and depth == 0:
            cells.append(row[start:pos])
            start = pos + 1
        pos += 1
    cells.append(row[start:])
    return cells


def split_items(body: str, names: tuple[str, ...]) -> list:
    """``[text before, (command, option, item text), …]`` for the items at nesting depth 0."""
    out: list = []
    depth, start, head = 0, 0, None
    pattern = re.compile(r"\\(begin|end)\s*\{[^}]*\}|\\(" + "|".join(names) + r")(?![A-Za-z])|\\.|[{}]", re.DOTALL)
    for match in pattern.finditer(body):
        token = match.group(0)
        if match.group(1) == "begin" or token == "{":
            depth += 1
        elif match.group(1) == "end" or token == "}":
            depth -= 1
        elif match.group(2) and depth == 0 and match.start() >= start:
            out.append(body[start:match.start()] if head is None else (*head, body[start:match.start()]))
            option, start = read_optional(body, match.end())
            head = (match.group(2), option)
    out.append(body[start:] if head is None else (*head, body[start:]))
    return out


# ---------------------------------------------------------------------------- exercises


def plain_title(src: str) -> str:
    """A section title as plain text (a manual line break becomes a space)."""
    pieces = Converter("title", {}).text(src)
    if any(piece[0] not in ("t", "n") for piece in pieces):
        raise Unsupported(f"section title with math: {src!r}")
    return " ".join("".join(piece[1] if piece[0] == "t" else " " for piece in pieces).split())


def extract(data: bytes, aux: Path | None = None) -> dict:
    texts = chapters(data)
    labels = {number: number_labels(number, text) for number, text in texts.items()}
    if aux is not None:
        check_aux(labels, aux)
    refs = {label: value for chapter in labels.values() for label, value in chapter.items()}
    exercises, skipped, seen = [], [], set()
    for chapter, text in texts.items():
        sections = [(m.start(), m) for m in re.finditer(r"\\section(?![A-Za-z*])", text)]
        titles = []
        for _, match in sections:
            _, end = read_optional(text, match.end())
            title, _ = read_arg(text, end)
            titles.append(plain_title(title))
        for block in re.finditer(r"\\begin\{LJSItem\}", text):
            body, _ = environment(text, block.end(), "LJSItem")
            index = sum(1 for start, _ in sections if start < block.start())
            if index == 0:
                raise Unsupported(f"exercises before the first section of chapter {chapter}")
            section = f"{chapter}.{index}"
            items = split_items(body, ("i", "istar", "item"))
            if items[0].strip():
                raise Unsupported(f"text before the first exercise of section {section}")
            numbered = {clean_label(label) for label in re.findall(r"\\label\{([^}]*)\}", body)
                        if refs[clean_label(label)].isdigit()}
            interlude = None
            for number, (command, option, item) in enumerate(items[1:], 1):
                if option is not None:
                    raise Unsupported(f"exercise {section}.{number} has an explicit label")
                label = f"{section}.{number}"
                item, note = split_interlude(item)
                if interlude is not None and (interlude[1] is None or number in interlude[1]):
                    item = "\\par ".join([interlude[0], item])
                if note is not None:
                    interlude = (note, interlude_targets(note, refs, numbered))
                if label in SKIP:
                    skipped.append({"label": label, "reason": SKIP[label]})
                    continue
                converter = Converter(label, refs)
                pieces = converter.text(item)
                markdown = converter.markdown(pieces)
                if not markdown:
                    raise Unsupported(f"exercise {label} is empty")
                if label in seen:
                    raise Unsupported(f"duplicate exercise {label}")
                seen.add(label)
                exercises.append({
                    "id": f"gs.{label}", "label": label, "chapter": chapter, "section": section,
                    "section_title": titles[index - 1], "number": number, "starred": command == "istar",
                    "text_md": markdown, "figure": converter.figure,
                    "program": bool(re.search(r"\bprograms?\b", markdown, re.IGNORECASE)),
                })
    unknown = SKIP.keys() - {item["label"] for item in skipped}
    if unknown:
        raise Unsupported(f"SKIP names unknown exercises {sorted(unknown)}")
    return {"source": {"title": "Exercises from Grinstead and Snell's Introduction to Probability",
                       "version": VERSION, "url": URL, "sha256": SHA256, "licence": "GFDL-1.3"},
            "exercises": exercises, "skipped": skipped}


def mask_nested(body: str) -> str:
    """``body`` with nested environments and groups blanked, so a search sees only its top level."""
    out, depth = [], 0
    pattern = re.compile(r"\\(begin|end)\s*\{[^}]*\}|\\.|[{}]|[^\\{}]+", re.DOTALL)
    for match in pattern.finditer(body):
        token = match.group(0)
        if match.group(1) == "begin" or token == "{":
            depth += 1
            out.append(" " * len(token))
        elif match.group(1) == "end" or token == "}":
            depth -= 1
            out.append(" " * len(token))
        else:
            out.append(token if depth == 0 else " " * len(token))
    return "".join(out)


def split_interlude(item: str) -> tuple[str, str | None]:
    """A ``\\medbreak`` at the end of an exercise starts a note about the exercises that follow."""
    at = find_command(mask_nested(item), "medbreak")
    if at < 0:
        return item, None
    return item[:at], item[at + len("\\medbreak"):]


def interlude_targets(note: str, refs: dict[str, str], numbered: set[str]) -> set[int] | None:
    """The exercises of this list a note names (``Exercises 15 and 16 concern…``); None
    means the rest of the section. ``numbered`` are the labels of this list's exercises."""
    named = {int(refs[label]) for label in map(clean_label, re.findall(r"\\ref\s*\{([^}]*)\}", note))
             if label in numbered}
    return named or None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tarball", type=Path, help="local prob.tar.gz (default: download it)")
    parser.add_argument("--aux", type=Path, help="directory with the official build's ch*.aux to check numbering")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    try:
        result = extract(source(args.tarball), args.aux)
    except Unsupported as error:
        raise SystemExit(f"Can't convert cleanly: {error}")
    for item in result["skipped"]:
        print(f"skipped {item['label']}: {item['reason']}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(dump(result))
    print(f"Wrote {len(result['exercises'])} exercises to {args.output}")


def dump(result: dict) -> str:
    return json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    main()
