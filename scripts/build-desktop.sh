#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
prepare_only=false
if [ "${1:-}" = --prepare-only ]; then
  prepare_only=true
  shift
fi
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
# No source checkout, Git, npm or system Node is required by installed Study.
uv run --locked python desktop/package_learnharness.py
uv run --locked --with pyinstaller==6.16.0 python desktop/package_backend.py
# The Terminal module's runtime: the pinned herdr release, checksum-verified.
uv run --locked python desktop/fetch_herdr.py
# CI delegates the native build/sign/upload to the official Tauri action.
if [ "$prepare_only" = true ]; then
  exit 0
fi
# Use the frontend lockfile's CLI rather than installing another build tool.
# APPIMAGE_EXTRACT_AND_RUN avoids requiring FUSE while assembling the AppImage.
export APPIMAGE_EXTRACT_AND_RUN=1
# linuxdeploy's rpath rewrite crashes the static-pie herdr sidecar; see the wrapper.
export PATCHELF="$ROOT/desktop/appimage_patchelf.py"
cd desktop
bundles="${KITTY_BUNDLES:-appimage,deb}"
if [ -e /etc/NIXOS ] && [ -z "${KITTY_BUNDLES:-}" ]; then
  # Tauri's AppImage step copies GTK from the single directory pkg-config reports;
  # Nix spreads it across many store paths, so NixOS builds the .deb only.
  bundles=deb
fi
"$ROOT/web/node_modules/.bin/tauri" build --bundles "$bundles" "$@" -- --locked
