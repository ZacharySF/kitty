use std::{thread, time::Duration};

use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

use crate::Desktop;

pub fn busy(app: &AppHandle) {
    app.dialog().message("An update check, download, or installation is already in progress. Please finish its dialog or wait for kitty! to restart before quitting.")
        .title("kitty! update in progress").show(|_| {});
}

fn status(app: &AppHandle, message: &str) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_title(&format!("kitty! — {message}"));
    }
    if let Some(item) = app.menu().and_then(|menu| menu.get("updates")) {
        if let Some(item) = item.as_menuitem() { let _ = item.set_text(message); }
    }
}

fn message(app: &AppHandle, text: impl Into<String>, kind: MessageDialogKind) {
    app.dialog().message(text).title("kitty! updates").kind(kind).blocking_show();
}

pub fn check(app: &AppHandle) {
    if !app.state::<Desktop>().lifecycle.lock().begin_update() {
        busy(app);
        return;
    }
    let app = app.clone();
    thread::spawn(move || {
        status(&app, "Checking for updates…");
        if let Err(error) = tauri::async_runtime::block_on(run(&app)) {
            message(&app, format!("The update could not be completed. No running work has been stopped.\n\n{error}\n\nCheck your connection and try Check for updates again. Never install an update that fails signature verification."), MessageDialogKind::Error);
        }
        app.state::<Desktop>().lifecycle.lock().finish_update();
        status(&app, "Check for updates…");
        if let Some(window) = app.get_webview_window("main") { let _ = window.set_title("kitty!"); }
    });
}

async fn run(app: &AppHandle) -> Result<(), String> {
    if !matches!(tauri::utils::platform::bundle_type(),
        Some(tauri::utils::config::BundleType::Deb | tauri::utils::config::BundleType::AppImage)) {
        let open = app.dialog().message("This copy was not installed from a supported Linux .deb or AppImage package, so it cannot replace itself safely. Download the current desktop installer from the public releases page; your existing workspace stays in place.")
            .title("kitty! desktop installers")
            .buttons(MessageDialogButtons::OkCancelCustom("Open downloads".into(), "Cancel".into()))
            .blocking_show();
        if open {
            open::that_detached("https://github.com/ZacharySF/kitty-releases/releases/latest")
                .map_err(|error| format!("Could not open the downloads page: {error}"))?;
        }
        return Ok(());
    }
    let updater = app.updater_builder()
        .timeout(Duration::from_secs(30))
        // Also reject HTTPS-to-HTTP redirects and insecure artifact URLs, in debug
        // builds too. Trust is the embedded signing key, never a key from the feed.
        .configure_client(|client| client.https_only(true))
        .build().map_err(|error| error.to_string())?;
    let Some(mut update) = updater.check().await.map_err(|error| error.to_string())? else {
        message(app, "kitty! is up to date.", MessageDialogKind::Info);
        return Ok(());
    };
    if update.download_url.scheme() != "https" {
        return Err("The release supplied an insecure download URL; it was rejected.".into());
    }
    let download = app.dialog().message(format!("kitty! {} is available (installed: {}).\n\nDownload and verify the signed update? Your workspace stays available during the download. You can decline installation after verification.", update.version, update.current_version))
        .title("kitty! update available")
        .buttons(MessageDialogButtons::OkCancelCustom("Download update".into(), "Not now".into()))
        .blocking_show();
    if !download { return Ok(()); }

    status(app, "Downloading and verifying update…");
    // Plugin 2.12 does not carry the check timeout onto the download object.
    update.timeout = Some(Duration::from_secs(600));
    // download() returns only after mandatory minisign verification. Its finish
    // callback runs BEFORE verification, so it must never trigger backend drain.
    let bytes = update.download(|_, _| {}, || {}).await.map_err(|error| error.to_string())?;
    if !app.state::<Desktop>().lifecycle.lock().verified() {
        return Err("The application is stopping; the update was not installed.".into());
    }
    status(app, "Signed update verified — ready to install");
    let install = app.dialog().message(format!("The signed update to kitty! {} has been verified.\n\nInstall and restart now? kitty! will stop accepting new work, wait for accepted operations and active workers to finish, and save your workspace before installation. This can take time; do not force-close the app.\n\nOn Ubuntu, a .deb installation may request administrator authentication. An AppImage must be in a writable location.", update.version))
        .title("kitty! verified update ready")
        .buttons(MessageDialogButtons::OkCancelCustom("Install and restart".into(), "Later".into()))
        .blocking_show();
    if !install { return Ok(()); }
    if !app.state::<Desktop>().lifecycle.lock().begin_install() {
        return Err("The application is stopping; the update was not installed.".into());
    }

    status(app, "Saving workspace and finishing active work…");
    // Keep the disconnected webview out of the way. All control/status remains
    // native; the loopback page receives no updater, dialog, or process IPC rights.
    if let Some(window) = app.get_webview_window("main") { let _ = window.hide(); }
    app.dialog().message("The signed update is ready. kitty! is finishing accepted operations and saving your workspace, then it will install and restart automatically. Do not force-close it while work is finishing.")
        .title("kitty! saving before update").show(|_| {});
    app.state::<Desktop>().backend.lock().stop();
    app.state::<Desktop>().lifecycle.lock().backend_drained();
    status(app, "Installing verified update…");
    if let Err(error) = update.install(&bytes) {
        // Installation can fail after changing files. Never revive an old sidecar
        // against a partially replaced installation: restart its installed owner.
        message(app, format!("The update could not be installed. Your workspace was saved and its workers stopped safely.\n\n{error}\n\nkitty! will now restart to reopen the installed application. If it does not reopen, launch kitty! from your desktop menu, or download the installer again from https://github.com/ZacharySF/kitty-releases/releases/latest . Your workspace has not been deleted. For .deb updates, administrator authentication and working package dependencies are required; for AppImage updates, check directory permissions and free space."), MessageDialogKind::Error);
    }
    app.state::<Desktop>().lifecycle.lock().restart();
    app.restart();
}

#[cfg(test)]
mod tests {
    use base64::{engine::general_purpose::STANDARD, Engine};
    use super::*;

    /// Explicit network smoke only: uses production GitHub metadata, downloads
    /// authenticated release bytes, and rejects a corrupted signature. Never installs.
    #[test]
    #[ignore = "requires a published signed GitHub release and network access"]
    fn published_release_is_authenticated_without_installing() {
        let config: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let updater_config = &config["plugins"]["updater"];
        let mut context = tauri::test::mock_context(tauri::test::noop_assets());
        context.config_mut().plugins.0.insert("updater".into(), updater_config.clone());
        let app = tauri::test::mock_builder()
            .plugin(tauri_plugin_updater::Builder::new().build())
            .build(context).unwrap();
        let target = std::env::var("KITTY_UPDATER_SMOKE_TARGET").unwrap_or_else(|_| "linux-x86_64-deb".into());
        let endpoint: url::Url = updater_config["endpoints"][0].as_str().unwrap().parse().unwrap();
        let build = || app.updater_builder().target(&target)
            .endpoints(vec![endpoint.clone()]).unwrap()
            .timeout(Duration::from_secs(60))
            .configure_client(|client| client.https_only(true));
        tauri::async_runtime::block_on(async {
            // Force the ordinary no-update branch independent of the test binary version.
            assert!(build().version_comparator(|_, _| false).build().unwrap().check().await.unwrap().is_none());
            let mut update = build().version_comparator(|_, _| true).build().unwrap().check().await.unwrap().unwrap();
            assert_eq!(update.download_url.scheme(), "https");
            update.timeout = Some(Duration::from_secs(600));
            let bytes = update.download(|_, _| {}, || {}).await.expect("published artifact must authenticate with embedded key and signed version");
            assert!(!bytes.is_empty());
            drop(bytes);
            // Keep a well-formed minisign file and corrupt only signature bytes,
            // not the parser framing, so rejection exercises cryptographic trust.
            let signature = String::from_utf8(STANDARD.decode(&update.signature).unwrap()).unwrap();
            let mut lines: Vec<String> = signature.lines().map(str::to_owned).collect();
            let mut signed = STANDARD.decode(&lines[1]).unwrap();
            *signed.last_mut().unwrap() ^= 1;
            lines[1] = STANDARD.encode(signed);
            update.signature = STANDARD.encode(format!("{}\n", lines.join("\n")));
            let error = update.download(|_, _| {}, || {}).await.expect_err("a corrupt signature must never authenticate");
            assert!(matches!(error, tauri_plugin_updater::Error::Minisign(_)), "unexpected failure instead of signature rejection: {error}");
        });
    }
}
