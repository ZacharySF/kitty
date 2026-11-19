"""One chat completion through omp's sign-in: ``omp auth-gateway stdio``.

The gateway takes an OpenAI Chat Completions request as one JSON line and answers with
the same shape, using the credentials, models and settings of the omp installed here
(a Claude subscription, for example), so Rika needs no OpenRouter key. Each request
starts its own gateway process (about a third of a second), sends one line, closes
stdin and reads the answer; the process exits once it has answered. The frames are
documented in omp's ``auth-broker-gateway.md`` (section *stdio*)::

    → {"id": "rika", "path": "/v1/chat/completions", "body": {...}}
    ← {"ready": true, "version": "18.14.1"}
    ← {"id": "rika", "status": 200, "body": {"object": "chat.completion", ...}}

Nothing is retried: omp has already run its own fallbacks when it answers an error.
"""
from __future__ import annotations

import json
import shutil
import subprocess

TIMEOUT = 120
PATH = "/v1/chat/completions"
_ID = "rika"
#: The longest part of omp's own error text passed on to the user.
REASON_MAX = 300


class OmpMissing(RuntimeError):
    """omp is not on PATH; nothing was sent."""


class OmpFailed(RuntimeError):
    """The gateway answered an error, timed out or broke the protocol; it may have been billed."""


def program() -> str | None:
    """The omp on PATH, or None."""
    return shutil.which("omp")


def complete(body: dict, *, timeout: float = TIMEOUT) -> dict:
    """The gateway's ``chat.completion`` for `body`; OmpMissing or OmpFailed otherwise."""
    found = program()
    if found is None:
        raise OmpMissing("omp is not installed (not found on PATH)")
    line = json.dumps({"id": _ID, "path": PATH, "body": body}, ensure_ascii=False) + "\n"
    try:
        finished = subprocess.run([found, "auth-gateway", "stdio"], input=line, capture_output=True, text=True,
                                  timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise OmpFailed(f"omp did not answer within {timeout:g} seconds") from None
    except OSError as exc:
        raise OmpFailed(f"omp could not be started: {exc.strerror or exc}") from None
    for raw in finished.stdout.splitlines():
        try:
            frame = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(frame, dict) or frame.get("id") != _ID:
            continue
        answer = frame.get("body")
        if frame.get("status") != 200 or not isinstance(answer, dict):
            raise OmpFailed(_reason(answer, frame.get("status")))
        return answer
    raise OmpFailed(f"omp's gateway stopped without answering (exit code {finished.returncode})")


def _reason(answer, status) -> str:
    error = answer.get("error") if isinstance(answer, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str) or not message.strip():
        return f"omp answered status {status}"
    return message.strip()[:REASON_MAX]
