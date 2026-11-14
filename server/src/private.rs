//! Owner-only files and folders on every platform. On Unix the mode is set when the file
//! or folder is made; Windows has no mode bits, and what rika! writes lives under the
//! user's own profile, which other accounts cannot read, so the same calls are no-ops
//! there. Call sites keep the Unix spelling (`.mode(0o600)`) through the `Mode` trait.
use std::{fs, io, path::Path};

pub trait Mode {
    /// The Unix permission bits a new file or folder gets; nothing on Windows.
    fn mode(&mut self, mode: u32) -> &mut Self;
}

impl Mode for fs::DirBuilder {
    fn mode(&mut self, mode: u32) -> &mut Self {
        #[cfg(unix)]
        std::os::unix::fs::DirBuilderExt::mode(self, mode);
        let _ = mode;
        self
    }
}

impl Mode for fs::OpenOptions {
    fn mode(&mut self, mode: u32) -> &mut Self {
        #[cfg(unix)]
        std::os::unix::fs::OpenOptionsExt::mode(self, mode);
        let _ = mode;
        self
    }
}

impl Mode for tokio::fs::OpenOptions {
    fn mode(&mut self, mode: u32) -> &mut Self {
        #[cfg(unix)]
        tokio::fs::OpenOptions::mode(self, mode);
        let _ = mode;
        self
    }
}

/// Set a file's permission bits (Unix); nothing on Windows.
pub fn set_mode(path: &Path, mode: u32) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(mode))?;
    }
    let _ = (path, mode);
    Ok(())
}

/// A file's permission bits (Unix); owner read and write on Windows.
pub fn mode_of(meta: &fs::Metadata) -> u32 {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        return meta.permissions().mode() & 0o7777;
    }
    #[cfg(not(unix))]
    {
        let _ = meta;
        0o600
    }
}

/// Open without following a symbolic link at the last component (Unix `O_NOFOLLOW`). On
/// Windows the caller's own `symlink_metadata` check is what refuses a link.
pub fn no_follow(options: &mut fs::OpenOptions) -> &mut fs::OpenOptions {
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::custom_flags(options, libc::O_NOFOLLOW);
    options
}

/// The error `no_follow` gives for a symbolic link.
pub fn is_link_refusal(error: &io::Error) -> bool {
    #[cfg(unix)]
    return error.raw_os_error() == Some(libc::ELOOP);
    #[cfg(not(unix))]
    {
        let _ = error;
        false
    }
}

/// Every file and folder this process makes from now on is owner-only (Unix umask).
pub fn owner_only_from_now() {
    // SAFETY: umask only swaps the process's file-creation mask; callers run it before any thread writes.
    #[cfg(unix)]
    unsafe {
        libc::umask(0o077);
    }
}

/// Resolves when the process is asked to stop: Ctrl+C everywhere, SIGTERM on Unix.
pub async fn stop_requested() {
    #[cfg(unix)]
    {
        let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).expect("SIGTERM handler");
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = terminate.recv() => {}
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}
