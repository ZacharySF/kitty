"""Saving a screening answer I typed into config/answers.yaml.

answers.yaml is my file: I write the answers, the tool only files them. So this
appends one entry without reserializing existing answers or comments. An empty
flow list is converted to block form first. The answer text always comes from me; nothing here generates
one. After writing, the file is re-read with the config loader and restored if
it no longer validates, so a bad append can never break `load_config`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from kitty.config import AnswersFile, load_yaml, parse_file
from kitty.normalize import norm_question

ANSWERS_FILENAME = "answers.yaml"
HEADER = (
    "# Screening answers (gitignored). I write these; the tool never invents one.\n"
    "answers:\n"
)


def _entry_block(question: str, answer: str, category: str | None, indent: str) -> str:
    """One list item as YAML text, every scalar quoted safely by yaml.safe_dump."""
    entry = {"question": question, "answer": answer}
    if category:
        entry["category"] = category
    dumped = yaml.safe_dump(
        [entry], sort_keys=False, allow_unicode=True, default_flow_style=False, width=10_000
    )
    return "".join(indent + line if line.strip() else line for line in dumped.splitlines(keepends=True))


def _item_indent(text: str) -> str:
    """The indentation of the existing list items under `answers:` (two spaces if none)."""
    in_answers = False
    for line in text.splitlines():
        if line.startswith("answers:"):
            in_answers = True
        elif in_answers and line.lstrip().startswith("- "):
            return line[: len(line) - len(line.lstrip())]
    return "  "


def _existing_questions(path: Path) -> list[str]:
    """Questions already in the file, read without requiring the file to be valid yet."""
    data, _node = load_yaml(path)
    entries = data.get("answers") if isinstance(data, dict) else None
    return [str(e.get("question", "")) for e in entries or [] if isinstance(e, dict)]


def add_answer(
    config_dir: Path, question: str, answer: str, *, category: str | None = None
) -> None:
    """Append one question/answer entry to config/answers.yaml.

    Creates the file with an `answers:` header when it is missing. Raises
    ValueError for an empty question or answer, or when the question is already
    answered (an exact repeat would never be used). Raises ConfigError, with the
    file restored byte for byte, if the result does not validate.
    """
    question, answer = question.strip(), answer.strip()
    if not question or not answer:
        raise ValueError("both the question and the answer must be filled in")

    path = config_dir / ANSWERS_FILENAME
    original = path.read_bytes() if path.exists() else None
    text = original.decode("utf-8") if original is not None else HEADER
    if original is not None and norm_question(question) in {
        norm_question(q) for q in _existing_questions(path)
    }:
        raise ValueError(f"answers.yaml already has an answer for: {question}")

    # The shipped empty file uses `answers: []`. Appending a block item below
    # that flow sequence is invalid YAML; remove only the empty sequence token.
    node = yaml.compose(text)
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if (key.value == "answers" and isinstance(value, yaml.SequenceNode)
                    and value.flow_style and not value.value):
                text = text[:value.start_mark.index] + text[value.end_mark.index:]
                break

    if text and not text.endswith("\n"):
        text += "\n"
    if original is not None and not any(line.startswith("answers:") for line in text.splitlines()):
        text += "answers:\n"
    text += _entry_block(question, answer, category, _item_indent(text))

    path.write_text(text, encoding="utf-8")
    try:
        saved = parse_file(path, AnswersFile)
        last = saved.answers[-1] if saved.answers else None
        if last is None or (last.question, last.answer) != (question, answer):
            raise ValueError("the new entry did not read back as written")
    except Exception:
        if original is None:
            path.unlink()
        else:
            path.write_bytes(original)
        raise
