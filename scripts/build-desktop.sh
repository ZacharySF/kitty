#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
for tool in cargo rustc pkg-config uv node npm patchelf; do
  command -v "$tool" >/dev/null || { printf 'Missing build prerequisite: %s\n' "$tool" >&2; exit 1; }
done
pkg-config --exists gtk+-3.0 webkit2gtk-4.1 javascriptcoregtk-4.1 libsoup-3.0 librsvg-2.0
if ! pkg-config --exists ayatana-appindicator3-0.1 && ! pkg-config --exists appindicator3-0.1; then
  printf 'Install libayatana-appindicator3-dev (or libappindicator3-dev) for the native tray.\n' >&2
  exit 1
fi
# The root lockfile pins the full backend dependency graph; PyInstaller's version
# is explicit and temporary so the application dependency set stays unchanged.
npm --prefix web ci
npm --prefix web run build
uv run --locked --with pyinstaller==6.16.0 python desktop/package_backend.py
# @tauri-apps/cli is a web devDependency owned by the integration parent.
# APPIMAGE_EXTRACT_AND_RUN avoids requiring FUSE while assembling the AppImage.
export APPIMAGE_EXTRACT_AND_RUN=1
cd desktop
"$ROOT/web/node_modules/.bin/tauri" build --bundles appimage,deb "$@" -- --locked
