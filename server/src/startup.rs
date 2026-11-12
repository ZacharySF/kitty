//! The workspace database before the worker starts: rika-server resolves it
//! (crate::settings) and migrates it (crate::migrations), so the Python worker opens a
//! current schema and never migrates (`open_workspace(auto_migrate=False)` refuses one that
//! is behind).
//!
//! A database from rika! 0.3.33 or older whose notes (033) or #tags (039) still have to be
//! converted is the one case the Rust runner leaves to Python: the worker program runs the
//! Python runner once (`--migrate-database DB`), then the Rust runner checks that nothing is
//! left. Without the worker program (an install without the Python backend) such a database
//! cannot open, and the message names the release that converts it.

use std::path::{Path, PathBuf};

use crate::migrations::{self, Outcome};
use crate::settings::{self, Paths};
use crate::worker::Launch;

/// The last release whose own start-up converts a pre-block workspace with Python.
pub const CONVERTING_RELEASE: &str = "0.3.36";

/// A `--demo` workspace: made by the front, removed when the front is done with it (the worker
/// used to make it as a `TemporaryDirectory`).
pub struct DemoFolder(PathBuf);

impl DemoFolder {
    fn create() -> Result<Self, String> {
        use std::os::unix::fs::DirBuilderExt;
        let base = std::env::temp_dir();
        for _ in 0..16 {
            let folder = base.join(format!("kitty-web-demo-{}", crate::auth::token_hex(4)));
            match std::fs::DirBuilder::new().mode(0o700).create(&folder) {
                Ok(()) => return Ok(Self(folder)),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(format!("creating the demo workspace: {error}")),
            }
        }
        Err("creating the demo workspace: no free name".into())
    }

    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for DemoFolder {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// The migrated workspace the worker is started on.
pub struct Prepared {
    pub paths: Paths,
    pub demo: Option<DemoFolder>,
}

impl Prepared {
    /// The worker must have opened the database migrated here (the routes answered by the
    /// front read it); a different one means the two resolved the workspace differently.
    pub fn opened_by_worker(&self, reported: &Path) -> Result<(), String> {
        if settings::resolve(reported) == settings::resolve(&self.paths.db_path) {
            Ok(())
        } else {
            Err(format!("the worker opened {} but rika-server migrated {}", reported.display(), self.paths.db_path.display()))
        }
    }
}

/// Resolve and migrate the workspace at `root` (or the demo workspace); `launch` runs the
/// Python runner when a conversion needs it. `owner_only` (the desktop shell's workspace)
/// creates every file and folder owner-only, as the desktop backend does (`os.umask(0o077)`).
pub async fn prepare(root: Option<PathBuf>, demo: bool, owner_only: bool, launch: &Launch) -> Result<Prepared, String> {
    if owner_only {
        // SAFETY: umask only swaps the process's file-creation mask; nothing else is running yet.
        unsafe { libc::umask(0o077) };
    }
    let (paths, demo) = if demo {
        let folder = DemoFolder::create()?;
        let paths = Paths { root: folder.path().to_path_buf(), db_path: folder.path().join("data").join("demo.db") };
        (paths, Some(folder))
    } else {
        (tokio::task::spawn_blocking(move || settings::load(root.as_deref())).await.map_err(|error| error.to_string())?, None)
    };
    let database = paths.db_path.clone();
    let outcome = run(database.clone()).await?;
    if let Outcome::NeedsPython { version, .. } = outcome {
        convert_with_python(launch, &database, version).await?;
        if let Outcome::NeedsPython { version, .. } = run(database.clone()).await? {
            return Err(format!("{}: migration {version:03} is still pending after the Python runner", database.display()));
        }
    }
    Ok(Prepared { paths, demo })
}

async fn run(database: PathBuf) -> Result<Outcome, String> {
    tokio::task::spawn_blocking(move || {
        migrations::connect(&database)
            .and_then(|conn| migrations::migrate(&conn, None))
            .map_err(|error| format!("migrating {}: {error}", database.display()))
    })
    .await
    .map_err(|error| error.to_string())?
}

async fn convert_with_python(launch: &Launch, database: &Path, version: u32) -> Result<(), String> {
    let status = tokio::process::Command::new(&launch.program)
        .args(&launch.args)
        .arg("--migrate-database")
        .arg(database)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .status()
        .await;
    match status {
        Ok(status) if status.success() => Ok(()),
        Ok(status) => Err(format!("the Python runner could not convert {} ({status})", database.display())),
        Err(error) if matches!(error.kind(), std::io::ErrorKind::NotFound | std::io::ErrorKind::PermissionDenied) => Err(format!(
            "{} was last opened by rika! 0.3.33 or older and migration {version:03} must convert its notes with the \
             Python backend, which this install does not have: open the workspace once with rika! {CONVERTING_RELEASE}, \
             then start this version again",
            database.display()
        )),
        Err(error) => Err(format!("could not start the Python runner ({}): {error}", launch.program)),
    }
}
