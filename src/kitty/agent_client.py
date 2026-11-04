"""The agent tab's side of the mediated block API: `kitty mcp` and `kitty notes` as HTTP clients.

Inside a confined Agent tab (kitty.agent_launch) rika!'s database and private files are
closed, and the tab's environment carries ``KITTY_AGENT_TOKEN`` and
``KITTY_AGENT_ORIGIN``. With both set, the MCP notes tools and ``kitty notes`` call
``POST /api/ingress/agent/call`` instead of opening the workspace: the same tools
(kitty.core.agent_access.TOOLS), mediated and journaled by rika!.
"""
from __future__ import annotations

import http.client
import json
import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

TIMEOUT = 60
CALL = "/api/ingress/agent/call"
SESSION = "/api/ingress/agent/session"


class AgentCallError(RuntimeError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class AgentClient:
    def __init__(self, origin: str, token: str):
        parts = urlsplit(origin)
        if parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port:
            raise ValueError("KITTY_AGENT_ORIGIN is not rika!'s loopback address")
        self.port = parts.port
        self.token = token

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AgentClient | None:
        """The tab's client when ``KITTY_AGENT_TOKEN`` is set (``KITTY_AGENT_ORIGIN`` is then required)."""
        env = os.environ if environ is None else environ
        token = env.get("KITTY_AGENT_TOKEN", "").strip()
        if not token:
            return None
        origin = env.get("KITTY_AGENT_ORIGIN", "").strip()
        if not origin:
            raise ValueError("KITTY_AGENT_TOKEN is set but KITTY_AGENT_ORIGIN is not")
        return cls(origin, token)

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=TIMEOUT)
        headers = {"Authorization": f"Bearer {self.token}", "Host": f"127.0.0.1:{self.port}"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        except OSError as exc:
            raise AgentCallError(f"rika! is not reachable at 127.0.0.1:{self.port} ({exc})", 503) from None
        finally:
            connection.close()
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            raise AgentCallError("rika! answered with something that is not JSON", 502) from None
        if response.status != 200:
            message = data.get("error") if isinstance(data, dict) else None
            raise AgentCallError(message or f"rika! answered {response.status}", response.status)
        return data

    def session(self) -> dict:
        """This token's session: ``{session, name, kind, scopes, guarantee, modules, tools}``."""
        return self._request("GET", SESSION)

    def call(self, tool: str, arguments: Mapping[str, Any]) -> dict:
        return self._request("POST", CALL, {"tool": tool, "arguments": {key: value for key, value in arguments.items()
                                                                        if value is not None}})
