"""`kitty schedule print`: the config that runs discovery (and optionally triage) on a timer.

The tool never installs anything itself. It prints a block I paste into my
home-manager config (NixOS), or a systemd user unit pair / crontab line for any
other Linux. Discovery is polite and cached, so once or twice a day is plenty.

With `with_triage`, each run also applies finished triage batches and sends the
new jobs as a batch. That spends money without asking, so it is opt-in, and every
batch still stops at max_cost_usd_per_run in config/triage.yaml.

Every run ends with `kitty today --notify`: the digest goes to my phone when
.env sets up ntfy or Telegram, and is just printed to the log otherwise.
"""

from __future__ import annotations

import shutil
from pathlib import Path

#: Default run times: early morning and late afternoon (local time).
DEFAULT_TIMES = ("07:00", "17:00")


def commands(*, with_triage: bool) -> list[str]:
    """The commands each scheduled run executes, in order."""
    uv = shutil.which("uv") or "uv"
    steps = [f"{uv} run kitty discover"]
    if with_triage:
        steps += [f"{uv} run kitty triage --collect", f"{uv} run kitty triage --batch --yes"]
    steps.append(f"{uv} run kitty today --notify")
    return steps


def home_manager(root: Path, times: tuple[str, ...] = DEFAULT_TIMES, *, with_triage: bool = False) -> str:
    """A home-manager block: a oneshot user service plus a timer."""
    calendars = " ".join(f'"*-*-* {time}:00"' for time in times)
    starts = " ".join(f'"{step}"' for step in commands(with_triage=with_triage))
    return f"""# Paste into your home-manager config, then `home-manager switch`.
systemd.user.services.kitty-discover = {{
  Unit.Description = "kitty: find new postings";
  Service = {{
    Type = "oneshot";
    WorkingDirectory = "{root}";
    ExecStart = [ {starts} ];
  }};
}};
systemd.user.timers.kitty-discover = {{
  Unit.Description = "kitty: find new postings on a schedule";
  Timer = {{
    OnCalendar = [ {calendars} ];
    Persistent = true;          # run at login if the machine was off at the time
    RandomizedDelaySec = "10m";
  }};
  Install.WantedBy = [ "timers.target" ];
}};
"""


def systemd_units(root: Path, times: tuple[str, ...] = DEFAULT_TIMES, *, with_triage: bool = False) -> str:
    """Plain systemd user units for ~/.config/systemd/user/."""
    calendars = "\n".join(f"OnCalendar=*-*-* {time}:00" for time in times)
    starts = "\n".join(f"ExecStart={step}" for step in commands(with_triage=with_triage))
    return f"""# ~/.config/systemd/user/kitty-discover.service
[Unit]
Description=kitty: find new postings

[Service]
Type=oneshot
WorkingDirectory={root}
{starts}

# ~/.config/systemd/user/kitty-discover.timer
[Unit]
Description=kitty: find new postings on a schedule

[Timer]
{calendars}
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target

# then: systemctl --user daemon-reload && systemctl --user enable --now kitty-discover.timer
"""


def crontab(root: Path, times: tuple[str, ...] = DEFAULT_TIMES, *, with_triage: bool = False) -> str:
    """One crontab line per run time; every step's output goes to data/discover.log."""
    chain = " && ".join(commands(with_triage=with_triage))
    lines = []
    for time in times:
        hour, minute = time.split(":")
        lines.append(f"{int(minute)} {int(hour)} * * * cd {root} && ({chain}) >> data/discover.log 2>&1")
    return "\n".join(lines) + "\n"
