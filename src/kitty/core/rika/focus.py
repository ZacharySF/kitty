"""What the owner is looking at when they ask Rika something.

The page sends ``context: {view, item?: {kind, id?, title, detail?}}`` with a request
(web/src/lib/rika/focus.svelte.ts): the view on screen and the item open in it. It is
checked strictly here and becomes one line of the volatile system prompt, with the
item's title and detail quoted: they are the owner's data, never instructions.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

#: The page's views and their labels (web/src/lib/workspace.ts `navigation`; tests/test_rika_focus.py keeps them equal).
VIEW_LABELS: dict[str, str] = {
    "today": "Home", "brain": "Brain", "goals": "Goals", "rika": "Rika", "calendar": "Calendar",
    "opportunities": "Opportunities & projects", "herdr": "herdr", "tern": "Tern", "agent-skills": "All agent skills",
    "study": "Study", "math": "Math", "learnharness": "LearnHarness", "flashcards": "Flashcards",
    "notes": "Notes & canvas", "quick-notes": "Quick notes", "documents": "Documents & PDF", "schema": "Mind maps",
    "ai": "AI settings", "inbox": "New jobs", "queue": "Ready to apply", "jobs": "Find jobs",
    "pipeline": "My applications", "applications": "Application list", "favorites": "Saved jobs",
    "reminders": "Reminders", "sources": "Job sources", "resumes": "My resumes", "workshop": "Resume Workshop",
    "settings": "Settings", "profile": "My profile", "finder": "Find companies", "reports": "Reports & exports",
    "mail": "Email updates", "triage": "Rank jobs", "questions": "Application questions",
}
TITLE_MAX = 200
DETAIL_MAX = 300
ID_MAX = 120
_KIND = re.compile(r"[a-z][a-z_]{0,39}")
#: Integer ids the page can hold exactly (JavaScript's safe integers).
_INT_MAX = 2**53 - 1


def _text(value: Any, name: str, limit: int) -> str:
    """A one-line string of 1..limit printable characters (controls, line breaks and lone surrogates refused)."""
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"context {name} is text of at most {limit} characters")
    if any(unicodedata.category(char) in ("Cc", "Cs", "Zl", "Zp") for char in value):
        raise ValueError(f"context {name} is one line of printable text")
    return value


def validate(context: Any) -> dict | None:
    """The context as sent, or None when there is none; ValueError for anything else."""
    if context is None:
        return None
    if not isinstance(context, dict) or set(context) - {"view", "item"} or "view" not in context:
        raise ValueError("context is {view, item?}")
    view = context["view"]
    if not isinstance(view, str) or view not in VIEW_LABELS:
        raise ValueError("context view is not a rika! view")
    if "item" not in context:
        return {"view": view}
    item = context["item"]
    if not isinstance(item, dict) or set(item) - {"kind", "id", "title", "detail"} or not {"kind", "title"} <= set(item):
        raise ValueError("context item is {kind, id?, title, detail?}")
    if not isinstance(item["kind"], str) or not _KIND.fullmatch(item["kind"]):
        raise ValueError("context item kind is a short lowercase name")
    clean: dict[str, Any] = {"kind": item["kind"], "title": _text(item["title"], "title", TITLE_MAX)}
    if "id" in item:
        ident = item["id"]
        if isinstance(ident, bool) or not (isinstance(ident, (int, str))):
            raise ValueError("context item id is a whole number or text")
        if isinstance(ident, int) and abs(ident) > _INT_MAX:
            raise ValueError("context item id is out of range")
        clean["id"] = _text(ident, "id", ID_MAX) if isinstance(ident, str) else ident
    if "detail" in item:
        clean["detail"] = _text(item["detail"], "detail", DETAIL_MAX)
    return {"view": view, "item": clean}


def line(context: dict | None) -> str:
    """One line for the volatile prompt: `Looking at: <view> · <kind> "<title>" (id <id>) — "<detail>"`."""
    if not context:
        return ""
    said = f"Looking at: {VIEW_LABELS[context['view']]}"
    item = context.get("item")
    if not item:
        return said
    said += f" · {item['kind']} {_quote(item['title'])}"
    if "id" in item:
        said += f" (id {_quote(item['id']) if isinstance(item['id'], str) else item['id']})"
    if "detail" in item:
        said += f" — {_quote(item['detail'])}"
    return said


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
