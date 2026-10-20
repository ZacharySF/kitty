"""The Firefox window the filler works in.

The window has to outlive kitty: I review each filled form and click submit
myself, possibly long after the command (or TUI worker) has finished. So
kitty starts system Firefox as a detached process with its own profile
under data/firefox and remote control (WebDriver BiDi) on a local port, then
attaches to it. Ending the session leaves the window and every tab open. If
that Firefox is already running (from an earlier `kitty apply`) it is reused.

Detached means more than a new process group: a desktop session puts every
process a terminal starts into that terminal's systemd scope and kills the
whole scope when the terminal window closes. So when a systemd user session is
available, Firefox is started in a scope of its own with `systemd-run --user
--scope`. The window's app id is "kitty", so it is easy to tell apart from my
everyday Firefox in the window list.

The profile is separate from my everyday Firefox on purpose: remote control
has no password, so while it is on any program on this computer could drive
the window. It should hold job-site logins and nothing else.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from kitty.core.apply.bidi import Bidi, BidiError, SessionBusy, open_session
from kitty.settings import Settings

#: Preferences written to the filler profile's user.js on every start (it is
#: kitty's profile, so these always win). Each one removes something that
#: would get in the filler's way.
PREFS: dict[str, bool | int | str] = {
    # no first-run tabs, default-browser or data-policy prompts in front of the forms
    "browser.shell.checkDefaultBrowser": False,
    "browser.aboutwelcome.enabled": False,
    "browser.startup.homepage_override.mstone": "ignore",
    "datareporting.policy.dataSubmissionPolicyBypassNotification": True,
    # Firefox's own form-history and address suggestions would cover the fields being filled
    "browser.formfill.enable": False,
    "extensions.formautofill.addresses.enabled": False,
    "extensions.formautofill.creditCards.enabled": False,
    # forms are filled while the window may sit behind other windows; do not slow its timers
    "dom.timeout.enable_budget_timer_throttling": False,
    # lets hide_automation restart remote control in Firefox's non-automation mode
    "remote.experimental.dynamicstart.enabled": True,
}


class BrowserError(RuntimeError):
    """Firefox could not be found or started."""


#: Window class / Wayland app id of the filler window.
APP_ID = "kitty"


def firefox_command(firefox: str, profile_dir: Path, port: int, *, headless: bool = False) -> list[str]:
    """The command line that starts a filler Firefox."""
    command = [
        firefox,
        "--profile", str(profile_dir),
        # its own instance: never hands off to (or takes over) my everyday Firefox
        "--no-remote",
        "--name", APP_ID,
        f"--remote-debugging-port={port}",
        # chrome-scope scripts, needed by hide_automation (see its docstring for the cost)
        "--remote-allow-system-access",
    ]
    if headless:
        command.append("--headless")
    return command


def own_scope(command: list[str]) -> list[str]:
    """`command` wrapped to run in its own systemd user scope, when systemd is there.

    Without this, closing the terminal that ran kitty closes the filler window too.
    """
    systemd_run = shutil.which("systemd-run")
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not systemd_run or not runtime_dir or not (Path(runtime_dir) / "bus").exists():
        return command
    return [systemd_run, "--user", "--scope", "--quiet", "--collect", *command]


def write_prefs(profile_dir: Path) -> None:
    """Write PREFS to the profile's user.js, which Firefox reads at every start."""
    lines = [f'user_pref("{name}", {_pref_value(value)});' for name, value in PREFS.items()]
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "user.js").write_text(
        "// Written by kitty at every start of its Firefox (core/apply/browser.py).\n"
        + "\n".join(lines) + "\n"
    )


def _pref_value(value: bool | int | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return f'"{value}"'


def is_listening(port: int) -> bool:
    """True when something accepts connections on this local port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _client_socket_inodes(port: int) -> set[str]:
    """Socket inodes of open TCP connections to this local port (from Linux /proc/net)."""
    inodes: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            rows = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            parts = row.split()           # sl local remote state ... uid timeout inode
            established = len(parts) > 9 and parts[3] == "01"
            if established and int(parts[2].rsplit(":", 1)[1], 16) == port:
                inodes.add(f"socket:[{parts[9]}]")
    return inodes


def session_holders(port: int) -> list[str]:
    """Other programs connected to Firefox's remote-control port, as "pid 123: command".

    Linux only (reads /proc); elsewhere, or when nothing is connected, the list is
    empty. Used to tell "another kitty is still running" from "a dead one left
    its session behind" when Firefox refuses a new session.
    """
    inodes = _client_socket_inodes(port)
    holders: list[str] = []
    if not inodes:
        return holders
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            links = {os.readlink(fd) for fd in (proc / "fd").iterdir()}
            command = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue                      # gone, or another user's process
        if links & inodes:
            holders.append(f"pid {proc.name}: {' '.join(command.split())[:120]}")
    return holders


def busy_message(port: int) -> str:
    """What to tell me when Firefox refuses a session because one already exists."""
    holders = session_holders(port)
    if holders:
        return (
            "Another program is remote-controlling the kitty Firefox (one at a time). "
            "If it is kitty watching tabs, stop it (Ctrl-C in its terminal, w on the TUI "
            "Apply tab, or quit that TUI). Connected: " + "; ".join(holders)
        )
    return (
        "The kitty Firefox still holds a remote-control session from an kitty that "
        "ended without letting go; Firefox keeps it until it restarts. Close the kitty "
        "Firefox window (finish or note any open forms first), then run this again."
    )


def _start(command: list[str]) -> subprocess.Popen:
    """Detach host Firefox without desktop/PyInstaller library overlays.

    Even LD_LIBRARY_PATH_ORIG can contain an incompatible Nix GTK/glibc runtime.
    Firefox's own launcher supplies its dependencies; only its child environment
    is cleaned, since the application still needs its bundled libraries.
    """
    environment = os.environ.copy()
    environment.pop("LD_LIBRARY_PATH", None)
    environment.pop("LD_LIBRARY_PATH_ORIG", None)
    try:
        return subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, env=environment,
        )
    except OSError as exc:
        raise BrowserError(f"Firefox could not start ({exc.strerror}); check the Firefox installation and desktop session.") from None


def _wait_for_port(port: int, wait_seconds: float, process: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if is_listening(port):
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(0.3)
    return False


def ensure_firefox(settings: Settings, *, wait_seconds: float = 30.0) -> int:
    """Start kitty's Firefox unless it is already up; return its remote-control port."""
    port = settings.browser_port
    if is_listening(port):
        return port
    if not settings.firefox_path:
        raise BrowserError("Firefox not found. Install it or set KITTY_FIREFOX to the binary.")
    if sys.platform == "linux" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise BrowserError("Firefox requires an active desktop display. Launch kitty from your desktop session.")
    write_prefs(settings.browser_profile_dir)
    process = _start(own_scope(firefox_command(settings.firefox_path, settings.browser_profile_dir, port)))
    if not _wait_for_port(port, wait_seconds, process):
        if process.poll() is not None:
            raise BrowserError(f"Firefox could not start (exit code {process.returncode}); check Firefox and the desktop display.")
        raise BrowserError(
            f"Firefox did not open its remote-control port {port} within {wait_seconds:.0f}s "
            "(is another program using it? set KITTY_BROWSER_PORT)."
        )
    return port


#: Chrome-scope script: is remote control running in automation mode? Wrapped in a
#: function because the browser window already has a global named RemoteAgent.
_IS_AUTOMATION = """(() => {
  const { RemoteAgent } = ChromeUtils.importESModule(
    "chrome://remote/content/components/RemoteAgent.sys.mjs");
  return RemoteAgent.isBrowserAutomationRunning;
})()"""

#: Chrome-scope script: restart remote control in non-automation mode. It runs after a
#: short delay because stopping ends the very connection that sent it.
_RESTART_AS_ASSISTANT = """(() => {
  const { RemoteAgent } = ChromeUtils.importESModule(
    "chrome://remote/content/components/RemoteAgent.sys.mjs");
  setTimeout(async () => {
    await RemoteAgent.stopAtRuntime();
    await RemoteAgent.startAtRuntime({ isBrowserAutomation: false });
  }, 300);
})()"""


def _chrome_eval(bidi: Bidi, expression: str) -> object:
    """Evaluate `expression` in Firefox's own (chrome) scope; return the plain value."""
    tree = bidi.command("browsingContext.getTree", {"moz:scope": "chrome"})
    reply = bidi.command("script.evaluate", {
        "expression": expression,
        "target": {"context": tree["contexts"][0]["context"]},
        "awaitPromise": False,
    })
    if reply.get("type") == "exception":
        raise BidiError(f"chrome script failed: {reply['exceptionDetails'].get('text')}")
    return reply.get("result", {}).get("value")


def automation_flag_shown(port: int) -> bool:
    """True when pages in this Firefox see navigator.webdriver = true."""
    bidi = open_session(port)
    try:
        return bool(_chrome_eval(bidi, _IS_AUTOMATION))
    finally:
        bidi.close()


def hide_automation(port: int, *, wait_seconds: float = 15.0) -> bool:
    """Switch remote control to non-automation mode; True when pages no longer see it.

    Firefox started with --remote-debugging-port tells every page
    navigator.webdriver = true, which bot checks such as reCAPTCHA read. Firefox
    also has a mode for an assistant driving an already running browser that
    leaves the flag false; restarting remote control in that mode (same port)
    hides it. The cost: chrome-scope scripts need --remote-allow-system-access,
    so any program on this computer that reaches the port can run code inside
    this Firefox, not only drive its tabs. The port listens on 127.0.0.1 only.
    """
    try:
        if not automation_flag_shown(port):
            return True
        bidi = open_session(port)
        try:
            _chrome_eval(bidi, _RESTART_AS_ASSISTANT)
        finally:
            bidi.close()
    except SessionBusy:
        raise                             # another session holds Firefox: the caller explains
    except BidiError:
        return False
    deadline = time.monotonic() + wait_seconds
    time.sleep(1.0)
    while time.monotonic() < deadline:
        try:
            return not automation_flag_shown(port)
        except BidiError:
            time.sleep(0.5)
    return False


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def headless_firefox(settings: Settings, *, profile_dir: Path | None = None) -> Iterator[Bidi]:
    """An invisible Firefox: a throwaway empty profile (tests and smoke runs), or
    `profile_dir`, a separate profile kept between runs (hiring.cafe discovery)."""
    if not settings.firefox_path:
        raise BrowserError("Firefox not found. Install it or set KITTY_FIREFOX to the binary.")
    profile = profile_dir or Path(tempfile.mkdtemp(prefix="kitty-firefox-"))
    port = _free_port()
    write_prefs(profile)
    process = _start(firefox_command(settings.firefox_path, profile, port, headless=True))
    try:
        if not _wait_for_port(port, 30.0, process):
            raise BrowserError("headless Firefox did not start within 30s")
        bidi = open_session(port)
        try:
            yield bidi
        finally:
            bidi.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        if profile_dir is None:
            shutil.rmtree(profile, ignore_errors=True)
