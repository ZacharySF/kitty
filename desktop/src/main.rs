use std::{
    fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{atomic::{AtomicBool, Ordering}, mpsc},
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
    quitting: AtomicBool,
    stopped: AtomicBool,
    preferences: PathBuf,
}

fn workspace() -> Result<PathBuf, String> {
    if let Some(root) = std::env::var_os("KITTY_HOME") {
        if root.is_empty() { return Err("KITTY_HOME must not be empty".into()); }
        return Ok(PathBuf::from(root));
    }
    let data = match std::env::var_os("XDG_DATA_HOME") {
        Some(path) if Path::new(&path).is_absolute() => PathBuf::from(path),
        _ => PathBuf::from(std::env::var_os("HOME").ok_or("HOME is unavailable")?).join(".local/share"),
    };
    Ok(data.join("kitty/workspace"))
}

fn local_url(url: &Url, origin: &Url) -> bool {
    url.scheme() == "http" && url.host_str() == Some("127.0.0.1")
        && url.port_or_known_default() == origin.port_or_known_default()
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
    if state.quitting.swap(true, Ordering::SeqCst) { return; }
    let app = app.clone();
    thread::spawn(move || {
        app.state::<Desktop>().backend.lock().stop();
        app.state::<Desktop>().stopped.store(true, Ordering::SeqCst);
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
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show(app)))
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
                quitting: AtomicBool::new(false), stopped: AtomicBool::new(false), preferences,
            });
            let show_item = MenuItem::with_id(app, "show", "Show kitty!", true, None::<&str>)?;
            let background_item = CheckMenuItem::with_id(app, "background", "Keep running on close (monitoring / reminders)", true, background, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit kitty! (save and stop workers)", true, Some("Ctrl+Shift+Q"))?;
            let menu = Menu::with_items(app, &[&show_item, &background_item, &quit])?;
            let tray = TrayIconBuilder::new().tooltip("kitty!").menu(&menu)
                .icon(tauri::image::Image::from_bytes(include_bytes!("../icons/icon.png"))?).build(app);
            if tray.is_err() {
                app.state::<Desktop>().background.store(false, Ordering::SeqCst);
                background_item.set_checked(false)?;
                background_item.set_enabled(false)?;
            }
            let navigation_origin = origin.clone();
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(origin))
                .title("kitty!").inner_size(1280.0, 850.0).min_inner_size(760.0, 500.0)
                .devtools(cfg!(debug_assertions))
                .data_directory(root.join(".private/webview"))
                .on_navigation(move |url| {
                    if local_url(url, &navigation_origin) { true }
                    else { open_external(url); false }
                })
                .on_new_window(|url, _features| {
                    open_external(&url);
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
                if state.quitting.load(Ordering::SeqCst) { break; }
                let exited = state.backend.lock().0.as_mut()
                    .and_then(|child| child.try_wait().ok()).flatten().is_some();
                if exited {
                    state.stopped.store(true, Ordering::SeqCst);
                    handle.exit(1);
                    break;
                }
            });
            Ok(())
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => show(app),
            "quit" => request_quit(app),
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
                if !state.stopped.load(Ordering::SeqCst) {
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
    fn external_opener_rejects_non_web_and_credentials() {
        for bad in ["file:///tmp/private", "javascript:alert(1)", "mailto:test@example.com", "https://user:password@example.com/"] {
            assert!(!external_url(&Url::parse(bad).unwrap()));
        }
        assert!(external_url(&Url::parse("https://example.com/path").unwrap()));
    }
}
