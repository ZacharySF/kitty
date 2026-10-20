"""A small WebDriver BiDi client: how kitty talks to its Firefox window.

WebDriver BiDi is the W3C remote-control protocol built into Firefox: JSON
commands and replies over a WebSocket on a local port. This module has only
what the form filler needs: open or reload a tab, go to a URL, list its frames,
run a JavaScript function, type, click one element, and attach a file.

Page scripts run in a private "sandbox" realm, the same isolation browser
extensions get: they see and change the page's DOM, but the page cannot see
them or anything they keep in their globals. So the filler does not stamp
attributes on the page's elements; it remembers them in the sandbox instead.

Ending a session (close()) leaves Firefox, its window and every tab open. That
is the point: I review each filled tab and click submit myself, later. A failed
close raises and retains this controller's connection for a possible retry:
disconnecting alone would leave Firefox's direct BiDi session orphaned.
"""

from __future__ import annotations

import itertools
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from websockets.exceptions import WebSocketException
from websockets.sync.client import ClientConnection, connect

from kitty.core.apply.credentials import redact

# Wire-level DEBUG output includes script arguments (and therefore passwords).
# An unregistered, disabled logger cannot inherit application/root debug handlers.
_TRANSPORT_LOGGER = logging.Logger("kitty.bidi.transport")
_TRANSPORT_LOGGER.disabled = True

#: Name of the private script realm the filler's JavaScript runs in.
SANDBOX = "kitty"

#: Pointer and keyboard ids for input actions (any stable names work).
_MOUSE = "kitty-mouse"
_KEYBOARD = "kitty-keyboard"


class BidiError(RuntimeError):
    """Firefox refused a command, a page script threw, or the connection broke."""


class TabClosed(BidiError):
    """The tab (browsing context) a command named no longer exists."""


class ConnectionLost(BidiError):
    """The WebSocket to Firefox broke: Firefox quit or the connection was dropped."""


class SessionBusy(BidiError):
    """Firefox already has a remote-control session; it allows only one at a time.

    Firefox keeps a direct BiDi session until someone sends session.end:
    closing the connection does not end it. Reconnection endpoints exist only
    for HTTP-upgraded sessions, not the direct sessions this client creates.
    """


class SessionEndError(BidiError):
    """Cleanup was not confirmed; retain the owner and its recovery metadata."""

    def __init__(self, url: str, session_id: str, detail: str, *, acknowledged: bool) -> None:
        self.url = url
        self.session_id = session_id
        self.end_acknowledged = acknowledged
        super().__init__(
            f"Firefox did not confirm cleanup of this controller's session "
            f"{session_id} at {url}: {detail}. The owning connection was not "
            "discarded; retry close() on this controller if it is still connected. "
            "Firefox cannot reattach a disconnected direct BiDi session. Preserve "
            "open forms before manually restarting Firefox; no restart was attempted."
        )



class Bidi:
    """One WebDriver BiDi session with a running Firefox."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._session_ended = False
        self._session_end_acknowledged = False
        self._secrets: tuple[str, ...] = ()
        try:
            # legacy=True: a plain connection object, closed in close() (the session
            # spans many calls, so a `with` block does not fit)
            self._ws: ClientConnection = connect(
                url, max_size=None, open_timeout=5, legacy=True, logger=_TRANSPORT_LOGGER,
            )
        except (OSError, WebSocketException) as exc:
            raise BidiError(f"cannot reach Firefox at {url}: {exc}") from exc
        self._ids = itertools.count(1)
        try:
            # "ignore": a dialog a page opens stays up for me instead of being dismissed.
            session = self.command("session.new", {"capabilities": {"alwaysMatch": {
                "unhandledPromptBehavior": {"default": "ignore"},
            }}})
            self.session_id: str = session["sessionId"]
        except BidiError as exc:
            self._ws.close()
            if "Maximum number of active sessions" in str(exc):
                raise SessionBusy("Firefox already has a remote-control session "
                                  "(it allows one at a time)") from exc
            raise BidiError(f"Firefox would not start a remote-control session ({exc})") from exc

    def protect_secrets(self, *values: str) -> None:
        """Keep credentials local to this session's diagnostic redactor."""
        self._secrets = tuple(dict.fromkeys((*self._secrets, *values)))

    def redact(self, text: str) -> str:
        """Sanitize page/transport diagnostics before displaying or persisting them."""
        return redact(text, self._secrets)

    @property
    def session_end_acknowledged(self) -> bool:
        """Whether this controller received session.end's reply, before teardown."""
        return self._session_end_acknowledged

    @property
    def session_ended(self) -> bool:
        """Whether Firefox confirmed release, not merely receipt of session.end."""
        return self._session_ended

    def command(self, method: str, params: dict[str, Any], *, timeout: float = 30.0) -> dict:
        """Send one command and wait for its reply. Events and late replies are skipped."""
        return self._command(self._ws, method, params, timeout=timeout)

    def _command(self, ws: ClientConnection, method: str, params: dict[str, Any],
                 *, timeout: float) -> dict:
        command_id = next(self._ids)
        try:
            ws.send(json.dumps({"id": command_id, "method": method, "params": params}))
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BidiError(f"{method} timed out after {timeout:.0f}s")
                try:
                    message = json.loads(ws.recv(timeout=remaining))
                except TimeoutError:
                    raise BidiError(f"{method} timed out after {timeout:.0f}s") from None
                if message.get("id") != command_id:
                    continue
                if message.get("type") == "error":
                    error_class = TabClosed if message.get("error") == "no such frame" else BidiError
                    raise error_class(self.redact(
                        f"{method}: {message.get('error')}: {message.get('message')}"
                    )) from None
                return message.get("result", {})
        except (OSError, WebSocketException) as exc:
            raise ConnectionLost(self.redact(
                f"lost the connection to Firefox during {method}: {exc}"
            )) from None

    def close(self) -> None:
        """Release remote control without closing tabs; raise if cleanup is unconfirmed.

        Firefox replies before deleting its session, so a successful reply alone
        is insufficient. The sessionless status probe never creates or ends a
        session. If another controller acquires one meanwhile, cleanup remains
        conservatively unconfirmed; that controller is never disturbed.
        """
        if self._session_ended:
            return
        try:
            if self._session_end_acknowledged:
                try:
                    self.command("session.end", {}, timeout=5)
                except ConnectionLost:
                    # A previous end reply was received, but its status probe
                    # failed. Recheck release even if the owner socket is now gone.
                    pass
            else:
                self.command("session.end", {}, timeout=5)
                self._session_end_acknowledged = True
            with connect(
                self.url, max_size=None, open_timeout=5, legacy=True,
                logger=_TRANSPORT_LOGGER,
            ) as probe:
                status = self._command(probe, "session.status", {}, timeout=5)
            if not status.get("ready"):
                raise BidiError(
                    "Firefox still reports an active session (this session or "
                    "a newly connected controller)"
                )
        except (BidiError, OSError, WebSocketException) as exc:
            # Do not manufacture an orphan by discarding our only attached socket.
            raise SessionEndError(
                self.url, self.session_id, self.redact(str(exc)),
                acknowledged=self._session_end_acknowledged,
            ) from exc
        self._session_ended = True
        self._ws.close()


def open_session(port: int) -> Bidi:
    """A session with the Firefox listening on this local port."""
    return Bidi(f"ws://127.0.0.1:{port}/session")


# ------------------------------------------------------------------ frames


@dataclass(frozen=True)
class Frame:
    """One document: a tab's top page or an iframe inside it."""

    bidi: Bidi
    context: str        # BiDi browsing-context id
    url: str

    def call(self, function: str, *args: Any, timeout: float = 30.0, page_realm: bool = False) -> Any:
        """Run a JavaScript function with JSON-able arguments and return its JSON-able result.

        The function may be async. It runs in the private sandbox unless
        `page_realm` is set (tests use that to read the page's own globals).
        """
        wrapper = (
            "async (argsJson) => JSON.stringify("
            f"(await ({function})(...JSON.parse(argsJson))) ?? null)"
        )
        result = self._call_function(wrapper, json.dumps(list(args)), timeout, page_realm)
        return json.loads(result.get("value", "null"))

    def node(self, function: str, *args: Any) -> str | None:
        """Run a JavaScript function that returns an element; return its BiDi reference.

        The reference (a "sharedId") is what clicks and file uploads target.
        """
        wrapper = f"(argsJson) => ({function})(...JSON.parse(argsJson))"
        result = self._call_function(wrapper, json.dumps(list(args)), 30.0, False)
        return result.get("sharedId") if result.get("type") == "node" else None

    def _call_function(self, declaration: str, args_json: str, timeout: float,
                       page_realm: bool) -> dict:
        target: dict[str, Any] = {"context": self.context}
        if not page_realm:
            target["sandbox"] = SANDBOX
        reply = self.bidi.command("script.callFunction", {
            "functionDeclaration": declaration,
            "arguments": [{"type": "string", "value": args_json}],
            "target": target,
            "awaitPromise": True,
            "resultOwnership": "none",
        }, timeout=timeout + 5)
        if reply.get("type") == "exception":
            details = reply.get("exceptionDetails", {})
            raise BidiError(self.bidi.redact(
                f"page script failed: {details.get('text', 'unknown error')}"
            )) from None
        return reply.get("result", {})

    def click(self, shared_id: str) -> None:
        """A real mouse click at the centre of one element (scrolled into view first)."""
        inspected = self.bidi.command("script.callFunction", {
            "functionDeclaration": """element => {
              if (!element.isConnected) throw new Error('The requested control is detached');
              element.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
              const r = element.getClientRects()[0];
              if (!r?.width || !r.height) throw new Error('The requested control has no visible bounds');
              const x = Math.floor((Math.max(0, r.left) + Math.min(innerWidth, r.right)) / 2);
              const y = Math.floor((Math.max(0, r.top) + Math.min(innerHeight, r.bottom)) / 2);
              let hit = document.elementFromPoint(x, y);
              while (hit?.shadowRoot) {
                const inner = hit.shadowRoot.elementFromPoint(x, y);
                if (!inner || inner === hit) break;
                hit = inner;
              }
              for (let n = hit; n; n = n.assignedSlot || n.parentElement || n.getRootNode()?.host)
                if (n === element) return true;
              throw new Error('Another element covers the requested control');
            }""",
            "arguments": [{"sharedId": shared_id}],
            "target": {"context": self.context, "sandbox": SANDBOX},
            "awaitPromise": True,
            "resultOwnership": "none",
        })
        if inspected.get("type") == "exception":
            details = inspected.get("exceptionDetails", {})
            raise BidiError(self.bidi.redact(
                f"Cannot click control: {details.get('text', 'viewport inspection failed')}"
            ))
        self.bidi.command("input.performActions", {"context": self.context, "actions": [{
            "type": "pointer", "id": _MOUSE, "parameters": {"pointerType": "mouse"},
            "actions": [
                {"type": "pointerMove", "x": 0, "y": 0,
                 "origin": {"type": "element", "element": {"sharedId": shared_id}}},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerUp", "button": 0},
            ],
        }]})
        self.bidi.command("input.releaseActions", {"context": self.context})

    def type(self, text: str, *, delay_ms: int = 25) -> None:
        """Type text into whatever has focus, one real key press per character.

        Callers pass text without newlines, so this never presses Enter.
        """
        keys: list[dict[str, Any]] = []
        for char in text:
            keys += [{"type": "keyDown", "value": char}, {"type": "keyUp", "value": char},
                     {"type": "pause", "duration": delay_ms}]
        self.bidi.command("input.performActions", {"context": self.context, "actions": [{
            "type": "key", "id": _KEYBOARD, "actions": keys,
        }]}, timeout=30 + len(text) * delay_ms / 1000)
        self.bidi.command("input.releaseActions", {"context": self.context})

    def set_files(self, shared_id: str, path: Path) -> None:
        """Attach a file to a file input (fires its input and change events)."""
        self.bidi.command("input.setFiles", {
            "context": self.context, "element": {"sharedId": shared_id}, "files": [str(path)],
        })


# ------------------------------------------------------------------ tabs


@dataclass(frozen=True)
class Tab:
    """One browser tab."""

    bidi: Bidi
    context: str

    def goto(self, url: str, *, timeout: float = 45.0) -> None:
        """Navigate and wait until the document is parsed (not for every image)."""
        self.bidi.command("browsingContext.navigate",
                          {"context": self.context, "url": url, "wait": "interactive"},
                          timeout=timeout)

    def reload(self, *, timeout: float = 45.0) -> None:
        """Normally refresh this tab in place, waiting until its new document is parsed.

        Uses Firefox's normal cache and reload behavior, including any page
        dialogs; it neither creates a replacement tab nor navigates other tabs.
        """
        self.bidi.command("browsingContext.reload",
                          {"context": self.context, "wait": "interactive"},
                          timeout=timeout)

    def frames(self) -> list[Frame]:
        """The top document first, then every iframe in it (depth first)."""
        tree = self.bidi.command("browsingContext.getTree", {"root": self.context})
        frames: list[Frame] = []

        def walk(nodes: list[dict]) -> None:
            for node in nodes:
                frames.append(Frame(self.bidi, node["context"], node.get("url", "")))
                walk(node.get("children") or [])

        walk(tree.get("contexts", []))
        return frames

    @property
    def main(self) -> Frame:
        return self.frames()[0]

    def activate(self) -> None:
        """Bring this tab to the front of its window."""
        self.bidi.command("browsingContext.activate", {"context": self.context})

    def close(self) -> None:
        self.bidi.command("browsingContext.close", {"context": self.context})


def open_tab(bidi: Bidi, *, background: bool = False) -> Tab:
    """A new tab in Firefox's window: in front, unless `background` (autofill runs
    while I work in another window, so its tabs must not take the screen)."""
    reply = bidi.command("browsingContext.create", {"type": "tab", "background": background})
    return Tab(bidi, reply["context"])
