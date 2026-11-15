"""Settings → Desktop: GET /api/desktop, POST /api/desktop/command, and system
notifications: POST /api/desktop/notify and /api/desktop/notify/close.

The page never gains native permissions. The desktop sidecar forwards one of a
fixed set of commands to the shell that started it over the private owner pipe
(stdout); the shell reports its keep-running preference back on stdin. A plain
``kitty-web`` website has no owner and answers ``{"available": false}``.

``autostart-on``/``autostart-off`` set **Start at login**: a systemd user unit that opens
rika! hidden in the tray, so reminders arrive with the window closed.

``restart`` saves, stops the backend and reopens rika!, so a saved module selection
applies (Settings → Modules' Restart now; the first run does it by itself).

``notify`` asks the shell to show one system notification (``actions`` adds Snooze and
Done buttons) (it sends it to the desktop's
notification server over D-Bus) and ``notify/close`` to take some down. A click comes
back to the page as a ``kitty:notice`` event; the page never gains a notification
permission of its own.

Two commands carry an argument and are sent only by the sidecar itself, never on the
page's request: ``{"command": "open-skill", "path"}`` opens one agent skill's SKILL.md
with the desktop opener (kitty.web_agent_skills sends it, for a path it listed itself; the
shell accepts only an absolute path to an existing file named SKILL.md), and
``{"command": "allow-frame", "port"}`` lets the shell's navigation guard keep one external
module's loopback server in its frame (desktop/backend.py sends one per framed module right
after the readiness line; rika-server relays it; kitty.web_external_modules).
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable

#: The only lines that ever reach the desktop shell, each as ``{"command": name}``.
COMMANDS = ("check-updates", "new-quick-note", "keep-running-on", "keep-running-off", "autostart-on", "autostart-off", "quit", "restart")
#: The commands that flip a preference the shell keeps: the state field and the value each asks for.
SWITCHES = {"keep-running-on": ("keep_running", True), "keep-running-off": ("keep_running", False),
            "autostart-on": ("autostart", True), "autostart-off": ("autostart", False)}
UNAVAILABLE = {"keep_running": "Keep running on close needs a system tray on this desktop",
               "autostart": "Start at login needs a system tray and a systemd session on this desktop"}
STATE_KEYS = frozenset({"keep_running", "keep_running_available", "autostart", "autostart_available"})
NOTICE_KEYS = frozenset({"key", "title", "body", "actions"})
MAX_CLOSED = 50


def _notice_key(value) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 200 and value.isprintable()


def _plain(value, limit: int) -> bool:
    """Text of at most ``limit`` characters with no control character but a line break."""
    return isinstance(value, str) and len(value) <= limit and all(char == "\n" or char.isprintable() for char in value)


class DesktopOwner:
    """The desktop shell on the other end of the sidecar's owner pipe."""

    def __init__(self, send: Callable[[str], None], *, timeout: float = 5.0):
        self._send = send
        self._timeout = timeout
        self._condition = threading.Condition()
        self._state: dict[str, bool | None] = {"keep_running": None, "keep_running_available": False,
                                               "autostart": None, "autostart_available": False}
        self._reports = 0

    def report(self, line: str) -> bool:
        """Adopt the shell's report of its four preference fields (`STATE_KEYS`); ignore anything else."""
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
            field, wanted = SWITCHES.get(command, (None, None))
            if field is not None and not self._state[f"{field}_available"]:
                raise APIError(409, UNAVAILABLE[field])
            seen = self._reports
            self._send(json.dumps({"command": command}))
            if field is None:
                return {"ok": True}
            if not self._condition.wait_for(lambda: self._reports > seen, self._timeout):
                raise APIError(504, "The desktop app did not confirm the change; try again")
            if self._state[field] is not wanted:
                raise APIError(409, "The desktop app could not save this preference")
            return {"available": True, **self._state}

    def notify(self, notice: dict) -> dict:
        """Show one system notification; a notice with the same ``key`` replaces the last."""
        from kitty.web_server import APIError

        title, body = notice.get("title"), notice.get("body")
        if (set(notice) != NOTICE_KEYS or not _notice_key(notice["key"]) or type(notice["actions"]) is not bool
                or not _plain(title, 120) or not title.strip() or "\n" in title or not _plain(body, 400)):
            raise APIError(400, "Expected {key, title, body, actions}: a title of up to 120 characters and a body of up to 400")
        self._send(json.dumps({"command": "notify", "key": notice["key"], "title": title, "body": body, "actions": notice["actions"]}))
        return {"ok": True}

    def close_notices(self, keys) -> dict:
        from kitty.web_server import APIError

        if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_CLOSED or not all(_notice_key(key) for key in keys):
            raise APIError(400, f"Expected {{keys}}: 1 to {MAX_CLOSED} notification keys")
        self._send(json.dumps({"command": "notify-close", "keys": keys}))
        return {"ok": True}

    def open_file(self, path: str) -> None:
        """Ask the shell to open one listed SKILL.md (kitty.web_agent_skills checked it)."""
        self._send(json.dumps({"command": "open-skill", "path": path}))

    def allow_frame(self, port: int) -> None:
        """Tell the shell an external module's server on ``127.0.0.1:<port>`` may load in a frame."""
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("a module frame port is an unprivileged TCP port")
        self._send(json.dumps({"command": "allow-frame", "port": port}))


def get(app, conn, path, query):
    if path != "/api/desktop":
        return None
    app._no_query(query)
    return app.desktop.state() if app.desktop else {"available": False}


def post(app, conn, path, body):
    from kitty.web_server import APIError

    if path not in {"/api/desktop/command", "/api/desktop/notify", "/api/desktop/notify/close"}:
        return None
    if path != "/api/desktop/command":
        if app.desktop is None:
            raise APIError(409, "System notifications from rika! are only available in the desktop app")
        if path == "/api/desktop/notify":
            return app.desktop.notify(body)
        if set(body) != {"keys"}:
            raise APIError(400, "Expected {keys}")
        return app.desktop.close_notices(body["keys"])
    if set(body) != {"command"} or not isinstance(body["command"], str):
        raise APIError(400, "Expected {\"command\": one of the desktop commands}")
    if app.desktop is None:
        raise APIError(409, "Desktop controls are only available in the rika! desktop app")
    return app.desktop.command(body["command"])
