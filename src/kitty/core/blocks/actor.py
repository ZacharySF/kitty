"""Who made a block transaction: the ``actor`` columns of ``op_log`` (migration 051).

Every ``op_log`` row records, besides the device it was written on, the kind of actor
that wrote it, a display name and (for agents and clients) a session:

- ``user``: the person at this device (browser windows, the editor, the TUI);
- ``agent``: a program acting for them: ``kitty mcp`` (one session per server process,
  named by the MCP client's ``initialize``), ``kitty notes`` (one session per command,
  or ``KITTY_AGENT_SESSION``), and rika!'s own assistant (one per conversation);
- ``client``: a paired client (the Tern plugin, the browser extension), named by its
  pairing, its session the client ID;
- ``system``: rika! itself (migrations, history compaction);
- ``peer``: a merge from a paired device, named by that device; ``via`` lists the
  actors whose changes the merge carried from it (their kind, name and session).

The entry point that starts a transaction sets the actor with :func:`acting`; a
transaction with none set is the person at this device. ``transact(actor=...)``
overrides it for one transaction.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from collections.abc import Iterable, Iterator

KINDS = ("user", "agent", "client", "system", "peer")
MAX_NAME = 100
MAX_SESSION = 100
#: At most this many distinct actors are carried on one merged row.
MAX_VIA = 20
_SESSION = re.compile(r"[A-Za-z0-9._:-]{1,100}\Z")


@dataclass(frozen=True)
class Actor:
    kind: str = "user"
    name: str | None = None
    session: str | None = None
    #: For a peer merge: the actors on the other device whose changes it carried.
    via: tuple[Actor, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"Unknown actor kind {self.kind!r}")
        if self.name is not None:
            object.__setattr__(self, "name", clean_name(self.name))
        if self.session is not None and not _SESSION.fullmatch(self.session):
            raise ValueError("An actor session is 1-100 letters, digits or . _ : -")

    def as_dict(self) -> dict:
        out: dict[str, Any] = {"kind": self.kind, "name": self.name, "session": self.session}
        if self.via:
            out["via"] = [item.as_dict() for item in self.via]
        return out

    def columns(self) -> tuple[str, str | None, str | None, str | None]:
        """``(actor, actor_name, actor_session, actor_via)`` for an ``op_log`` row."""
        via = json.dumps([item.as_dict() for item in self.via], ensure_ascii=False, separators=(",", ":")) if self.via else None
        return self.kind, self.name, self.session, via


def clean_name(value: Any) -> str | None:
    """A display name: printable, single line, at most MAX_NAME characters (None when empty)."""
    if not isinstance(value, str):
        return None
    text = " ".join("".join(char if char.isprintable() or char.isspace() else "" for char in value).split())[:MAX_NAME]
    return text or None


def parse(value: Any) -> Actor | None:
    """An actor from its ``as_dict`` form (another device's, so untrusted); None when malformed."""
    if not isinstance(value, dict) or value.get("kind") not in KINDS or value.get("kind") == "peer":
        return None
    session = value.get("session")
    if session is not None and (not isinstance(session, str) or not _SESSION.fullmatch(session)):
        return None
    return Actor(value["kind"], clean_name(value.get("name")), session)


def merged_via(actors: Iterable[Actor]) -> tuple[Actor, ...]:
    """Distinct non-peer actors in first-seen order (a peer row contributes what it carried)."""
    seen: dict[tuple, Actor] = {}
    for item in actors:
        for leaf in (item.via if item.kind == "peer" else (item,)):
            key = (leaf.kind, leaf.name, leaf.session)
            if key not in seen and len(seen) < MAX_VIA:
                seen[key] = Actor(leaf.kind, leaf.name, leaf.session)
    return tuple(seen.values())


def from_row(row: Any) -> Actor:
    """The actor recorded on an ``op_log`` row (``actor``, ``actor_name``, ``actor_session``, ``actor_via``)."""
    via: tuple[Actor, ...] = ()
    if row["actor_via"]:
        try:
            via = tuple(item for item in (parse(raw) for raw in json.loads(row["actor_via"])) if item is not None)
        except ValueError:
            via = ()
    return Actor(row["actor"], row["actor_name"], row["actor_session"], via)


USER = Actor("user")
MIGRATION = Actor("system", "migration")
COMPACTION = Actor("system", "compaction")

_current: ContextVar[Actor] = ContextVar("kitty_block_actor", default=USER)


def current() -> Actor:
    """The actor transactions started in this context are recorded under."""
    return _current.get()


@contextmanager
def acting(actor: Actor) -> Iterator[Actor]:
    """Record every transaction started inside the block under `actor`."""
    token = _current.set(actor)
    try:
        yield actor
    finally:
        _current.reset(token)


def new_session() -> str:
    return str(uuid.uuid4())


def cli_agent(environ: dict[str, str] | None = None) -> Actor:
    """``kitty notes``: one agent session per command, or ``KITTY_AGENT_SESSION`` (named by ``KITTY_AGENT_NAME``)."""
    env = os.environ if environ is None else environ
    session = env.get("KITTY_AGENT_SESSION", "").strip() or new_session()
    if not _SESSION.fullmatch(session):
        raise ValueError("KITTY_AGENT_SESSION is 1-100 letters, digits or . _ : -")
    return Actor("agent", clean_name(env.get("KITTY_AGENT_NAME")) or "kitty notes", session)
