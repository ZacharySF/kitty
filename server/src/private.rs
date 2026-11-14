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

/// No one but the owner may read or write the file (Unix mode bits). True on Windows,
/// where the user's profile already keeps other accounts out.
pub fn owner_only(meta: &fs::Metadata) -> bool {
    mode_of(meta) & 0o077 == 0
}

/// The file belongs to the user this process runs as (Unix uid). True on Windows.
pub fn owned_by_me(meta: &fs::Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        // SAFETY: getuid has no preconditions and cannot fail.
        return meta.uid() == unsafe { libc::getuid() };
    }
    #[cfg(not(unix))]
    {
        let _ = meta;
        true
    }
}

/// What tells one saved version of a file from the next without reading it: when it was
/// written (nanoseconds), its size and, on Unix, which file it is (a replaced file is a new
/// inode; Windows has no stable equivalent in std, so 0 there).
pub fn signature(meta: &fs::Metadata) -> (i128, u64, u64) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        return (i128::from(meta.mtime()) * 1_000_000_000 + i128::from(meta.mtime_nsec()), meta.size(), meta.ino());
    }
    #[cfg(not(unix))]
    {
        let written = meta.modified().ok().and_then(|at| at.duration_since(std::time::UNIX_EPOCH).ok()).map_or(0, |since| since.as_nanos() as i128);
        (written, meta.len(), 0)
    }
}

/// A folder to lock for exclusive use: Unix locks the folder itself (as the Python stores
/// do); Windows cannot open a folder as a file, so `.lock` inside it is locked there.
pub fn open_for_lock(folder: &Path) -> io::Result<fs::File> {
    #[cfg(unix)]
    return fs::File::open(folder);
    #[cfg(not(unix))]
    return fs::OpenOptions::new().create(true).truncate(false).write(true).open(folder.join(".lock"));
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
