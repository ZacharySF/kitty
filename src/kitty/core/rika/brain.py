"""Which service Rika thinks with on this computer: OpenRouter (Settings → AI's key,
catalog, prices and daily budget) or omp (the omp coding agent's own sign-in, reached
through ``omp auth-gateway stdio``; see :mod:`kitty.core.rika.omp`), and how hard omp
thinks.

The choice belongs to this computer, not to the build: omp has to be installed and
signed in here. It is kept owner-only in ``.private/rika/brain.json`` as
``{"provider": "openrouter"|"omp", "models": {tier: model}, "thinking": level}``; a
missing or unreadable file is OpenRouter, rika!'s behaviour before the choice existed.
An empty tier model is :data:`OMP_DEFAULTS`' model for that tier. OpenRouter's tier
models stay in Settings → AI (:mod:`kitty.core.rika.router`).

The thinking level (:data:`THINKING`, chosen beside Rika's field) rides on each omp
request as ``reasoning_effort``; ``off`` sends none. omp maps a level a model lacks to
the nearest one it has.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from kitty.core.rika.router import TIERS

PROVIDERS = ("openrouter", "omp")
#: omp's model for each tier when none is chosen: Claude, which a Claude subscription covers.
OMP_DEFAULTS = {"fast": "anthropic/claude-haiku-5-5", "standard": "anthropic/claude-sonnet-5-5",
                "deep": "anthropic/claude-opus-5-5"}
#: How hard omp thinks before answering, as in Claude's own apps.
THINKING = ("off", "low", "medium", "high", "max")
#: The output limit of a thinking request: its thinking is spent from the same tokens.
THINKING_MAX_TOKENS = 16_000
#: An omp ``--model`` selector: ``provider/id``, a bare name or a role (``@smol``), never an option.
_MODEL = re.compile(r"[A-Za-z0-9@][A-Za-z0-9._:/@+-]{0,199}")


@dataclass(frozen=True)
class Brain:
    provider: str
    models: dict[str, str] = field(default_factory=lambda: dict.fromkeys(TIERS, ""))
    thinking: str = "off"


def _path(settings):
    return settings.root / ".private" / "rika" / "brain.json"


def _checked(provider, models, thinking) -> Brain:
    if provider not in PROVIDERS:
        raise ValueError("Rika's brain is openrouter or omp")
    if not isinstance(models, dict) or set(models) - set(TIERS):
        raise ValueError("omp models are given per tier: fast, standard, deep")
    if thinking not in THINKING:
        raise ValueError("Thinking is off, low, medium, high or max")
    chosen = dict.fromkeys(TIERS, "")
    for tier, model in models.items():
        if not isinstance(model, str) or (model and not _MODEL.fullmatch(model)):
            raise ValueError(f"The {tier} model must be an omp model like anthropic/claude-sonnet-5-5")
        chosen[tier] = model
    return Brain(provider, chosen, thinking)


def load(settings) -> Brain:
    """The saved choice, or OpenRouter when there is none (or it cannot be read)."""
    try:
        with open(_path(settings), encoding="utf-8") as handle:
            saved = json.load(handle)
        return _checked(saved.get("provider"), saved.get("models", {}), saved.get("thinking", "off"))
    except (OSError, ValueError, AttributeError, TypeError):
        return Brain("openrouter")


def save(settings, provider, models, *, thinking: str | None = None) -> Brain:
    """Check and keep the choice owner-only; ValueError (nothing saved) when it is not valid.
    Without `thinking` the saved level stays."""
    from kitty.core.terminal import _owner_only_directory, _write_private

    chosen = _checked(provider, models, load(settings).thinking if thinking is None else thinking)
    folder = _path(settings).parent
    _owner_only_directory(folder.parent)
    _owner_only_directory(folder)
    _write_private(_path(settings), json.dumps({"provider": chosen.provider, "models": chosen.models,
                                                "thinking": chosen.thinking}, sort_keys=True) + "\n")
    return chosen


def models(chosen: Brain) -> dict[str, str]:
    """omp's model for each tier: the chosen one, else the default."""
    return {tier: chosen.models.get(tier) or OMP_DEFAULTS[tier] for tier in TIERS}
