//! The bubblewrap sandbox: an allowlisted read-only filesystem, an empty environment, no
//! network, and this engine's own `worker` as the only program started inside.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::discover::{bundle, bundle_mount, executable, RUNTIMES};
use crate::{Failure, Outcome};

/// Where this engine's binary appears inside the sandbox.
pub const WORKER: &str = "/run/kitty-workshop";

/// Immutable runtime installations; never `/`, `/home`, user profiles or `/etc` as a whole.
const SYSTEM: [&str; 6] = ["/usr", "/bin", "/lib", "/lib64", "/nix/store", "/etc/fonts"];

/// Job files the worker may read but never replace.
const READ_ONLY: [&str; 3] = ["request.json", "frozen.html", "project"];

fn under(path: &Path, roots: &[PathBuf]) -> bool {
    roots.iter().any(|root| path.starts_with(root))
}

fn text(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

/// The bubblewrap argument list and environment for running `inner` on job `folder`.
pub fn command(worker: &Path, folder: &Path, inner: &[String]) -> (Vec<String>, Vec<(String, String)>) {
    let mut args: Vec<String> = [
        "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL", "--clearenv",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home", "--dir", "/run",
        "--dir", "/etc",
    ]
    .map(String::from)
    .to_vec();
    let mut mounts: Vec<PathBuf> = SYSTEM.iter().map(PathBuf::from).filter(|p| p.exists()).collect();
    for root in &mounts {
        args.extend(["--ro-bind".into(), text(root), text(root)]);
    }
    for item in ["/etc/ld.so.cache", "/etc/localtime"] {
        if Path::new(item).is_file() {
            args.extend(["--ro-bind".into(), item.into(), item.into()]);
        }
    }
    // Explicit binary configuration is authority to expose that installation, not its home.
    for (name, _) in RUNTIMES {
        let Some(path) = executable(name) else { continue };
        for candidate in [path.clone(), path.canonicalize().unwrap_or(path)] {
            if !under(&candidate, &mounts) {
                args.extend(["--ro-bind".into(), text(&candidate), text(&candidate)]);
                mounts.push(candidate);
            }
        }
    }
    if let Some(bundle) = bundle() {
        args.extend(["--ro-bind".into(), text(&bundle), bundle_mount(&bundle)]);
    }
    args.extend(["--ro-bind".into(), text(worker), WORKER.into()]);
    args.extend(["--bind".into(), text(folder), "/job".into()]);
    for name in READ_ONLY {
        if folder.join(name).exists() {
            args.extend(["--ro-bind".into(), text(&folder.join(name)), format!("/job/{name}")]);
        }
    }
    let environment: Vec<(String, String)> = [
        ("HOME", "/tmp/home"), ("PATH", "/usr/bin:/bin"), ("LANG", "C.UTF-8"), ("LC_ALL", "C.UTF-8"),
        ("TMPDIR", "/tmp"), ("XDG_CACHE_HOME", "/tmp/cache"), ("XDG_CONFIG_HOME", "/tmp/config"),
        ("FONTCONFIG_FILE", "/etc/fonts/fonts.conf"),
        // Reads: 'r' (no dotfiles). LuaTeX's font loader opens its own Lua files by absolute
        // path, which 'p' refuses; the sandbox already limits what exists. Writes stay paranoid.
        ("TEXMFOUTPUT", "/job/out"), ("openin_any", "r"), ("openout_any", "p"),
        // LuaTeX's font loader needs a writable cache; keep it in the private /tmp.
        ("TEXMFVAR", "/tmp/texmf-var"), ("TEXMFCACHE", "/tmp/texmf-var"),
    ]
    .map(|(key, value)| (key.to_string(), value.to_string()))
    .to_vec();
    for (key, value) in &environment {
        args.extend(["--setenv".into(), key.clone(), value.clone()]);
    }
    let directory = if folder.join("project").exists() { "/job/project" } else { "/job" };
    args.extend(["--chdir".into(), directory.into(), "--".into()]);
    args.extend(inner.iter().cloned());
    (args, environment)
}

/// A diagnostics log the sandbox cannot reach: created outside the job and unlinked at once.
fn log_file() -> Outcome<File> {
    let unique = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_nanos());
    let path = std::env::temp_dir().join(format!(".kitty-workshop-{}-{unique}.log", std::process::id()));
    let file = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&path)
        .map_err(|e| Failure::new(500, format!("Cannot create the sandbox log: {e}")))?;
    let _ = std::fs::remove_file(&path);
    Ok(file)
}

fn tail(file: &mut File, limit: u64) -> String {
    let end = file.seek(SeekFrom::End(0)).unwrap_or(0);
    let _ = file.seek(SeekFrom::Start(end.saturating_sub(limit)));
    let mut raw = Vec::new();
    let _ = file.take(limit).read_to_end(&mut raw);
    String::from_utf8_lossy(&raw).into_owned()
}

/// Run one worker operation on `folder` in the sandbox, killing its whole session at the end.
pub fn run(folder: &Path, request: &Value, timeout: Duration) -> Outcome<()> {
    let bwrap = executable("bwrap")
        .ok_or_else(|| Failure::new(503, "bubblewrap sandbox is unavailable; install bwrap or set KITTY_BWRAP"))?;
    let worker = std::env::current_exe()
        .and_then(|path| path.canonicalize())
        .map_err(|e| Failure::new(500, format!("Cannot locate the workshop engine: {e}")))?;
    std::fs::write(folder.join("request.json"), request.to_string())
        .map_err(|e| Failure::new(500, format!("Cannot write the worker request: {e}")))?;
    let (args, environment) = command(&worker, folder, &[WORKER.into(), "worker".into()]);
    let mut log = log_file()?;
    let output = log.try_clone().map_err(|e| Failure::new(500, e.to_string()))?;
    let errors = log.try_clone().map_err(|e| Failure::new(500, e.to_string()))?;
    let mut child = Command::new(&bwrap)
        .args(&args)
        .env_clear()
        .envs(environment)
        .stdin(Stdio::null())
        .stdout(output)
        .stderr(errors)
        .process_group(0)
        .spawn()
        .map_err(|e| Failure::new(503, format!("Cannot start the sandbox: {e}")))?;
    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(20)),
            _ => break None,
        }
    };
    // The worker and anything it started share bubblewrap's process group.
    // SAFETY: plain syscall on the group this process created; ESRCH means it already exited.
    unsafe { libc::killpg(child.id() as libc::pid_t, libc::SIGKILL) };
    let _ = child.wait();
    match status {
        None => Err(Failure::new(422, "Sandbox worker exceeded its time limit")),
        Some(status) if status.success() => Ok(()),
        Some(_) => Err(Failure::new(422, format!("Sandbox worker failed: {}", tail(&mut log, 6000).trim()))),
    }
}

/// Read a worker artifact without following symlinks or blocking on special files.
pub fn artifact(path: &Path, maximum: u64) -> Outcome<Vec<u8>> {
    let unsafe_artifact = || Failure::new(409, "Worker artifact is missing or unsafe");
    let file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)
        .map_err(|_| unsafe_artifact())?;
    let info = file.metadata().map_err(|_| unsafe_artifact())?;
    if !info.is_file() || info.len() > maximum {
        return Err(Failure::new(409, "Worker artifact exceeds limits or is not a regular file"));
    }
    let mut raw = Vec::with_capacity(info.len() as usize);
    file.take(maximum + 1).read_to_end(&mut raw).map_err(|_| unsafe_artifact())?;
    if raw.len() as u64 > maximum {
        return Err(Failure::new(409, "Worker artifact exceeds limits or is not a regular file"));
    }
    Ok(raw)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn built() -> (Vec<String>, Vec<(String, String)>) {
        std::env::set_var("ANTHROPIC_API_KEY", "must-not-leak");
        static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let unique = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let folder = std::env::temp_dir().join(format!("kitty-workshop-test-{}-{unique}", std::process::id()));
        std::fs::create_dir_all(folder.join("project")).unwrap();
        let result = command(Path::new("/opt/engine"), &folder, &["/run/kitty-workshop".into(), "worker".into()]);
        std::fs::remove_dir_all(&folder).unwrap();
        result
    }

    #[test]
    fn environment_is_an_allowlist_without_secrets_or_the_home() {
        let (args, environment) = built();
        assert!(environment.iter().all(|(key, _)| key != "ANTHROPIC_API_KEY"));
        assert!(!args.iter().any(|arg| arg.contains("must-not-leak")));
        assert!(environment.contains(&("HOME".into(), "/tmp/home".into())));
        assert!(args.iter().any(|arg| arg == "--clearenv"));
    }

    #[test]
    fn network_namespaces_are_dropped_and_root_is_never_mounted() {
        let (args, _) = built();
        assert!(args.iter().any(|arg| arg == "--unshare-all"));
        assert!(!args.windows(3).any(|w| w[0].ends_with("bind") && w[1] == "/" ));
        let home = std::env::var("HOME").unwrap();
        assert!(!args.windows(3).any(|w| w[0].ends_with("bind") && w[2] == home));
    }

    #[test]
    fn the_worker_is_this_engine_read_only_and_the_project_cannot_be_replaced() {
        let (args, _) = built();
        assert!(args.windows(3).any(|w| w == ["--ro-bind", "/opt/engine", WORKER]));
        assert!(args.windows(3).any(|w| w[0] == "--ro-bind" && w[2] == "/job/project"));
        assert_eq!(args.iter().skip_while(|a| *a != "--").skip(1).collect::<Vec<_>>(), ["/run/kitty-workshop", "worker"]);
        let chdir = args.iter().position(|a| a == "--chdir").unwrap();
        assert_eq!(args[chdir + 1], "/job/project");
    }

    #[test]
    fn artifacts_refuse_symlinks_and_oversized_files() {
        let folder = std::env::temp_dir().join(format!("kitty-workshop-artifact-{}", std::process::id()));
        std::fs::create_dir_all(&folder).unwrap();
        std::fs::write(folder.join("big"), vec![0u8; 32]).unwrap();
        std::os::unix::fs::symlink("/etc/passwd", folder.join("link")).unwrap();
        assert_eq!(artifact(&folder.join("link"), 1 << 20).unwrap_err().status, 409);
        assert_eq!(artifact(&folder.join("big"), 16).unwrap_err().status, 409);
        assert_eq!(artifact(&folder.join("big"), 32).unwrap().len(), 32);
        std::fs::remove_dir_all(&folder).unwrap();
    }
}
