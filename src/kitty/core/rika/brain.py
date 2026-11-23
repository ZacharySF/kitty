"""Which service Rika thinks with on this computer: OpenRouter (Settings → AI's key,
catalog, prices and daily budget) or omp (the omp coding agent's own sign-in, reached
through ``omp auth-gateway stdio``; see :mod:`kitty.core.rika.omp`), which model she
talks on and how hard she thinks.

The choice belongs to this computer, not to the build: omp has to be installed and
signed in here. It is kept owner-only in ``.private/rika/brain.json`` as
``{"provider": "openrouter"|"omp", "models": {tier: model}, "model": model,
"thinking": level}``; a missing or unreadable file is OpenRouter, rika!'s behaviour
before the choice existed. An empty tier model is :data:`OMP_DEFAULTS`' model for that
tier. OpenRouter's tier models stay in Settings → AI (:mod:`kitty.core.rika.router`).

``model``, chosen beside Rika's field, pins her chat to one model of the brain's
:func:`catalog` on every tier; ``""`` is Auto, the router's tier models. A pinned omp
model is an omp selector; a pinned OpenRouter model must be in Settings → AI's list.
Changing the provider drops the pin (it names the other provider's model).

The thinking level (:data:`THINKING`, chosen beside Rika's field) rides on each request:
``reasoning_effort`` for omp, OpenRouter's ``reasoning.effort`` for models that accept
it (:mod:`kitty.core.rika.llm`); ``off`` sends none. omp maps a level a model lacks to
the nearest one it has.

Python modules may read :func:`catalog` (and :func:`auto_thinking`) to offer the same
choice, and pass a model id and ``thinking=`` to :func:`kitty.core.rika.llm.prepare`.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field

from kitty.core.rika import omp, router
from kitty.core.rika.router import TIERS

PROVIDERS = ("openrouter", "omp")
#: omp's model for each tier when none is chosen: Claude, which a Claude subscription covers.
OMP_DEFAULTS = {"fast": "anthropic/claude-haiku-5-5", "standard": "anthropic/claude-sonnet-5-5",
                "deep": "anthropic/claude-opus-5-5"}
#: How hard Rika thinks before answering, as in Claude's own apps.
THINKING = ("off", "low", "medium", "high", "max")
#: The levels an OpenRouter model that accepts ``reasoning`` is offered.
OPENROUTER_THINKING = ("off", "low", "medium", "high")
#: The output limit of a thinking request: its thinking is spent from the same tokens.
THINKING_MAX_TOKENS = 16_000
#: An omp ``--model`` selector: ``provider/id`` (OpenRouter's ``~vendor/x`` aliases too), a bare
#: name or a role (``@smol``), never an option.
_MODEL = re.compile(r"[A-Za-z0-9@][A-Za-z0-9._:/@+~-]{0,199}")
#: An OpenRouter model id as Settings → AI lists it: printable, no spaces.
_OPENROUTER_MODEL = re.compile(r"[!-~]{1,200}")
#: How long one ``omp models --json`` answer is reused, and how long it may take.
OMP_CATALOG_TTL = 60
OMP_CATALOG_TIMEOUT = 20
#: (omp's path, when it was read, the catalog) of the last ``omp models --json``.
_omp_cache: tuple[str, float, list[dict]] | None = None


@dataclass(frozen=True)
class Brain:
    provider: str
    models: dict[str, str] = field(default_factory=lambda: dict.fromkeys(TIERS, ""))
    thinking: str = "off"
    model: str = ""


def _path(settings):
    return settings.root / ".private" / "rika" / "brain.json"


def _checked(provider, models, thinking, model) -> Brain:
    if provider not in PROVIDERS:
        raise ValueError("Rika's brain is openrouter or omp")
    if not isinstance(models, dict) or set(models) - set(TIERS):
        raise ValueError("omp models are given per tier: fast, standard, deep")
    if thinking not in THINKING:
        raise ValueError("Thinking is off, low, medium, high or max")
    chosen = dict.fromkeys(TIERS, "")
    for tier, tier_model in models.items():
        if not isinstance(tier_model, str) or (tier_model and not _MODEL.fullmatch(tier_model)):
            raise ValueError(f"The {tier} model must be an omp model like anthropic/claude-sonnet-5-5")
        chosen[tier] = tier_model
    pattern = _MODEL if provider == "omp" else _OPENROUTER_MODEL
    if not isinstance(model, str) or (model and not pattern.fullmatch(model)):
        raise ValueError("Rika's model is Auto or one of the models listed for her brain")
    return Brain(provider, chosen, thinking, model)


def load(settings) -> Brain:
    """The saved choice, or OpenRouter when there is none (or it cannot be read)."""
    try:
        with open(_path(settings), encoding="utf-8") as handle:
            saved = json.load(handle)
        return _checked(saved.get("provider"), saved.get("models", {}), saved.get("thinking", "off"),
                        saved.get("model", ""))
    except (OSError, ValueError, AttributeError, TypeError):
        return Brain("openrouter")


def save(settings, provider, models, *, thinking: str | None = None, model: str | None = None, conn=None) -> Brain:
    """Check and keep the choice owner-only; ValueError (nothing saved) when it is not valid.
    Without `thinking` the saved level stays; without `model` the pinned model stays while
    the provider does. A pinned OpenRouter model is checked against Settings → AI's list
    on `conn`."""
    from kitty.core.terminal import _owner_only_directory, _write_private

    saved = load(settings)
    if model is None:
        model = saved.model if saved.provider == provider else ""
    chosen = _checked(provider, models, saved.thinking if thinking is None else thinking, model)
    kept = (saved.provider, saved.model) == (chosen.provider, chosen.model)
    if chosen.provider == "openrouter" and chosen.model and (conn is not None or not kept):
        if conn is None or conn.execute("SELECT 1 FROM ai_models WHERE id=?", (chosen.model,)).fetchone() is None:
            raise ValueError(f"{chosen.model} is not in Settings → AI's model list; refresh it or choose another model")
    folder = _path(settings).parent
    _owner_only_directory(folder.parent)
    _owner_only_directory(folder)
    _write_private(_path(settings), json.dumps({"provider": chosen.provider, "models": chosen.models, "model": chosen.model,
                                                "thinking": chosen.thinking}, sort_keys=True) + "\n")
    return chosen


def models(chosen: Brain) -> dict[str, str]:
    """omp's model for each tier: the chosen one, else the default."""
    return {tier: chosen.models.get(tier) or OMP_DEFAULTS[tier] for tier in TIERS}


def tier_models(app, conn) -> dict[str, str]:
    """The model each tier runs on with this computer's brain (Auto)."""
    chosen = load(app.settings)
    return models(chosen) if chosen.provider == "omp" else router.models(conn)


def catalog(app, conn) -> list[dict]:
    """The models this computer's brain can use, as ``{"id", "label", "thinking"}``: omp's
    ``omp models --json`` (reused for :data:`OMP_CATALOG_TTL` seconds; empty when omp is
    missing or fails) or Settings → AI's OpenRouter list. ``thinking`` is ``off`` and the
    levels of :data:`THINKING` the model accepts."""
    if load(app.settings).provider == "omp":
        return [dict(entry, thinking=list(entry["thinking"])) for entry in _omp_catalog()]
    rows = conn.execute("SELECT m.id, m.name, r.id IS NOT NULL AS reasoning FROM ai_models m "
                        "LEFT JOIN ai_model_reasoning r ON r.id = m.id ORDER BY m.name, m.id").fetchall()
    return [{"id": row["id"], "label": row["name"],
             "thinking": list(OPENROUTER_THINKING if row["reasoning"] else ("off",))} for row in rows]


def auto_thinking(app, conn, found: list[dict]) -> list[str]:
    """The levels every tier model accepts (Auto's choices), or all of :data:`THINKING`
    when a tier model is not in `found` (the brain's :func:`catalog`)."""
    levels = {entry["id"]: entry["thinking"] for entry in found}
    tiers = tier_models(app, conn).values()
    if not all(model in levels for model in tiers):
        return list(THINKING)
    return [level for level in THINKING if all(level in levels[model] for model in tiers)]


def _omp_catalog() -> list[dict]:
    global _omp_cache
    program = omp.program()
    if program is None:
        return []
    now = time.monotonic()
    if _omp_cache is not None and _omp_cache[0] == program and now - _omp_cache[1] < OMP_CATALOG_TTL:
        return _omp_cache[2]
    found = _read_omp_catalog(program)
    _omp_cache = (program, now, found)
    return found


def _read_omp_catalog(program: str) -> list[dict]:
    try:
        finished = subprocess.run([program, "models", "--json"], capture_output=True, text=True,
                                  timeout=OMP_CATALOG_TIMEOUT, check=False)
        listed = json.loads(finished.stdout) if finished.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    entries = listed.get("models") if isinstance(listed, dict) else None
    if not isinstance(entries, list):
        return []
    found = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        selector, name, levels = entry.get("selector"), entry.get("name"), entry.get("thinking")
        if not isinstance(selector, str) or not _MODEL.fullmatch(selector):
            continue
        supported = levels if entry.get("reasoning") is True and isinstance(levels, list) else []
        found.append({"id": selector, "label": name if isinstance(name, str) and name.strip() else selector,
                      "thinking": ["off", *(level for level in THINKING[1:] if level in supported)]})
    return found
