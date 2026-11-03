"""Settings → Desktop: GET /api/desktop, POST /api/desktop/command.

The page never gains native permissions. The desktop sidecar forwards one of a
fixed set of commands to the shell that started it over the private owner pipe
(stdout); the shell reports its keep-running preference back on stdin. A plain
``kitty-web`` website has no owner and answers ``{"available": false}``.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable

#: The only lines that ever reach the desktop shell, each as ``{"command": name}``.
COMMANDS = ("check-updates", "new-quick-note", "keep-running-on", "keep-running-off", "quit")
KEEP_RUNNING = {"keep-running-on": True, "keep-running-off": False}
STATE_KEYS = frozenset({"keep_running", "keep_running_available"})


class DesktopOwner:
    """The desktop shell on the other end of the sidecar's owner pipe."""

    def __init__(self, send: Callable[[str], None], *, timeout: float = 5.0):
        self._send = send
        self._timeout = timeout
        self._condition = threading.Condition()
        self._state: dict[str, bool | None] = {"keep_running": None, "keep_running_available": False}
        self._reports = 0

    def report(self, line: str) -> bool:
        """Adopt a ``{"keep_running", "keep_running_available"}`` report; ignore anything else."""
        try:
            data = json.loads(line)
        except ValueError:
            return False
        if not isinstance(data, dict) or set(data) != STATE_KEYS or any(type(value) is not bool for value in data.values()):
            return False
        with self._condition:
            self._state = data
            self._reports += 1
            self._condition.notify_all()
        return True

    def state(self) -> dict:
        with self._condition:
            return {"available": True, **self._state}

    def command(self, command: str) -> dict:
        from kitty.web_server import APIError

        if command not in COMMANDS:
            raise APIError(400, "Unknown desktop command")
        with self._condition:
            wanted = KEEP_RUNNING.get(command)
            if wanted is not None and not self._state["keep_running_available"]:
                raise APIError(409, "Keep running on close needs a system tray on this desktop")
            seen = self._reports
            self._send(json.dumps({"command": command}))
            if wanted is None:
                return {"ok": True}
            if not self._condition.wait_for(lambda: self._reports > seen, self._timeout):
                raise APIError(504, "The desktop app did not confirm the change; try again")
            if self._state["keep_running"] is not wanted:
                raise APIError(409, "The desktop app could not save this preference")
            return {"available": True, **self._state}


def get(app, conn, path, query):
    if path != "/api/desktop":
        return None
    app._no_query(query)
    return app.desktop.state() if app.desktop else {"available": False}


def post(app, conn, path, body):
    from kitty.web_server import APIError

    if path != "/api/desktop/command":
        return None
    if set(body) != {"command"} or not isinstance(body["command"], str):
        raise APIError(400, "Expected {\"command\": one of the desktop commands}")
    if app.desktop is None:
        raise APIError(409, "Desktop controls are only available in the rika! desktop app")
    return app.desktop.command(body["command"])
