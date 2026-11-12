mod appimage_update;
mod owner;
mod update_lifecycle;
mod updates;
mod workspace_path;

use std::{
    ffi::OsString,
    fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{atomic::{AtomicBool, AtomicU64, Ordering}, mpsc, Arc},
    thread,
    time::Duration,
};
use parking_lot::Mutex;
use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem},
    tray::TrayIconBuilder,
    webview::{NewWindowResponse, PermissionKind, PermissionResponse},
    Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use url::Url;

/// rika-server stopped before its readiness line: the reason for the user and what it wrote.
#[derive(Debug)]
struct StartupFailed {
    reason: String,
    details: String,
}

impl std::fmt::Display for StartupFailed {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.reason)
    }
}

impl std::error::Error for StartupFailed {}

/// The reason to show for a front that stopped before readiness: the last line it wrote to
/// stderr (rika-server ends a start-up failure with `rika-server: <reason>`).
fn startup_failure(stderr: &str) -> String {
    stderr.lines().rev().map(str::trim).find(|line| !line.is_empty())
        .map(|line| line.strip_prefix("rika-server: ").unwrap_or(line).to_owned())
        .unwrap_or_else(|| "rika-server stopped before it was ready".into())
}

struct Backend(Option<Child>);

impl Backend {
    fn stop(&mut self) {
        if let Some(mut child) = self.0.take() {
            if let Some(mut input) = child.stdin.take() {
                let _ = input.write_all(b"quit\n");
            }
            // Drain accepted operations and persist state. Never kill Firefox, a
            // process group, or a process discovered via port/name/PID files.
            let _ = child.wait();
        }
    }
}

impl Drop for Backend {
    fn drop(&mut self) { self.stop(); }
}

struct Desktop {
    backend: Mutex<Backend>,
    background: AtomicBool,
    background_item: CheckMenuItem<tauri::Wry>,
    background_available: bool,
    update_item: MenuItem<tauri::Wry>,
    lifecycle: Mutex<update_lifecycle::Lifecycle>,
    preferences: PathBuf,
    root: PathBuf,
    origin: Url,
    /// Hands this launch's UI secret to every rika! window (`owner::window_script`).
    window_script: String,
    next_note: AtomicU64,
}

fn workspace() -> Result<PathBuf, String> {
    workspace_path::select(
        std::env::args_os().skip(1),
        std::env::var_os("KITTY_HOME"),
        std::env::var_os("XDG_DATA_HOME"),
        std::env::var_os("HOME"),
    )
}

// The optional LearnHarness extension is a fixed loopback frame (see web_learnharness.py);
// WebKitGTK reports subframe loads to the navigation handler too.
const LEARNHARNESS_PORT: u16 = 3210;

fn local_url(url: &Url, origin: &Url) -> bool {
    let port = url.port_or_known_default();
    url.scheme() == "http" && url.host_str() == Some("127.0.0.1")
        && (port == origin.port_or_known_default() || port == Some(LEARNHARNESS_PORT))
        && url.username().is_empty() && url.password().is_none()
}

fn external_url(url: &Url) -> bool {
    matches!(url.scheme(), "http" | "https") && url.host_str().is_some()
        && url.username().is_empty() && url.password().is_none()
}

fn open_external(url: &Url) {
    if external_url(url) {
        // open passes the checked URL as an argument, not shell source. There is
        // deliberately no frontend shell, file, opener, or notification command; the
        // one file the owner pipe may open is a listed SKILL.md (open_skill).
        let _ = open::that_detached(url.as_str());
    }
}

/// The agent skills view's "Open file": the owner pipe already accepted only an absolute
/// path named SKILL.md; it must still be a regular file when it opens.
fn open_skill(path: &Path) {
    if path.is_file() {
        let _ = open::that_detached(path);
    }
}

/// Quick notes are the kitty origin's own `#quick-note[/<id>]` route, nothing else.
fn quick_note_target(url: &Url, origin: &Url) -> Option<String> {
    let fragment = url.fragment()?;
    let id = fragment.strip_prefix("quick-note").filter(|rest| rest.is_empty() || rest.starts_with('/'))?;
    let id_ok = id.is_empty() || {
        let digits = &id[1..];
        (1..=16).contains(&digits.len()) && !digits.starts_with('0') && digits.bytes().all(|b| b.is_ascii_digit())
    };
    let same_origin = url.scheme() == origin.scheme() && url.host_str() == origin.host_str()
        && url.port_or_known_default() == origin.port_or_known_default()
        && url.username().is_empty() && url.password().is_none();
    (id_ok && same_origin && url.path() == "/" && url.query().is_none()).then(|| fragment.to_string())
}

fn wants_quick_note(args: &[String]) -> bool {
    args.iter().skip(1).any(|arg| arg == "--quick-note")
}

/// `--safe`: every window opens without the build's custom CSS and theme overrides
/// (docs/platform-design.md, Q9); the backend announces it to the page.
fn wants_safe_mode(args: &[String]) -> bool {
    args.iter().skip(1).any(|arg| arg == "--safe")
}

// Lets the page paint translucent surfaces only where the window really is
// transparent; browser tabs keep an opaque canvas. Runs before page scripts.
const TRANSPARENT_MARKER: &str = "document.documentElement.dataset.window = 'transparent';";

/// A small transparent, undecorated, always-on-top note window. Same private
/// webview profile and navigation guard as the main window; never IPC.
fn open_quick_note(app: &tauri::AppHandle, fragment: String) {
    let app = app.clone();
    // Window creation from event/menu callbacks must not block the main loop.
    thread::spawn(move || {
        let state = app.state::<Desktop>();
        let mut url = state.origin.clone();
        url.set_fragment(Some(&fragment));
        let label = format!("quick-note-{}", state.next_note.fetch_add(1, Ordering::SeqCst));
        let origin = state.origin.clone();
        let _ = WebviewWindowBuilder::new(&app, label, WebviewUrl::External(url))
            .title("rika! quick note").inner_size(380.0, 460.0).min_inner_size(260.0, 220.0)
            .transparent(true).decorations(false).always_on_top(true)
            .initialization_script(TRANSPARENT_MARKER)
            .initialization_script(state.window_script.as_str())
            .devtools(cfg!(debug_assertions))
            .data_directory(state.root.join(".private/webview"))
            .on_navigation(move |url| {
                if local_url(url, &origin) { true } else { open_external(url); false }
            })
            .on_new_window(|url, _features| {
                open_external(&url);
                NewWindowResponse::Deny
            }).build();
    });
}

/// Where the desktop binary's own bundle installs the backend, relative to the binary's
/// directory (`/usr/bin` in the .deb, `usr/bin` inside the AppImage): an unpacked
/// PyInstaller directory (tauri.conf.json `files`), started in place on every launch.
/// Outside `usr/lib`, so linuxdeploy never rewrites its libraries.
const BACKEND: &str = "libexec/kitty-workspace/kitty-web/kitty-web";

/// The backend executable from this binary's bundle. Tauri dev and cargo smoke binaries
/// use the staging folder's `kitty-web/` directory; installed bundles always use their
/// own copy, never a checkout or PATH.
fn backend(directory: &Path, staging: Option<&Path>) -> Option<PathBuf> {
    bundled(directory.parent()?.join(BACKEND), staging.map(|folder| folder.join("kitty-web/kitty-web")))
}

/// A bundled sidecar executable beside the desktop binary (Tauri `externalBin`), or the
/// target-suffixed staging copy.
fn sidecar(directory: &Path, name: &str, staging: Option<&Path>) -> Option<PathBuf> {
    bundled(directory.join(name), staging.map(|folder| folder.join(format!("{name}-{}", env!("KITTY_TARGET_TRIPLE")))))
}

fn bundled(packaged: PathBuf, staged: Option<PathBuf>) -> Option<PathBuf> {
    if packaged.is_file() { return Some(packaged); }
    staged.filter(|path| path.is_file())
}

/// rika-server's command line (server/src/main.rs `--desktop`): it serves the backend's
/// bundled website itself, migrates the workspace at `root` and starts `backend` behind it
/// with exactly these arguments.
fn front_arguments(backend: &Path, root: &Path, safe: bool) -> Vec<OsString> {
    // PyInstaller's onedir keeps its data beside the executable in `_internal` (sys._MEIPASS).
    let website = backend.parent().unwrap_or(Path::new("")).join("_internal/website");
    let mut arguments: Vec<OsString> = ["--desktop", "--port", "0", "--static-dir"].map(OsString::from).into();
    arguments.push(website.into());
    arguments.extend(["--root".into(), root.into()]);
    arguments.extend(["--worker-program".into(), backend.into(), "--worker-arg".into(), "--workspace".into(), "--worker-arg".into(), root.into()]);
    if safe { arguments.extend(["--worker-arg".into(), "--safe".into()]); }
    arguments
}

/// Starts rika-server, which starts the backend behind it and relays the owner pipe both
/// ways: its readiness line carries rika-server's own origin and UI secret.
fn start_backend(root: &Path, safe: bool) -> Result<(Backend, Url, String, mpsc::Receiver<owner::Command>), Box<dyn std::error::Error>> {
    let executable = std::env::current_exe()?;
    let directory = executable.parent().ok_or("Desktop executable has no directory")?;
    let staging = cfg!(debug_assertions).then(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries"));
    let binary = backend(directory, staging.as_deref())
        .ok_or("The bundled kitty-web backend is missing")?;
    let front = sidecar(directory, "rika-server", staging.as_deref())
        .ok_or("The bundled rika-server is missing")?;
    let mut command = Command::new(front);
    // The Terminal module runs only rika!'s pinned herdr, never one from the environment;
    // rika-server passes its environment on to the backend.
    command.env_remove("KITTY_HERDR");
    if let Some(herdr) = sidecar(directory, "kitty-herdr", staging.as_deref()) { command.env("KITTY_HERDR", herdr); }
    command.args(front_arguments(&binary, root, safe));
    let child = command
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()?;
    let mut backend = Backend(Some(child));
    let output = backend.0.as_mut().unwrap().stdout.take().ok_or("Backend readiness pipe unavailable")?;
    let errors = backend.0.as_mut().unwrap().stderr.take().ok_or("Backend error pipe unavailable")?;
    // What the front writes to stderr before readiness is kept for a start-up failure; after
    // readiness it is read and dropped, as /dev/null took it before.
    let written = Arc::new(Mutex::new(Some(String::new())));
    let (closed, errors_closed) = mpsc::channel::<()>();
    let keep = written.clone();
    thread::spawn(move || {
        for line in BufReader::new(errors).lines().map_while(Result::ok) {
            if let Some(text) = keep.lock().as_mut() {
                text.push_str(&line);
                text.push('\n');
                // Only the end matters (the reason is the last line): keep at most 64 KiB of it.
                if text.len() > 64 * 1024 {
                    let cut = text.len() - 32 * 1024;
                    let cut = (cut..text.len()).find(|&at| text.is_char_boundary(at)).unwrap_or(text.len());
                    text.drain(..cut);
                }
            }
        }
        let _ = closed.send(());
    });
    let (send, receive) = mpsc::sync_channel(1);
    let (commands, receiver) = mpsc::channel();
    thread::spawn(move || {
        let mut output = BufReader::new(output);
        let mut line = String::new();
        let result = output.read_line(&mut line).map(|_| line);
        let _ = send.send(result);
        // stdout remains a private, fixed-command channel after readiness.
        for line in output.lines().map_while(Result::ok) {
            if let Some(command) = owner::parse(&line) {
                if commands.send(command).is_err() { break; }
            }
        }
    });
    let line = match receive.recv_timeout(Duration::from_secs(90)) {
        Ok(result) => result?,
        Err(error) => {
            // No UI was exposed and no application worker was requested. Reap
            // only the child we spawned if startup never reaches its owner pipe.
            if let Some(mut child) = backend.0.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
            return Err(error.into());
        }
    };
    if line.is_empty() {
        // The front exited before readiness: its stderr says why.
        if let Some(mut child) = backend.0.take() { let _ = child.wait(); }
        let _ = errors_closed.recv_timeout(Duration::from_secs(2));
        let details = written.lock().take().unwrap_or_default();
        return Err(Box::new(StartupFailed { reason: startup_failure(&details), details }));
    }
    written.lock().take();
    // The readiness line also carries the UI secret; it is never logged or written anywhere.
    let (origin, secret) = owner::readiness(&line).ok_or("Invalid backend readiness message")?;
    let origin = Url::parse(&origin)?;
    if !local_url(&origin, &origin) || origin.port().is_none() || origin.path() != "/"
        || origin.query().is_some() || origin.fragment().is_some() {
        return Err("Backend did not bind a private loopback origin".into());
    }
    Ok((backend, origin, secret, receiver))
}

/// A start-up that never reached readiness: the reason in an error dialog (the app exits when
/// it is dismissed) and the front's whole stderr in `.private/startup-error.log` and on ours.
fn report_startup_failure(app: &tauri::AppHandle, root: &Path, error: &(dyn std::error::Error + 'static)) {
    let (reason, details) = match error.downcast_ref::<StartupFailed>() {
        Some(failed) => (failed.reason.clone(), failed.details.clone()),
        None => (error.to_string(), format!("{error}\n")),
    };
    let log = root.join(".private/startup-error.log");
    let logged = fs::write(&log, &details).is_ok();
    eprint!("{details}");
    let mut message = format!("rika! could not start.\n\n{reason}");
    if logged { message.push_str(&format!("\n\nThe full report is in {}.", log.display())); }
    let handle = app.clone();
    app.dialog().message(message).title("rika!").kind(MessageDialogKind::Error)
        .show(move |_| handle.exit(1));
}

fn show(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn request_quit(app: &tauri::AppHandle) {
    let state = app.state::<Desktop>();
    match state.lifecycle.lock().begin_quit() {
        update_lifecycle::QuitDecision::Stop => {},
        update_lifecycle::QuitDecision::AlreadyStopping => return,
        update_lifecycle::QuitDecision::UpdateBusy => {
            updates::busy(app);
            return;
        }
    }
    let app = app.clone();
    thread::spawn(move || {
        app.state::<Desktop>().backend.lock().stop();
        app.state::<Desktop>().lifecycle.lock().stopped();
        app.exit(0);
    });
}

fn report_desktop(app: &tauri::AppHandle) {
    let state = app.state::<Desktop>();
    let line = owner::state_line(state.background.load(Ordering::SeqCst), state.background_available);
    if let Some(input) = state.backend.lock().0.as_mut().and_then(|child| child.stdin.as_mut()) {
        let _ = input.write_all(line.as_bytes());
        let _ = input.flush();
    };
}

fn set_background(app: &tauri::AppHandle, enabled: bool) {
    let state = app.state::<Desktop>();
    if state.background_available {
        let temporary = state.preferences.with_extension("json.new");
        let bytes = if enabled { b"{\"background\":true}".as_slice() } else { b"{\"background\":false}".as_slice() };
        if fs::write(&temporary, bytes).and_then(|_| fs::rename(&temporary, &state.preferences)).is_ok() {
            state.background.store(enabled, Ordering::SeqCst);
        }
    }
    let _ = state.background_item.set_checked(state.background.load(Ordering::SeqCst));
    report_desktop(app);
}

fn desktop_command(app: &tauri::AppHandle, command: owner::Command) {
    match command {
        owner::Command::CheckUpdates => updates::check(app),
        owner::Command::NewQuickNote => open_quick_note(app, "quick-note".into()),
        owner::Command::KeepRunning(enabled) => set_background(app, enabled),
        owner::Command::Quit => request_quit(app),
        owner::Command::OpenSkill(path) => open_skill(&path),
    }
}

/// GTK's DPI setting when it has none: 96 DPI in GTK's 1/1024 units.
const NEUTRAL_XFT_DPI: i32 = 96 * 1024;

/// The DPI to set, if any. On Wayland GTK has no XSETTINGS, so `gtk-xft-dpi` stays -1 and
/// WebKitGTK derives a negative device-pixel ratio from it (a blank, compressed page on
/// fractional-scale outputs). A real value is left alone.
fn dpi_override(current: i32) -> Option<i32> {
    (current <= 0).then_some(NEUTRAL_XFT_DPI)
}

/// Lets the window run natively on Wayland at the output's GTK scale (2× on a 125% screen,
/// downsampled by the compositor), instead of being upscaled through XWayland, which blurred
/// all text and every canvas.
#[cfg(target_os = "linux")]
fn neutral_xft_dpi() {
    use gtk::prelude::GtkSettingsExt;
    if let Some(settings) = gtk::Settings::default() {
        if let Some(dpi) = dpi_override(settings.gtk_xft_dpi()) { settings.set_gtk_xft_dpi(dpi); }
    }
}

fn main() {
    // Applies to new private files only; never changes selected existing files.
    unsafe { libc::umask(0o077); }
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if wants_quick_note(&args) { open_quick_note(app, "quick-note".into()) } else { show(app) }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            #[cfg(target_os = "linux")]
            neutral_xft_dpi();
            let root = workspace().map_err(std::io::Error::other)?;
            fs::create_dir_all(root.join(".private"))?;
            let root = root.canonicalize()?;
            let preferences = root.join(".private/desktop.json");
            let background = fs::read(&preferences).ok()
                .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
                .and_then(|value| value["background"].as_bool()).unwrap_or(false);
            let safe = wants_safe_mode(&std::env::args().collect::<Vec<_>>());
            let (backend, origin, secret, commands) = match start_backend(&root, safe) {
                Ok(started) => started,
                Err(error) => {
                    report_startup_failure(app.handle(), &root, error.as_ref());
                    return Ok(());
                }
            };
            let window_script = owner::window_script(&origin, &secret);
            let show_item = MenuItem::with_id(app, "show", "Show rika!", true, None::<&str>)?;
            let note_item = MenuItem::with_id(app, "quick-note", "New quick note", true, None::<&str>)?;
            let background_item = CheckMenuItem::with_id(app, "background", "Keep running on close (monitoring / reminders)", true, background, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit rika! (save and stop workers)", true, Some("Ctrl+Shift+Q"))?;
            let update = MenuItem::with_id(app, "updates", "Check for updates…", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &note_item, &background_item, &update, &quit])?;
            let tray = TrayIconBuilder::new().tooltip("rika!").menu(&menu)
                .icon(tauri::image::Image::from_bytes(include_bytes!("../icons/icon.png"))?).build(app);
            let background_available = tray.is_ok();
            if !background_available {
                background_item.set_checked(false)?;
                background_item.set_enabled(false)?;
            }
            app.manage(Desktop {
                backend: Mutex::new(backend),
                background: AtomicBool::new(background && background_available),
                background_item, background_available,
                update_item: update,
                lifecycle: Mutex::new(update_lifecycle::Lifecycle::default()), preferences,
                root: root.clone(), origin: origin.clone(), window_script: window_script.clone(),
                next_note: AtomicU64::new(1),
            });
            report_desktop(app.handle());
            let navigation_origin = origin.clone();
            let note_origin = origin.clone();
            let note_app = app.handle().clone();
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(origin))
                .title("rika!").inner_size(1280.0, 850.0).min_inner_size(760.0, 500.0)
                .transparent(true)
                .initialization_script(TRANSPARENT_MARKER)
                .initialization_script(window_script)
                .devtools(cfg!(debug_assertions))
                .data_directory(root.join(".private/webview"))
                .on_navigation(move |url| {
                    if local_url(url, &navigation_origin) { true }
                    else { open_external(url); false }
                })
                // Rika's voice input (docs/rika-design.md Q5): WebKitGTK denies getUserMedia
                // unless the app allows it. The window only shows rika!'s own loopback origin
                // (navigation above), audio is transcribed on this computer, and the page
                // opens the microphone only while Space is held or a brain dump runs.
                .on_permission_request(|_webview, kind| match kind {
                    PermissionKind::Microphone => PermissionResponse::Allow,
                    _ => PermissionResponse::Default,
                })
                .on_new_window(move |url, _features| {
                    match quick_note_target(&url, &note_origin) {
                        Some(fragment) => open_quick_note(&note_app, fragment),
                        None => open_external(&url),
                    }
                    NewWindowResponse::Deny
                }).build()?;
            let handle = app.handle().clone();
            window.on_window_event(move |event| {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    if handle.state::<Desktop>().background.load(Ordering::SeqCst) {
                        if let Some(window) = handle.get_webview_window("main") { let _ = window.hide(); }
                    } else { request_quit(&handle); }
                }
            });
            let handle = app.handle().clone();
            thread::spawn(move || loop {
                thread::sleep(Duration::from_secs(1));
                let state = handle.state::<Desktop>();
                let mut lifecycle = state.lifecycle.lock();
                if lifecycle.can_exit() { break; }
                if !lifecycle.monitor_backend() { continue; }
                let exited = state.backend.lock().0.as_mut()
                    .and_then(|child| child.try_wait().ok()).flatten().is_some();
                if exited {
                    lifecycle.stopped();
                    handle.exit(1);
                    break;
                }
            });
            let handle = app.handle().clone();
            thread::spawn(move || {
                for command in commands {
                    desktop_command(&handle, command);
                }
            });
            if wants_quick_note(&std::env::args().collect::<Vec<_>>()) {
                open_quick_note(app.handle(), "quick-note".into());
            }
            Ok(())
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => show(app),
            "quick-note" => open_quick_note(app, "quick-note".into()),
            "quit" => request_quit(app),
            "updates" => updates::check(app),
            "background" => {
                let enabled = !app.state::<Desktop>().background.load(Ordering::SeqCst);
                set_background(app, enabled);
            }
            _ => {},
        })
        .build(tauri::generate_context!())
        .expect("Unable to start rika!: verify the packaged backend and local workspace");
    app.run(|app, event| {
        if let RunEvent::ExitRequested { api, .. } = event {
            if let Some(state) = app.try_state::<Desktop>() {
                if !state.lifecycle.lock().can_exit() {
                    api.prevent_exit();
                    request_quit(app);
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn navigation_never_grants_remote_or_scheme_access() {
        let local = Url::parse("http://127.0.0.1:43721/").unwrap();
        for bad in ["http://localhost:43721/", "http://127.0.0.1:43722/", "https://127.0.0.1:43721/", "file:///etc/passwd", "http://user@127.0.0.1:43721/", "https://example.com/"] {
            assert!(!local_url(&Url::parse(bad).unwrap(), &local));
        }
        assert!(local_url(&Url::parse("http://127.0.0.1:43721/api/jobs?q=cat").unwrap(), &local));
    }
    #[test]
    fn navigation_keeps_only_the_fixed_learnharness_frame_in_app() {
        let local = Url::parse("http://127.0.0.1:43721/").unwrap();
        assert!(local_url(&Url::parse("http://127.0.0.1:3210/chat/new").unwrap(), &local));
        for bad in ["http://localhost:3210/", "https://127.0.0.1:3210/", "http://127.0.0.1:3211/", "http://user@127.0.0.1:3210/"] {
            assert!(!local_url(&Url::parse(bad).unwrap(), &local));
        }
    }
    #[test]
    fn external_opener_rejects_non_web_and_credentials() {
        for bad in ["file:///tmp/private", "javascript:alert(1)", "mailto:test@example.com", "https://user:password@example.com/"] {
            assert!(!external_url(&Url::parse(bad).unwrap()));
        }
        assert!(external_url(&Url::parse("https://example.com/path").unwrap()));
    }
    #[test]
    fn quick_note_windows_open_only_for_the_kitty_origin_note_route() {
        let local = Url::parse("http://127.0.0.1:43721/").unwrap();
        let target = |url: &str| quick_note_target(&Url::parse(url).unwrap(), &local);
        assert_eq!(target("http://127.0.0.1:43721/#quick-note").as_deref(), Some("quick-note"));
        assert_eq!(target("http://127.0.0.1:43721/#quick-note/42").as_deref(), Some("quick-note/42"));
        for bad in ["http://127.0.0.1:3210/#quick-note", "http://127.0.0.1:43721/api/#quick-note", "http://127.0.0.1:43721/?x=1#quick-note",
                    "http://127.0.0.1:43721/#quick-note/0", "http://127.0.0.1:43721/#quick-note/4x", "http://127.0.0.1:43721/#notes",
                    "http://user@127.0.0.1:43721/#quick-note", "https://example.com/#quick-note"] {
            assert_eq!(target(bad), None, "{bad}");
        }
    }
    #[test]
    fn a_missing_gtk_dpi_becomes_neutral_and_a_real_one_is_kept() {
        // Wayland reports -1 (no XSETTINGS); WebKitGTK turns that into a negative pixel ratio.
        assert_eq!(dpi_override(-1), Some(96 * 1024));
        assert_eq!(dpi_override(0), Some(96 * 1024));
        // X11 sessions and users who set a DPI keep theirs (120 DPI here).
        assert_eq!(dpi_override(120 * 1024), None);
    }
    #[test]
    fn sidecars_come_only_from_the_bundle_or_the_debug_staging_folder() {
        let folder = std::env::temp_dir().join(format!("kitty-sidecar-test-{}", std::process::id()));
        let (bundle, staging) = (folder.join("bundle/bin"), folder.join("binaries"));
        fs::create_dir_all(&bundle).unwrap();
        fs::create_dir_all(&staging).unwrap();
        assert_eq!(sidecar(&bundle, "kitty-herdr", Some(&staging)), None);
        let staged = staging.join(format!("kitty-herdr-{}", env!("KITTY_TARGET_TRIPLE")));
        fs::write(&staged, b"staged").unwrap();
        assert_eq!(sidecar(&bundle, "kitty-herdr", Some(&staging)), Some(staged));
        assert_eq!(sidecar(&bundle, "kitty-herdr", None), None);
        fs::write(bundle.join("kitty-herdr"), b"bundled").unwrap();
        assert_eq!(sidecar(&bundle, "kitty-herdr", Some(&staging)), Some(bundle.join("kitty-herdr")));
        // rika-server, which the shell starts in front of the backend, follows the same rule.
        assert_eq!(sidecar(&bundle, "rika-server", None), None);
        let staged = staging.join(format!("rika-server-{}", env!("KITTY_TARGET_TRIPLE")));
        fs::write(&staged, b"staged").unwrap();
        assert_eq!(sidecar(&bundle, "rika-server", Some(&staging)), Some(staged));
        fs::write(bundle.join("rika-server"), b"bundled").unwrap();
        assert_eq!(sidecar(&bundle, "rika-server", Some(&staging)), Some(bundle.join("rika-server")));

        // The backend is an unpacked directory: never a file beside the binary (the old
        // onefile location) and never a directory standing in for its executable.
        fs::write(bundle.join("kitty-web"), b"old onefile").unwrap();
        fs::create_dir_all(staging.join("kitty-web")).unwrap();
        assert_eq!(backend(&bundle, Some(&staging)), None);
        let staged = staging.join("kitty-web/kitty-web");
        fs::write(&staged, b"staged").unwrap();
        assert_eq!(backend(&bundle, Some(&staging)), Some(staged));
        assert_eq!(backend(&bundle, None), None);
        let packaged = folder.join("bundle/libexec/kitty-workspace/kitty-web/kitty-web");
        fs::create_dir_all(&packaged).unwrap();
        assert_eq!(backend(&bundle, None), None);
        fs::remove_dir(&packaged).unwrap();
        fs::write(&packaged, b"bundled").unwrap();
        assert_eq!(backend(&bundle, Some(&staging)), Some(bundle.parent().unwrap().join(BACKEND)));
        fs::remove_dir_all(&folder).unwrap();
    }
    #[test]
    fn the_front_serves_the_bundled_website_and_starts_exactly_this_backend() {
        let backend = Path::new("/usr/libexec/kitty-workspace/kitty-web/kitty-web");
        let root = Path::new("/home/someone/rika workspace");
        let expected = |safe: &[&str]| {
            let mut arguments = vec!["--desktop", "--port", "0",
                "--static-dir", "/usr/libexec/kitty-workspace/kitty-web/_internal/website",
                "--root", "/home/someone/rika workspace",
                "--worker-program", "/usr/libexec/kitty-workspace/kitty-web/kitty-web",
                "--worker-arg", "--workspace", "--worker-arg", "/home/someone/rika workspace"];
            arguments.extend_from_slice(safe);
            arguments.into_iter().map(OsString::from).collect::<Vec<_>>()
        };
        assert_eq!(front_arguments(backend, root, false), expected(&[]));
        assert_eq!(front_arguments(backend, root, true), expected(&["--worker-arg", "--safe"]));
    }
    #[test]
    fn a_start_up_failure_shows_the_fronts_last_line() {
        let stderr = "Traceback ...\nUnable to open the local website. Check the port.\nrika-server: open the workspace once with rika! 0.3.36\n\n";
        assert_eq!(startup_failure(stderr), "open the workspace once with rika! 0.3.36");
        assert_eq!(startup_failure("worker said no\n"), "worker said no");
        assert_eq!(startup_failure(""), "rika-server stopped before it was ready");
    }
    #[test]
    fn quick_note_launch_flag_is_exact() {
        let args = |values: &[&str]| values.iter().map(|value| value.to_string()).collect::<Vec<_>>();
        assert!(wants_quick_note(&args(&["kitty-workspace", "--quick-note"])));
        assert!(!wants_quick_note(&args(&["kitty-workspace"])));
        assert!(!wants_quick_note(&args(&["kitty-workspace", "--quick-notes"])));
        // argv[0] is never treated as the flag.
        assert!(!wants_quick_note(&args(&["--quick-note"])));
        assert!(wants_safe_mode(&args(&["kitty-workspace", "--safe"])));
        assert!(!wants_safe_mode(&args(&["kitty-workspace"])));
        assert!(!wants_safe_mode(&args(&["--safe"])));
    }
}
