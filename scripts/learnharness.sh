#!/usr/bin/env bash
# External AGPL app; only the reviewed installation lockfile is patched.
set -euo pipefail
umask 077
checkout="$HOME/Projects/learnharness"
runtime="$checkout/data/kitty-extension"
script="$(realpath "${BASH_SOURCE[0]}")"
revision="3dc88472eea41eed0d5e5f29c2f16f8d063c415b"
dependency_patch="$(dirname "$script")/learnharness-dependencies.patch"

case "${1:-}" in
  setup)
    if [[ ! -d "$checkout" ]]; then
      mkdir -p "$(dirname "$checkout")"
      git clone --no-checkout https://github.com/amosblomqvist/learnharness "$checkout"
      git -C "$checkout" checkout --detach "$revision"
    fi
    if [[ ! -f "$checkout/package.json" ]]; then
      echo "LearnHarness checkout missing at $checkout" >&2
      exit 1
    fi
    if [[ "$(git -C "$checkout" rev-parse HEAD)" != "$revision" ]]; then
      echo "Expected reviewed upstream revision $revision; leave this checkout unchanged." >&2
      exit 1
    fi
    # Reproducible npm audit fix (without --force), not a moving registry repair.
    # Refuse conflicting local edits rather than replacing an existing lockfile.
    if ! git -C "$checkout" apply --reverse --check "$dependency_patch" 2>/dev/null; then
      git -C "$checkout" apply --check "$dependency_patch"
      git -C "$checkout" apply "$dependency_patch"
    fi
    mkdir -p "$runtime/home" "$runtime/auth"
    cd "$checkout"
    # Official upstream npm install, without Kitty's environment or npm secrets.
    env -i HOME="$runtime/home" PATH="$PATH" LANG=C.UTF-8 npm install
    ;;
  run)
    if [[ ! -d "$checkout/node_modules" ]]; then
      echo "Run $script setup first (requires Git, Node.js and npm)." >&2
      exit 1
    fi
    mkdir -p "$runtime/home" "$runtime/auth"
    cd "$checkout"
    exec env -i HOME="$runtime/home" PATH="$PATH" LANG=C.UTF-8 \
      LH_DATA_DIR="$runtime" LH_AUTH_FILE="$runtime/auth/auth.json" \
      NEXT_TELEMETRY_DISABLED=1 \
      npm run dev -- --hostname 127.0.0.1 --port 3210
    ;;
  start)
    # Transient user unit survives the launching shell, not a system install.
    # Re-run start after a reboot; persistent learning data is never removed.
    exec systemd-run --user --unit=kitty-learnharness --collect \
      --setenv="PATH=$PATH" "$script" run
    ;;
  stop)
    exec systemctl --user stop kitty-learnharness.service
    ;;
  status)
    exec systemctl --user status kitty-learnharness.service
    ;;
  *)
    echo "Usage: $script {setup|start|stop|status|run}" >&2
    echo "start/stop use systemd --user; run stays in foreground (Ctrl+C stops)." >&2
    exit 2
    ;;
esac
