"""A rika-server front over a synthetic workspace for the flashcard routes (server/src/flashcards).

The routes read time from ``KITTY_TEST_CLOCK`` (a file holding one ISO timestamp), so a test
moves the clock by rewriting it, as the in-process tests once monkeypatched ``flashcards.now``.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from kitty.db import connect
from terminal_support import front_site
from test_web_modules import make_root

TABLES = ("flash_sessions", "flash_studies", "flash_reviews", "flash_cards", "flash_notes", "flash_media",
          "flash_types", "flash_decks")


class FlashcardFront:
    """Calls the flashcard routes through the front; failures come back as (status, error)."""

    def __init__(self, running, settings, clock_file: Path):
        self.running, self.settings, self.clock_file = running, settings, clock_file
        self.now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
        self.events_from = 0
        self.set_clock(self.now)

    def set_clock(self, moment: datetime) -> None:
        self.now = moment
        self.clock_file.write_text(moment.isoformat(), encoding="utf-8")

    def reset(self) -> None:
        with closing(connect(self.settings.db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            for table in TABLES:
                conn.execute(f"DELETE FROM {table}")
            # record_events is append-only: a test reads what came after its reset.
            self.events_from = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM record_events").fetchone()[0]
        self.set_clock(datetime(2026, 10, 14, 12, tzinfo=timezone.utc))

    def call(self, path: str, body=None, method=None):
        return self.running.request(path, body, method=method)

    def ok(self, path: str, body=None):
        status, answer = self.call(path, body)
        assert status == 200, (path, status, answer)
        return answer

    def fails(self, path: str, body=None, *, status: int = 400, match: str = "") -> str:
        got, answer = self.call(path, body)
        assert got == status and match in answer["error"], (path, got, answer)
        return answer["error"]

    @contextmanager
    def db(self):
        with closing(connect(self.settings.db_path)) as conn:
            yield conn

    def row(self, table: str, ident) -> dict:
        with self.db() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (ident,)).fetchone()
            assert row is not None
            return dict(row)

    def overview(self) -> dict:
        return self.ok("/api/flashcards")

    def add(self, kind="basic", fields=None, deck=None, tags=None) -> dict:
        state = self.overview()
        typ = next(t for t in state["types"] if t["kind"] == kind)
        default = next(d for d in state["decks"] if d["name"] == "Default")
        fields = fields or ({"Text": "{{c1::hidden-one::hint}} + {{c2::hidden-two}} + {{c1::second-one}}", "Extra": "extra-only"}
                            if kind == "cloze" else {"Front": "synthetic-question", "Back": "answer-only-secret"})
        return self.ok("/api/flashcards/note", {"deck_id": deck or default["id"], "type_id": typ["id"], "fields": fields,
                                                "tags": tags or []})["note"]

    def deck(self, **body) -> dict:
        return self.ok("/api/flashcards/deck", body)["deck"]

    def start(self, **body) -> dict:
        return self.ok("/api/flashcards/review", body)

    def reveal(self, token: str) -> dict:
        return self.ok("/api/flashcards/reveal", {"token": token})

    def grade(self, token: str, rating: int, duration_ms: int = 0) -> dict:
        return self.ok("/api/flashcards/grade", {"token": token, "rating": rating, "duration_ms": duration_ms})

    def review_once(self, rating: int, **body):
        card = self.start(**body)["card"]
        assert card is not None
        self.reveal(card["token"])
        return card, self.grade(card["token"], rating, 1234)

    def undo(self) -> dict:
        return self.ok("/api/flashcards/undo", {})

    def browser(self, **query) -> dict:
        from urllib.parse import urlencode
        return self.ok("/api/flashcards/browser" + ("?" + urlencode(query) if query else ""))

    def action(self, **body) -> dict:
        return self.ok("/api/flashcards/action", body)


@contextmanager
def flashcard_front(tmp_path: Path, name: str = "root"):
    settings = make_root(tmp_path / name, ("workspace",), careers=False)
    clock = tmp_path / f"{name}-clock.txt"
    clock.write_text(datetime(2026, 10, 14, 12, tzinfo=timezone.utc).isoformat(), encoding="utf-8")
    with front_site(settings, env={"KITTY_TEST_CLOCK": str(clock)}) as running:
        yield FlashcardFront(running, settings, clock)


def sqlite_rows(path: Path, sql: str, args=()) -> list[dict]:
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, args)]
