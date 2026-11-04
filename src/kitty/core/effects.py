"""The effects kernel (kitty paper §16): one approval protocol for every risky action.

    propose -> digest-bound approval -> re-check preconditions -> execute

An effect is a `kind`, a canonical JSON `payload`, named `preconditions` (the values the
proposer saw, re-evaluated against current state before approval and again before
execution) and the `actor` that proposed it (kitty.core.blocks.actor: user, agent,
client, system, peer). Its `digest` is the SHA-256 of the canonical (kind, payload,
preconditions) triple. The person approves that exact digest; execution recomputes the
preconditions and the digest, and any difference refuses the effect for good: the
proposer proposes again and the person reviews again.

Approvals are single-use, short-lived (`Kind.approval_lifetime`) and bound to the
approving session: only the same rika! window session may execute them. Only a ``user``
actor with a session approves or denies; agents, clients, peers and the system can
propose (a kind lists who may) but never approve. Policy-approved kinds
(`Kind.approver == "policy"`) are the exception: a computed policy approves them in
memory (`policy_approve`), never a model or another agent.

Every decision names the `kinds` its caller owns: `approve`, `execute`,
`approve_and_execute` and `deny` refuse (403, untouched) an effect of any other kind,
so a route can only ever decide its own kind of effect, never one it was handed by ID.

Kinds come in two types. `Executable` kinds have an executor (`run`), called inside the
same immediate transaction that re-checks the preconditions and records ``executed``;
external work (a network push, a browser run) happens after that commit, using what
`run` returned. `ProposalOnly` kinds have no executor and cannot be given one: the
registry refuses an `Executable` under a proposal-only name, `execute` raises
`NotExecutable` (a `TypeError`) for them, and approving one only records the person's
decision. ``submit_application`` is such a kind, so kitty's "never submit an
application" rule is a type, not a convention (kitty.core.effect_kinds).

Stored effects live in ``effects`` and their transitions in ``effect_events``
(migration 053), both append-only: proposed -> approved | denied | refused | expired,
approved -> executed | refused | expired. Unique indexes allow at most one approval and
one terminal event per effect, so a second execution cannot commit even when two
callers race. This module imports no web code.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Callable, Collection, Iterable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from kitty.core.blocks.actor import KINDS as ACTOR_KINDS
from kitty.core.blocks.actor import Actor
from kitty.db.connection import transaction

STATES = ("proposed", "approved", "denied", "executed", "refused", "expired")
#: States after which nothing else happens to an effect.
TERMINAL = frozenset({"denied", "executed", "refused", "expired"})
#: Kind names that may never have an executor, whatever registers them.
PROPOSAL_ONLY = frozenset({"submit_application"})
#: Payload, preconditions and preview together stay below this many bytes.
MAX_BYTES = 512 * 1024
DEFAULT_LIFETIME = timedelta(hours=1)
DEFAULT_APPROVAL_LIFETIME = timedelta(minutes=2)


class EffectError(Exception):
    """A refusal with an HTTP-like ``status``; the message is safe to show."""

    status = 400

    def __init__(self, message: str, status: int | None = None, *, changed: Iterable[str] = ()):
        super().__init__(message)
        if status is not None:
            self.status = status
        #: The preconditions whose values differ from the approved ones.
        self.changed = tuple(changed)


class UnknownEffect(EffectError):
    status = 404


class Forbidden(EffectError):
    """Who asked may not do this (an agent approving, another session executing)."""

    status = 403


class Refused(EffectError):
    """The effect cannot go ahead as approved: changed, expired, decided or already used."""

    status = 409


class NotExecutable(EffectError, TypeError):
    """Executing a kind that has no executor: a type error, never a runtime choice."""

    status = 400


def now() -> datetime:
    return datetime.now(UTC)


def canonical(value: Any) -> str:
    """The one JSON text of a value: sorted keys, no whitespace, no NaN, UTF-8 as is."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise EffectError("Effect payloads and preconditions are plain JSON values") from None


def digest(kind: str, payload: Any, preconditions: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical (kind, payload, expected precondition values)."""
    text = canonical({"kind": kind, "payload": payload, "preconditions": dict(preconditions)})
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hashed(value: Any) -> str:
    """A precondition value that must not be stored as is (credentials, private config)."""
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def changed_names(expected: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    """Names whose canonical value differs, including ones only one side has."""
    names = sorted(set(expected) | set(current))
    return [name for name in names if name not in expected or name not in current
            or canonical(expected[name]) != canonical(current[name])]


Reviewer = Literal["prompt", "inline"]
Approver = Literal["user", "policy"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Kind:
    """What an effect is and how its preconditions are read.

    `preconditions(ctx, payload)` returns {name: current value} from current state;
    `validate(ctx, raw)` returns the canonical payload (raising EffectError);
    `preview(ctx, payload, preconditions)` what the person reviews. `ctx` is whatever
    the caller passes (a connection, the web app); the kernel never looks inside.
    `review` is where a person approves it: "prompt" (the pending-approvals list) or
    "inline" (its own view, which passes that view's options). `remote` kinds read
    their preconditions over the network, so they are read just before (not inside) the
    claim transaction.
    """

    name: str
    title: str
    proposers: frozenset[str]
    preconditions: Callable[[Any, Any], Mapping[str, Any]]
    validate: Callable[[Any, Any], Any] | None = None
    preview: Callable[[Any, Any, Mapping[str, Any]], Any] | None = None
    review: Reviewer = "inline"
    approver: Approver = "user"
    policy: Callable[[Any, Mapping[str, Any]], bool] | None = None
    remote: bool = False
    lifetime: timedelta = DEFAULT_LIFETIME
    approval_lifetime: timedelta = DEFAULT_APPROVAL_LIFETIME

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum() or not self.name.islower():
            raise ValueError("An effect kind name is lower_snake_case")
        if not self.proposers or set(self.proposers) - set(ACTOR_KINDS):
            raise ValueError("An effect kind lists which actor kinds may propose it")
        if (self.approver == "policy") != (self.policy is not None):
            raise ValueError("Policy-approved kinds, and only they, have a policy")
        if self.review not in ("prompt", "inline") or self.approver not in ("user", "policy"):
            raise ValueError("Unknown review place or approver")


@dataclass(frozen=True, slots=True, kw_only=True)
class Executable(Kind):
    """A kind with an executor: `run(ctx, effect)` inside the claim transaction."""

    run: Callable[[Any, Effect], Any]

    def __post_init__(self) -> None:
        if self.name in PROPOSAL_ONLY:
            raise TypeError(f"{self.name} is proposal-only: it can never have an executor")
        super(Executable, self).__post_init__()


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposalOnly(Kind):
    """A kind with no executor, ever: approving it records the person's decision and
    `handoff` tells them what they do themselves."""

    handoff: str


class Registry:
    """Effect kinds by name. A name is registered once; a proposal-only name can only
    ever hold a `ProposalOnly` kind, so no executor can be registered for it."""

    def __init__(self) -> None:
        self._kinds: dict[str, Kind] = {}

    def register(self, kind: Kind) -> Kind:
        if not isinstance(kind, Kind):
            raise TypeError("Register an effect Kind")
        if (kind.name in PROPOSAL_ONLY) != isinstance(kind, ProposalOnly):
            raise TypeError(f"{kind.name}: proposal-only names take exactly a ProposalOnly kind")
        if kind.name in self._kinds:
            raise ValueError(f"Effect kind {kind.name} is already registered")
        self._kinds[kind.name] = kind
        return kind

    def get(self, name: Any) -> Kind:
        kind = self._kinds.get(name) if isinstance(name, str) else None
        if kind is None:
            raise UnknownEffect("Unknown effect kind")
        return kind

    def executable(self, name: Any) -> Executable:
        kind = self.get(name)
        if not isinstance(kind, Executable):
            raise NotExecutable(f"{kind.name} has no executor: {getattr(kind, 'handoff', 'it cannot be executed')}")
        return kind

    def __contains__(self, name: object) -> bool:
        return name in self._kinds

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._kinds))

    def reviewed_in(self, review: Reviewer) -> frozenset[str]:
        """The kinds a person reviews in one place ("prompt": the pending-approvals list)."""
        return frozenset(name for name, kind in self._kinds.items() if kind.review == review)


#: Every kind kitty registers (kitty.core.effect_kinds and the modules owning the others).
REGISTRY = Registry()


# --------------------------------------------------------------------- in memory


@dataclass(frozen=True, slots=True)
class Proposal:
    """An effect's exact identity, before or without storage."""

    kind: str
    payload: Any
    preconditions: Mapping[str, Any]
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "preconditions", dict(self.preconditions))
        object.__setattr__(self, "digest", digest(self.kind, self.payload, self.preconditions))


class Grant:
    """A policy's in-memory, single-use approval of one proposal's exact digest."""

    __slots__ = ("_digest", "_used")

    def __init__(self, proposal: Proposal) -> None:
        self._digest = proposal.digest
        self._used = False

    @property
    def used(self) -> bool:
        return self._used

    def covers(self, proposal: Proposal) -> bool:
        return not self._used and secrets.compare_digest(self._digest, proposal.digest)

    def consume(self, proposal: Proposal) -> None:
        """Spend the grant on exactly the approved proposal; a second use is refused."""
        if self._used:
            raise Refused("This approval was already used")
        if not secrets.compare_digest(self._digest, proposal.digest):
            raise Refused("This is not the approved effect")
        self._used = True


def policy_approve(kind: Kind, proposal: Proposal) -> Grant:
    """The kind's computed policy approves the proposal, or it is refused."""
    if kind.approver != "policy" or kind.policy is None or proposal.kind != kind.name:
        raise Forbidden("Only a policy-approved kind is approved by its policy")
    if kind.policy(proposal.payload, proposal.preconditions) is not True:
        raise Refused("The policy does not allow this effect")
    return Grant(proposal)


# --------------------------------------------------------------------- stored


@dataclass(frozen=True, slots=True)
class Effect:
    """A stored effect and its latest state."""

    id: str
    kind: str
    payload: Any
    preconditions: Mapping[str, Any]
    preview: Any
    digest: str
    actor: Actor
    state: str
    created_at: str
    expires_at: str
    #: The open approval's session and expiry (state ``approved``), else None.
    approved_session: str | None = None
    approval_expires_at: str | None = None
    #: Options the approver chose (not part of the digest), recorded on the approval.
    options: Mapping[str, Any] = field(default_factory=dict)

    def expected(self, name: str) -> Any:
        return self.preconditions[name]

    def require(self, name: str, current: Any) -> None:
        """Re-check one precondition at the moment of use (for a long-running execution)."""
        if name not in self.preconditions or canonical(self.preconditions[name]) != canonical(current):
            raise Refused("Approved state changed; review again", changed=(name,))

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": REGISTRY.get(self.kind).title if self.kind in REGISTRY else self.kind,
                "payload": self.payload, "preconditions": dict(self.preconditions), "preview": self.preview,
                "digest": self.digest, "actor": self.actor.as_dict(), "state": self.state,
                "created_at": self.created_at, "expires_at": self.expires_at,
                "approval_expires_at": self.approval_expires_at}


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _new_id() -> str:
    return secrets.token_hex(16)


def _latest(conn: sqlite3.Connection, effect_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM effect_events WHERE effect_id=? ORDER BY seq DESC LIMIT 1", (effect_id,)).fetchone()


def _load(conn: sqlite3.Connection, effect_id: Any) -> Effect:
    if not isinstance(effect_id, str) or len(effect_id) != 32 or set(effect_id) - set("0123456789abcdef"):
        raise UnknownEffect("Effect not found")
    row = conn.execute("SELECT * FROM effects WHERE id=?", (effect_id,)).fetchone()
    if row is None:
        raise UnknownEffect("Effect not found")
    latest = _latest(conn, effect_id)
    approved = latest if latest is not None and latest["state"] == "approved" else None
    return Effect(
        row["id"], row["kind"], json.loads(row["payload"]), json.loads(row["preconditions"]),
        json.loads(row["preview"]), row["digest"],
        Actor(row["actor"], row["actor_name"], row["actor_session"]),
        latest["state"] if latest is not None else "proposed", row["created_at"], row["expires_at"],
        approved["actor_session"] if approved is not None else None,
        approved["expires_at"] if approved is not None else None,
        json.loads(approved["detail"]).get("options", {}) if approved is not None and approved["detail"] else {},
    )


def get(conn: sqlite3.Connection, effect_id: Any) -> Effect:
    return _load(conn, effect_id)


def _event(conn: sqlite3.Connection, effect: Effect | str, state: str, actor: Actor, at: datetime, *,
           digest_value: str | None = None, detail: Mapping[str, Any] | None = None,
           expires_at: datetime | None = None) -> None:
    effect_id = effect if isinstance(effect, str) else effect.id
    conn.execute(
        "INSERT INTO effect_events(effect_id,state,actor,actor_name,actor_session,digest,detail,at,expires_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (effect_id, state, actor.kind, actor.name, actor.session, digest_value,
         canonical(dict(detail)) if detail else None, _stamp(at), _stamp(expires_at) if expires_at else None))


def _owned(effect: Effect, kinds: Collection[str]) -> None:
    """A route decides only the kinds it owns; another view's effect is refused untouched."""
    if isinstance(kinds, (str, bytes)) or not isinstance(kinds, Collection) or not kinds:
        raise TypeError("kinds is the non-empty set of effect kind names the caller owns")
    if effect.kind not in kinds:
        raise Forbidden("Review this effect in its own view")


def _user(actor: Actor, action: str) -> None:
    if not isinstance(actor, Actor) or actor.kind != "user":
        raise Forbidden(f"Only you, in rika!'s own window, can {action} an effect; agents can propose but never {action}")
    if not actor.session:
        raise Forbidden(f"An effect is {action}d only from a rika! window session")


def propose(conn: sqlite3.Connection, kind_name: Any, payload: Any, *, actor: Actor, ctx: Any = None,
            registry: Registry = REGISTRY, at: datetime | None = None) -> Effect:
    """Record a proposal: its canonical payload, the precondition values read now and their digest.

    Inside an open transaction (the agent mediator's, kitty.core.agent_access) it joins
    it, so the proposal commits or rolls back with that call's journal rows."""
    kind = registry.get(kind_name)
    if not isinstance(actor, Actor) or actor.kind not in kind.proposers:
        raise Forbidden(f"A {actor.kind if isinstance(actor, Actor) else 'unknown'} actor cannot propose {kind.name}")
    if kind.approver == "policy":
        raise EffectError("Policy-approved effects are approved in memory, not stored for review")
    clean = kind.validate(ctx, payload) if kind.validate is not None else payload
    expected = dict(kind.preconditions(ctx, clean))
    shown = kind.preview(ctx, clean, expected) if kind.preview is not None else None
    proposal = Proposal(kind.name, clean, expected)
    texts = canonical(clean), canonical(expected), canonical(shown)
    if sum(len(text.encode("utf-8")) for text in texts) > MAX_BYTES:
        raise EffectError("This effect is too large to review")
    moment = at or now()
    effect_id = _new_id()
    with nullcontext() if conn.in_transaction else transaction(conn, immediate=True):
        conn.execute(
            "INSERT INTO effects(id,kind,payload,preconditions,preview,digest,actor,actor_name,actor_session,created_at,expires_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (effect_id, kind.name, *texts, proposal.digest, actor.kind, actor.name, actor.session,
             _stamp(moment), _stamp(moment + kind.lifetime)))
        _event(conn, effect_id, "proposed", actor, moment, digest_value=proposal.digest)
    return _load(conn, effect_id)


def _expired(effect: Effect, moment: datetime) -> bool:
    deadline = effect.approval_expires_at if effect.state == "approved" else effect.expires_at
    return deadline is None or datetime.fromisoformat(deadline) <= moment


@dataclass(frozen=True, slots=True)
class _Problem:
    state: str
    message: str
    changed: tuple[str, ...] = ()


def _matches(effect: Effect, presented: Any) -> bool:
    return isinstance(presented, str) and secrets.compare_digest(presented.encode(), effect.digest.encode())


def _current(kind: Kind, ctx: Any, effect: Effect) -> tuple[dict, str]:
    current = dict(kind.preconditions(ctx, effect.payload))
    return current, digest(kind.name, effect.payload, current)


def _problem(kind: Kind, ctx: Any, effect: Effect, presented: Any, moment: datetime,
             read: tuple[dict, str] | None) -> _Problem | None:
    """Expiry, the presented digest, then every precondition and the recomputed digest."""
    if _expired(effect, moment):
        return _Problem("expired", "This approval expired; ask again" if effect.state == "approved"
                        else "This proposal expired; propose it again")
    if not _matches(effect, presented):
        return _Problem("refused", "The reviewed effect differs from this one; review it again")
    current, recomputed = read if read is not None else _current(kind, ctx, effect)
    if recomputed != effect.digest:
        return _Problem("refused", "Something this effect depends on changed since it was proposed; review it again",
                        tuple(changed_names(effect.preconditions, current)))
    return None


class _Claim:
    """One immediate transaction. A refusal found inside it is recorded and committed
    (refusals are final); any other error rolls everything back."""

    def __init__(self, conn: sqlite3.Connection, actor: Actor, moment: datetime):
        self.conn, self.actor, self.moment = conn, actor, moment

    def __enter__(self) -> _Claim:
        self.conn.execute("BEGIN IMMEDIATE")
        return self

    def refuse(self, effect: Effect, problem: _Problem) -> None:
        _event(self.conn, effect, problem.state, self.actor, self.moment, digest_value=effect.digest,
               detail={"reason": problem.message, **({"changed": list(problem.changed)} if problem.changed else {})})
        self.conn.execute("COMMIT")
        self.conn.execute("BEGIN")  # the empty transaction __exit__ rolls back
        raise Refused(problem.message, changed=problem.changed)

    def __exit__(self, kind, value, traceback) -> None:
        self.conn.execute("ROLLBACK" if kind is not None else "COMMIT")


def _open(conn: sqlite3.Connection, effect_id: Any, kinds: Collection[str], registry: Registry) -> tuple[Effect, Kind]:
    effect = _load(conn, effect_id)
    _owned(effect, kinds)
    return effect, registry.get(effect.kind)


def _approval(claim: _Claim, kind: Kind, effect: Effect, options: Mapping[str, Any] | None) -> None:
    _event(claim.conn, effect, "approved", claim.actor, claim.moment, digest_value=effect.digest,
           detail={"options": dict(options)} if options else None, expires_at=claim.moment + kind.approval_lifetime)


def approve(conn: sqlite3.Connection, effect_id: Any, presented: Any, *, kinds: Collection[str], actor: Actor,
            ctx: Any = None, options: Mapping[str, Any] | None = None, registry: Registry = REGISTRY,
            at: datetime | None = None) -> Effect:
    """The person approves this exact digest, after its preconditions are re-checked.

    The approval is single-use, lives `Kind.approval_lifetime` and belongs to `actor`'s
    session. For a proposal-only kind it is the person's recorded decision and the end.
    """
    _user(actor, "approve")
    moment = at or now()
    effect, kind = _open(conn, effect_id, kinds, registry)
    if kind.approver != "user":
        raise Forbidden("This effect is approved by its policy, not by a person")
    read = _current(kind, ctx, effect) if kind.remote else None
    with _Claim(conn, actor, moment) as claim:
        effect = _load(conn, effect.id)
        if effect.state != "proposed":
            raise Refused(f"This effect is already {effect.state}")
        if (problem := _problem(kind, ctx, effect, presented, moment, read)) is not None:
            claim.refuse(effect, problem)
        _approval(claim, kind, effect, options)
    return _load(conn, effect.id)


def deny(conn: sqlite3.Connection, effect_id: Any, *, kinds: Collection[str], actor: Actor,
         at: datetime | None = None) -> Effect:
    """The person says no; the proposer may propose again."""
    _user(actor, "deny")
    with transaction(conn, immediate=True):
        effect = _load(conn, effect_id)
        _owned(effect, kinds)
        if effect.state not in ("proposed", "approved"):
            raise Refused(f"This effect is already {effect.state}")
        _event(conn, effect, "denied", actor, at or now(), digest_value=effect.digest)
    return _load(conn, effect.id)


def execute(conn: sqlite3.Connection, effect_id: Any, presented: Any, *, kinds: Collection[str], actor: Actor,
            ctx: Any = None, registry: Registry = REGISTRY, at: datetime | None = None) -> tuple[Effect, Any]:
    """Consume the approval: re-check every precondition and the digest, record
    ``executed`` and run the executor, all in one immediate transaction.

    Returns the executed effect and what the executor returned. An executor error rolls
    everything back, so the approval stays unused until it expires. A proposal-only kind
    raises `NotExecutable` before anything else is checked.
    """
    effect, kind = _open(conn, effect_id, kinds, registry)
    executable = registry.executable(kind.name)
    _user(actor, "execute")
    moment = at or now()
    read = _current(executable, ctx, effect) if executable.remote else None
    with _Claim(conn, actor, moment) as claim:
        effect = _load(conn, effect.id)
        if effect.state != "approved":
            raise Refused("Approve this effect first" if effect.state == "proposed" else f"This effect is already {effect.state}")
        if effect.approved_session != actor.session:
            raise Forbidden("This approval belongs to another rika! window session")
        if (problem := _problem(executable, ctx, effect, presented, moment, read)) is not None:
            claim.refuse(effect, problem)
        _event(conn, effect, "executed", actor, moment, digest_value=effect.digest)
        result = executable.run(ctx, effect)
    return _load(conn, effect.id), result


def approve_and_execute(conn: sqlite3.Connection, effect_id: Any, presented: Any, *, kinds: Collection[str],
                        actor: Actor, ctx: Any = None, options: Mapping[str, Any] | None = None,
                        registry: Registry = REGISTRY, at: datetime | None = None) -> tuple[Effect, Any]:
    """An inline view's Approve: approval and execution in one immediate transaction.

    Nothing commits unless the executor succeeds: an executor refusal (an underfunded
    budget, say) leaves the proposal pending exactly as it was.
    """
    _user(actor, "approve")
    moment = at or now()
    effect, kind = _open(conn, effect_id, kinds, registry)
    executable = registry.executable(kind.name)
    if executable.approver != "user":
        raise Forbidden("This effect is approved by its policy, not by a person")
    read = _current(executable, ctx, effect) if executable.remote else None
    with _Claim(conn, actor, moment) as claim:
        effect = _load(conn, effect.id)
        if effect.state != "proposed":
            raise Refused(f"This effect is already {effect.state}")
        if (problem := _problem(executable, ctx, effect, presented, moment, read)) is not None:
            claim.refuse(effect, problem)
        _approval(claim, executable, effect, options)
        effect = _load(conn, effect.id)
        _event(conn, effect, "executed", actor, moment, digest_value=effect.digest)
        result = executable.run(ctx, effect)
    return _load(conn, effect.id), result


def pending(conn: sqlite3.Connection, *, review: Reviewer | None = "prompt", registry: Registry = REGISTRY,
            at: datetime | None = None, limit: int = 50) -> list[Effect]:
    """Proposals waiting for a person (unexpired, latest state ``proposed``), oldest first."""
    moment = _stamp(at or now())
    rows = conn.execute(
        "SELECT e.id FROM effects e JOIN effect_events v ON v.effect_id=e.id"
        " WHERE v.seq=(SELECT MAX(seq) FROM effect_events WHERE effect_id=e.id) AND v.state='proposed'"
        " AND e.expires_at>? ORDER BY e.created_at, e.id", (moment,)).fetchall()
    found = []
    for row in rows:
        effect = _load(conn, row["id"])
        if effect.kind in registry.names() and (review is None or registry.get(effect.kind).review == review):
            found.append(effect)
        if len(found) >= limit:
            break
    return found


def recent(conn: sqlite3.Connection, *, limit: int = 20) -> list[Effect]:
    """The latest decided effects, newest first (the review list's history)."""
    rows = conn.execute(
        "SELECT e.id FROM effects e JOIN effect_events v ON v.effect_id=e.id"
        " WHERE v.seq=(SELECT MAX(seq) FROM effect_events WHERE effect_id=e.id) AND v.state<>'proposed'"
        " ORDER BY v.seq DESC LIMIT ?", (limit,)).fetchall()
    return [_load(conn, row["id"]) for row in rows]


def events(conn: sqlite3.Connection, effect_id: Any) -> list[dict]:
    effect = _load(conn, effect_id)
    return [{"state": row["state"], "actor": Actor(row["actor"], row["actor_name"], row["actor_session"]).as_dict(),
             "digest": row["digest"], "detail": json.loads(row["detail"]) if row["detail"] else None,
             "at": row["at"], "expires_at": row["expires_at"]}
            for row in conn.execute("SELECT * FROM effect_events WHERE effect_id=? ORDER BY seq", (effect.id,))]
