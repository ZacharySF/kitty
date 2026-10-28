"""Launcher uses a separate persistent store and drops inherited provider secrets."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "learnharness.sh"


def test_run_binds_loopback_and_isolates_secrets_and_persistent_paths(tmp_path):
    checkout = tmp_path / "Projects" / "learnharness"
    checkout.mkdir(parents=True)
    (checkout / "package.json").write_text('{"name":"learnharness"}')
    (checkout / "node_modules").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text('''#!/bin/sh
printf '%s\\n' "$@" "key=${OPENROUTER_API_KEY-unset}" "anthropic=${ANTHROPIC_API_KEY-unset}" "kitty=${KITTY_HOME-unset}" "data=$LH_DATA_DIR" "auth=$LH_AUTH_FILE" "home=$HOME"
''')
    npm.chmod(0o755)
    env = {**os.environ, "HOME": str(tmp_path), "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "OPENROUTER_API_KEY": "synthetic-never-forward", "ANTHROPIC_API_KEY": "synthetic-never-forward",
           "KITTY_HOME": "/synthetic/private-kitty"}
    result = subprocess.run(["bash", str(SCRIPT), "run"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "run", "dev", "--", "--hostname", "127.0.0.1", "--port", "3210",
        "key=unset", "anthropic=unset", "kitty=unset",
        f"data={checkout}/data/kitty-extension", f"auth={checkout}/data/kitty-extension/auth/auth.json",
        f"home={checkout}/data/kitty-extension/home",
    ]
    assert (checkout / "data" / "kitty-extension").stat().st_mode & 0o777 == 0o700
    assert "stop" in subprocess.run(["bash", str(SCRIPT), "invalid"], env=env,
                                    capture_output=True, text=True).stderr


def test_setup_refuses_unknown_upstream_before_installing(tmp_path):
    checkout = tmp_path / "Projects" / "learnharness"
    checkout.mkdir(parents=True)
    (checkout / "package.json").write_text('{"name":"learnharness"}')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text("#!/bin/sh\nprintf 'synthetic-unreviewed-revision\\n'\n")
    git.chmod(0o755)
    npm = bin_dir / "npm"
    npm.write_text("#!/bin/sh\nprintf 'npm-must-not-run\\n'\n")
    npm.chmod(0o755)
    env = {**os.environ, "HOME": str(tmp_path), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run(["bash", str(SCRIPT), "setup"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "reviewed upstream revision" in result.stderr
    assert "npm-must-not-run" not in result.stdout
