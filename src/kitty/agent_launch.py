"""kitty-agent-launch: confine this process, trade the launch nonce for a token, exec the agent.

An agent tab's PATH resolves ``omp``, ``claude`` and ``codex`` to small shims that run
this module (``python -I -m kitty.agent_launch --kind KIND -- ARGS``, or the desktop
sidecar's ``agent-launch``). It needs ``KITTY_LAUNCH_NONCE`` and ``KITTY_LAUNCH_ORIGIN``
from the tab's environment (kitty.core.agent_sessions), then:

1. asks rika! for the launch spec (``POST /api/ingress/agent/launch``);
2. applies Landlock and seccomp to itself (kitty.core.confine), at the highest
   Landlock ABI the kernel offers;
3. exchanges the nonce (``POST /api/ingress/agent/exchange``) with its pid and report;
   rika! checks the process from outside and answers with the token, or with no token
   and the guarantee that applies;
4. closes every inherited descriptor but the terminal's, sets ``KITTY_AGENT_TOKEN``
   (``KITTY_AGENT_ORIGIN``, ``KITTY_AGENT_SESSION``, ``KITTY_AGENT_NAME``,
   ``KITTY_AGENT_GUARANTEE``) and execs the agent, found on PATH without the shims.

Why Python and not a small C or Rust helper: the launcher runs once per tab and then
execs, so its start-up time is all it costs; the deny-set expansion it applies is the
same code rika! uses to describe the launch (one implementation to test); it ships in
the existing wheel and desktop sidecar with no new toolchain; and every system call it
needs (``landlock_*``, ``prctl``) is reachable through ctypes. Nothing of Python remains
after the exec, and the confinement it applied cannot be lifted by the agent.

It refuses to start the agent when there is no nonce, the nonce was used, rika! is not
reachable, rika! could not verify the launch, or confinement failed and the policy's
``launch.unconfined`` is ``refuse``. Exit status 126 means "not started".
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import sys
from urllib.parse import urlsplit

from kitty.core import agent_sessions, confine

NOT_STARTED = 126
TIMEOUT = 15


class LaunchFailed(RuntimeError):
    pass


def _post(origin: str, path: str, body: dict, connection: http.client.HTTPConnection | None = None) -> dict:
    parts = urlsplit(origin)
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port:
        raise LaunchFailed("KITTY_LAUNCH_ORIGIN is not rika!'s loopback address")
    own = connection is None
    connection = connection or http.client.HTTPConnection("127.0.0.1", parts.port, timeout=TIMEOUT)
    try:
        payload = json.dumps(body).encode()
        connection.request("POST", path, body=payload, headers={"Content-Type": "application/json",
                                                               "Host": f"127.0.0.1:{parts.port}"})
        response = connection.getresponse()
        data = json.loads(response.read() or b"{}")
    except (OSError, ValueError) as exc:
        raise LaunchFailed(f"rika! did not answer at {origin}: {exc}") from None
    finally:
        if own:
            connection.close()
    if response.status != 200:
        raise LaunchFailed(str(data.get("error") or f"rika! answered {response.status}"))
    return data


def _program(name: str, shims: str | None) -> str:
    """The real agent on PATH, skipping the folder of rika!'s shims."""
    path = os.pathsep.join(part for part in os.environ.get("PATH", os.defpath).split(os.pathsep)
                           if part and (shims is None or os.path.realpath(part) != os.path.realpath(shims)))
    found = shutil.which(name, path=path) if os.sep not in name else name
    if not found:
        raise LaunchFailed(f"{name} is not installed (not found on PATH)")
    os.environ["PATH"] = path
    return found


def _say(text: str) -> None:
    print(f"rika! · {text}", file=sys.stderr, flush=True)


def launch(kind: str, arguments: list[str], *, program: str | None = None) -> int:
    nonce = os.environ.pop("KITTY_LAUNCH_NONCE", "")
    origin = os.environ.pop("KITTY_LAUNCH_ORIGIN", "")
    shims = os.environ.pop("KITTY_LAUNCH_SHIMS", None)
    if not nonce or not origin:
        _say(f"{kind} was not started by rika! (no launch nonce), so it was not started here. "
             "Start a new Agent tab from rika!.")
        return NOT_STARTED
    try:
        target = _program(program or kind, shims)
        spec = _post(origin, "/api/ingress/agent/launch", {"nonce": nonce})
        plan = agent_sessions.compile_plan(spec)
        try:
            report = confine.apply(plan).as_dict()
        except (confine.ConfineError, OSError) as exc:
            report = confine.Report(abi=confine.abi(), error=str(exc)).as_dict()
        report["missing"] = list(plan.missing)[:50]
        # One connection: rika! checks that this pid holds it.
        parts = urlsplit(origin)
        connection = http.client.HTTPConnection("127.0.0.1", parts.port, timeout=TIMEOUT)
        try:
            result = _post(origin, "/api/ingress/agent/exchange", {"nonce": nonce, "pid": os.getpid(), "report": report},
                           connection)
        finally:
            connection.close()
    except (LaunchFailed, agent_sessions.LaunchError) as exc:
        _say(f"{kind} was not started: {exc}")
        return NOT_STARTED
    _say(result.get("message") or result.get("guarantee", ""))
    if not result.get("run"):
        return NOT_STARTED
    env = dict(os.environ)
    env.update({key: str(value) for key, value in (result.get("env") or {}).items()})
    if "KITTY_AGENT_TOKEN" in env:
        env["KITTY_AGENT_ORIGIN"] = origin
    os.closerange(3, 65536)
    os.execve(target, [target, *arguments], env)
    return NOT_STARTED  # not reached


def main(argv: list[str] | None = None) -> None:
    if not getattr(sys, "frozen", False) and not sys.flags.isolated:
        # rika! accepts the exchange only from `python -I -m kitty.agent_launch`
        # (kitty.core.agent_sessions.launcher_command): no PYTHON* variables, no user site.
        os.execv(sys.executable, [sys.executable, "-I", "-m", "kitty.agent_launch",
                                  *(sys.argv[1:] if argv is None else argv)])
    parser = argparse.ArgumentParser(prog="kitty-agent-launch", description=__doc__.split("\n\n")[0])
    parser.add_argument("--kind", required=True, help="omp, claude, codex or another agent's name (for the session)")
    parser.add_argument("--program", help="Run this executable instead of the one named --kind on PATH.")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="The agent's own arguments, after --.")
    args = parser.parse_args(argv)
    rest = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    sys.exit(launch(args.kind, rest, program=args.program))


if __name__ == "__main__":
    main()
