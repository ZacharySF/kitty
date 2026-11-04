"""The per-launch UI secret: only rika!'s own windows reach ``/api``.

The Host, Origin, ``X-Kitty-Request`` and JSON rules (``RequestHandler._security``) stop
web pages, not local programs: any program can send those headers. Every ``/api`` route
except the paired-client ingress (``/api/ingress/*``, its own bearer tokens) therefore
also requires this server's secret, a fresh 256-bit value made at start-up and kept only
in memory. It is never logged, never written to ``.private/endpoint.json`` and never put
in a URL.

How a window gets it:

- **Desktop:** the sidecar sends it to the Tauri shell in its readiness line on the
  private owner pipe; the shell injects it into every rika! window (main window and
  quick notes) with an initialisation script that runs only on the main frame and only
  on the server's exact origin (``desktop/src/owner.rs``).
- **Browser** (``kitty-web``): the terminal shows a link with a one-time ``code``
  (as Jupyter's token link); the page trades it for the secret at ``SESSION_PATH``.
  A code lives ``CODE_TTL`` seconds and works once; a new link is printed once the
  current one is used or expires.

The page sends the secret as the ``HEADER`` request header, and on WebSocket upgrades
(which cannot carry custom headers) as the subprotocol ``PROTOCOL_PREFIX + secret``
next to ``PROTOCOL``, which the server echoes. A header and not a cookie: browsers send
a 127.0.0.1 cookie to every port on 127.0.0.1 (cookies ignore ports), so a cookie would
reach the LearnHarness frame's server and any other local server the browser visits
(``web/tests/uiSecret.browser.test.mjs`` shows it).

Navigations that cannot carry a header (a PDF opened in a new window, a download link)
use a link ticket from ``LINK_PATH``: single-use, ``LINK_TTL`` seconds, valid for one
exact path and query, accepted only on GET.
"""
from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections.abc import Callable

HEADER = "X-Kitty-Secret"
#: The WebSocket subprotocol the server selects when the secret travels as a subprotocol.
PROTOCOL = "kitty"
PROTOCOL_PREFIX = "kitty-secret."
#: POST {"code"} -> {"secret"}: the browser-mode exchange; needs the code, not the secret.
SESSION_PATH = "/api/session"
#: POST {"path"} -> {"url"}: a single-use link for one GET navigation.
LINK_PATH = "/api/session/link"
TICKET = "ticket"
CODE_TTL = 600.0
LINK_TTL = 60.0
MAX_LINKS = 64
#: The one message for every refusal: it says nothing about which part was wrong.
REFUSAL = "rika! did not recognise this window; open rika! or the link kitty-web printed"


class UiAuth:
    """The secret, the outstanding one-time code and the link tickets of one server."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self.secret = secrets.token_hex(32)
        self._clock = clock
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._code: tuple[str, float] | None = None
        self._links: dict[str, tuple[str, float]] = {}

    def __repr__(self) -> str:
        return "UiAuth(<secret>)"

    def valid(self, value: str | None) -> bool:
        """Constant-time check of a presented secret."""
        if not isinstance(value, str) or not value.isascii():
            return False
        return hmac.compare_digest(value.encode(), self.secret.encode())

    def protocol_secret(self, offered: list[str]) -> bool:
        """Whether a WebSocket upgrade's subprotocols are exactly ``PROTOCOL`` plus the secret."""
        names = [name.strip() for value in offered for name in value.split(",") if name.strip()]
        carried = [name[len(PROTOCOL_PREFIX):] for name in names if name.startswith(PROTOCOL_PREFIX)]
        return (len(names) == 2 and names.count(PROTOCOL) == 1 and len(carried) == 1
                and self.valid(carried[0]))

    # One-time codes (browser mode).

    def issue_code(self) -> str:
        """A fresh code; it replaces any code still outstanding."""
        code = secrets.token_hex(16)
        with self._changed:
            self._code = (code, self._clock() + CODE_TTL)
            self._changed.notify_all()
        return code

    def redeem(self, code: object) -> str | None:
        """The secret for the outstanding code, once; None for anything else."""
        if not isinstance(code, str) or not code.isascii():
            return None
        with self._changed:
            current = self._code
            if current is None or not hmac.compare_digest(code.encode(), current[0].encode()):
                return None
            self._code = None
            self._changed.notify_all()
            if current[1] <= self._clock():
                return None
        return self.secret

    def wait_code(self, code: str, stop: threading.Event) -> None:
        """Block until `code` is used, replaced or expired, or `stop` is set."""
        with self._changed:
            while not stop.is_set() and self._code is not None and self._code[0] == code:
                remaining = self._code[1] - self._clock()
                if remaining <= 0:
                    self._code = None
                    return
                self._changed.wait(min(remaining, 0.5))

    # Link tickets.

    def issue_link(self, target: str) -> str:
        ticket = secrets.token_hex(32)
        now = self._clock()
        with self._lock:
            self._links = {key: value for key, value in self._links.items() if value[1] > now}
            if len(self._links) >= MAX_LINKS:
                del self._links[min(self._links, key=lambda key: self._links[key][1])]
            self._links[ticket] = (target, now + LINK_TTL)
        return ticket

    def follow(self, ticket: str, target: str) -> bool:
        """Whether `ticket` was issued for exactly `target`; it is spent either way."""
        with self._lock:
            entry = self._links.pop(ticket, None) if isinstance(ticket, str) else None
        return (entry is not None and entry[1] > self._clock()
                and hmac.compare_digest(entry[0].encode(), target.encode()))
