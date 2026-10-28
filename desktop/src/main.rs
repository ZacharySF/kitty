mod update_lifecycle;
mod updates;
mod workspace_path;

use std::{
    fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{atomic::{AtomicBool, AtomicU64, Ordering}, mpsc},
    thread,
    time::Duration,
};
use parking_lot::Mutex;
use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem},
    tray::TrayIconBuilder,
    webview::NewWindowResponse,
    Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};
use url::Url;

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
    lifecycle: Mutex<update_lifecycle::Lifecycle>,
    preferences: PathBuf,
    root: PathBuf,
    origin: Url,
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
        // deliberately no frontend shell, file, opener, or notification command.
        let _ = open::that_detached(url.as_str());
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
            .title("kitty! quick note").inner_size(380.0, 460.0).min_inner_size(260.0, 220.0)
            .transparent(true).decorations(false).always_on_top(true)
            .initialization_script(TRANSPARENT_MARKER)
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

fn start_backend(root: &Path) -> Result<(Backend, Url), Box<dyn std::error::Error>> {
    let executable = std::env::current_exe()?;
    let packaged = executable.parent().ok_or("Desktop executable has no directory")?.join("kitty-web");
    // Tauri dev and cargo smoke binaries use the target-suffixed staging sidecar.
    // Installed bundles always use their own sibling executable, never a checkout.
    let binary = if packaged.is_file() { packaged } else if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries")
            .join(format!("kitty-web-{}", env!("KITTY_TARGET_TRIPLE")))
    } else { return Err("The bundled kitty-web executable is missing".into()); };
    let child = Command::new(binary).arg("--workspace").arg(root)
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn()?;
    let mut backend = Backend(Some(child));
    let output = backend.0.as_mut().unwrap().stdout.take().ok_or("Backend readiness pipe unavailable")?;
    let (send, receive) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let mut output = BufReader::new(output);
        let mut line = String::new();
        let result = output.read_line(&mut line).map(|_| line);
        let _ = send.send(result);
        // Keep diagnostics private and keep the child pipe from becoming full.
        let mut sink = std::io::sink();
        let _ = std::io::copy(&mut output, &mut sink);
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
    let message: serde_json::Value = serde_json::from_str(&line)?;
    let origin = Url::parse(message["origin"].as_str().ok_or("Invalid backend readiness message")?)?;
    if !local_url(&origin, &origin) || origin.port().is_none() || origin.path() != "/"
        || origin.query().is_some() || origin.fragment().is_some() {
        return Err("Backend did not bind a private loopback origin".into());
    }
    Ok((backend, origin))
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

fn main() {
    // WebKitGTK's native Wayland path can produce a negative device scale on
    // fractional-scale desktops. Prefer XWayland when available; explicit
    // backend selection and Wayland-only sessions remain untouched.
    #[cfg(target_os = "linux")]
    if std::env::var_os("GDK_BACKEND").is_none()
        && std::env::var_os("WAYLAND_DISPLAY").is_some()
        && std::env::var_os("DISPLAY").is_some()
    {
        std::env::set_var("GDK_BACKEND", "x11");
    }
    // Applies to new private files only; never changes selected existing files.
    unsafe { libc::umask(0o077); }
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if wants_quick_note(&args) { open_quick_note(app, "quick-note".into()) } else { show(app) }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let root = workspace().map_err(std::io::Error::other)?;
            fs::create_dir_all(root.join(".private"))?;
            let root = root.canonicalize()?;
            let preferences = root.join(".private/desktop.json");
            let background = fs::read(&preferences).ok()
                .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
                .and_then(|value| value["background"].as_bool()).unwrap_or(false);
            let (backend, origin) = start_backend(&root)?;
            app.manage(Desktop {
                backend: Mutex::new(backend), background: AtomicBool::new(background),
                lifecycle: Mutex::new(update_lifecycle::Lifecycle::default()), preferences,
                root: root.clone(), origin: origin.clone(), next_note: AtomicU64::new(1),
            });
            let show_item = MenuItem::with_id(app, "show", "Show kitty!", true, None::<&str>)?;
            let note_item = MenuItem::with_id(app, "quick-note", "New quick note", true, None::<&str>)?;
            let background_item = CheckMenuItem::with_id(app, "background", "Keep running on close (monitoring / reminders)", true, background, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit kitty! (save and stop workers)", true, Some("Ctrl+Shift+Q"))?;
            let update = MenuItem::with_id(app, "updates", "Check for updates…", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &note_item, &background_item, &update, &quit])?;
            // Also expose the native window menu: some Linux desktops have no tray.
            app.set_menu(menu.clone())?;
            let tray = TrayIconBuilder::new().tooltip("kitty!").menu(&menu)
                .icon(tauri::image::Image::from_bytes(include_bytes!("../icons/icon.png"))?).build(app);
            if tray.is_err() {
                app.state::<Desktop>().background.store(false, Ordering::SeqCst);
                background_item.set_checked(false)?;
                background_item.set_enabled(false)?;
            }
            let navigation_origin = origin.clone();
            let note_origin = origin.clone();
            let note_app = app.handle().clone();
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(origin))
                .title("kitty!").inner_size(1280.0, 850.0).min_inner_size(760.0, 500.0)
                .transparent(true)
                .initialization_script(TRANSPARENT_MARKER)
                .devtools(cfg!(debug_assertions))
                .data_directory(root.join(".private/webview"))
                .on_navigation(move |url| {
                    if local_url(url, &navigation_origin) { true }
                    else { open_external(url); false }
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
                let state = app.state::<Desktop>();
                let enabled = !state.background.load(Ordering::SeqCst);
                let temporary = state.preferences.with_extension("json.new");
                let bytes = if enabled { b"{\"background\":true}".as_slice() } else { b"{\"background\":false}".as_slice() };
                if fs::write(&temporary, bytes).and_then(|_| fs::rename(&temporary, &state.preferences)).is_ok() {
                    state.background.store(enabled, Ordering::SeqCst);
                } else if let Some(item) = app.menu().and_then(|menu| menu.get("background")) {
                    if let Some(item) = item.as_check_menuitem() { let _ = item.set_checked(!enabled); }
                }
            }
            _ => {},
        })
        .build(tauri::generate_context!())
        .expect("Unable to start kitty!: verify the packaged backend and local workspace");
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
    fn quick_note_launch_flag_is_exact() {
        let args = |values: &[&str]| values.iter().map(|value| value.to_string()).collect::<Vec<_>>();
        assert!(wants_quick_note(&args(&["kitty-workspace", "--quick-note"])));
        assert!(!wants_quick_note(&args(&["kitty-workspace"])));
        assert!(!wants_quick_note(&args(&["kitty-workspace", "--quick-notes"])));
        // argv[0] is never treated as the flag.
        assert!(!wants_quick_note(&args(&["--quick-note"])));
    }
}
