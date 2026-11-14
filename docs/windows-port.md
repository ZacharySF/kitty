# Windows port

rika! ships for Linux (x86_64). There is no Windows download yet. This page says what a
Windows build needs, what is done, and how to see the rest on a real Windows machine.

## Done

- **rika-server** no longer names Unix-only APIs outside `server/src/private.rs`: file
  modes, `O_NOFOLLOW`, `umask` and `SIGTERM` go through that module, which does the Unix
  thing on Unix and the Windows equivalent (or nothing, where Windows has no such notion)
  on Windows. The build-file lock is a `.lock` file inside `config/` on Windows, because
  Windows cannot open a folder as a file; `kitty.core.builds.store.locked` takes the same
  lock there. It type-checks for `x86_64-pc-windows-gnu` (`cargo check --target`, from
  Linux, C dependencies included); it has not been run on Windows.
- **Windows check** (`.github/workflows/windows-check.yml`, run by hand from the Actions
  tab): builds and tests rika-server, imports and tests the Python base, and compiles the
  desktop shell on `windows-latest`. The last two are expected to fail until the work
  below is done; their logs are the list of what to fix. It publishes nothing.

## Left to do

1. **Python backend.** These refuse to import or run on Windows today:
   - `kitty.core.endpoint`, `kitty.core.keys`: `os.getuid`, `O_DIRECTORY`, `O_NOFOLLOW`,
     `fchmod` (owner checks on `.private/`). Windows needs an ACL check or none.
   - `kitty.core.terminal`, `kitty.core.tern`, `kitty.core.tern_plugin`: pseudo-terminals,
     Unix sockets, process groups. The Terminal module is Linux-only until herdr and Tern
     exist for Windows; it has to be hidden from the module list there.
   - `kitty.core.resume_render`, `kitty.core.apply.browser`: process groups.
   - `kitty.web_server`: stops on `SIGTERM`; the front must end the worker another way.
2. **Desktop shell** (`desktop/src`): gate `notifications.rs` (D-Bus), `autostart.rs`
   (systemd), `appimage_update.rs` and the GTK calls behind `cfg(target_os = "linux")`;
   send notifications through Windows toasts and start at login through the Run key;
   find the backend beside the executable instead of under `libexec/`.
3. **Packaging**: build the backend with PyInstaller on Windows, add the `nsis` bundle
   target and an `.ico` icon to `desktop/tauri.conf.json`, sign the installer, and add a
   Windows job to `desktop-release.yml` that uploads it and its updater entry.
4. **Packs**: the herdr and LearnHarness packs carry Linux programs; publish Windows
   ones or mark those modules unavailable on Windows.
