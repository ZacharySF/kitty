use std::{fs, io::{self, Write}, path::{Path, PathBuf}};

fn sibling(image: &Path, suffix: &str) -> PathBuf {
    let mut name = image.as_os_str().to_os_string();
    name.push(suffix);
    PathBuf::from(name)
}

fn atomic_write(
    destination: &Path,
    permissions: fs::Permissions,
    write: impl FnOnce(&mut fs::File) -> io::Result<()>,
) -> io::Result<()> {
    // st_dev alone cannot identify mount boundaries inside appimage-run's FHS
    // bind mounts. Stage beside the destination, never in /tmp or the cache.
    let staged = sibling(destination, ".new");
    let mut output = fs::OpenOptions::new().write(true).create_new(true).open(&staged)?;
    let result = (|| {
        output.set_permissions(permissions)?;
        write(&mut output)?;
        output.sync_all()
    })();
    // No writable handle may survive publication of an executable.
    drop(output);
    let result = result.and_then(|_| fs::rename(&staged, destination));
    if result.is_err() { let _ = fs::remove_file(&staged); }
    result
}

fn replace(image: &Path, write: impl FnOnce(&mut fs::File) -> io::Result<()>) -> io::Result<()> {
    let previous = sibling(image, ".previous");
    let mut current = fs::File::open(image)?;
    let permissions = current.metadata()?.permissions();
    atomic_write(&previous, permissions.clone(), |output| {
        io::copy(&mut current, output).map(|_| ())
    })?;
    // The original remains at its installed path until a complete, synced,
    // executable replacement is ready. Failure needs no destructive rollback.
    atomic_write(image, permissions, write)
}

/// Bytes must come from tauri-plugin-updater's authenticated download().
/// Its Linux installer uses cross-mount renames in appimage-run; only AppImage
/// replacement is handled here. Signature/version/TLS checks remain upstream.
pub fn install(image: &Path, bytes: &[u8]) -> Result<(), String> {
    replace(image, |output| output.write_all(bytes))
        .map_err(|error| format!("Could not replace the AppImage; the installed version is unchanged: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, os::unix::fs::PermissionsExt, process::Command, time::{SystemTime, UNIX_EPOCH}};

    struct Installation(std::path::PathBuf);
    impl Installation {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!("kitty-update-{}-{}", std::process::id(), SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()));
            fs::create_dir(&root).unwrap();
            fs::write(root.join("kitty.AppImage"), b"#!/bin/sh\nexit 17\n").unwrap();
            fs::set_permissions(root.join("kitty.AppImage"), fs::Permissions::from_mode(0o755)).unwrap();
            fs::write(root.join("profile.yaml"), "name: Synthetic update person\n").unwrap();
            Self(root)
        }
        fn image(&self) -> std::path::PathBuf { self.0.join("kitty.AppImage") }
    }
    impl Drop for Installation {
        fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
    }

    /// Runs a fixture executable and returns its exit code. A test thread that forks
    /// while this one still holds the freshly written file open makes `exec` fail with
    /// ETXTBSY until that child execs; that is a test-harness race, so retry briefly.
    fn exit_code(path: &std::path::Path) -> Option<i32> {
        for _ in 0..100 {
            match Command::new(path).status() {
                Err(error) if error.kind() == io::ErrorKind::ExecutableFileBusy => std::thread::sleep(std::time::Duration::from_millis(10)),
                result => return result.unwrap().code(),
            }
        }
        panic!("{} stayed busy", path.display());
    }

    #[test]
    fn successful_replacement_keeps_a_launchable_prior_installation_and_workspace() {
        let fixture = Installation::new();
        install(&fixture.image(), b"#!/bin/sh\nexit 23\n").unwrap();
        assert_eq!(exit_code(&fixture.image()), Some(23));
        assert_eq!(exit_code(&fixture.0.join("kitty.AppImage.previous")), Some(17));
        assert_eq!(fs::read_to_string(fixture.0.join("profile.yaml")).unwrap(), "name: Synthetic update person\n");
    }

    #[test]
    fn failed_partial_write_keeps_the_launchable_original() {
        let fixture = Installation::new();
        let error = replace(&fixture.image(), |output| {
            output.write_all(b"partial corrupt replacement")?;
            Err(io::Error::other("simulated disk failure"))
        }).unwrap_err();
        assert!(error.to_string().contains("simulated disk failure"));
        assert_eq!(exit_code(&fixture.image()), Some(17));
        assert_eq!(fs::read(fixture.0.join("kitty.AppImage.previous")).unwrap(), fs::read(fixture.image()).unwrap());
        assert_eq!(fs::read_to_string(fixture.0.join("profile.yaml")).unwrap(), "name: Synthetic update person\n");
    }

    #[test]
    fn backup_failure_does_not_attempt_replacement_or_remove_the_current_image() {
        let fixture = Installation::new();
        fs::create_dir(fixture.0.join("kitty.AppImage.previous")).unwrap();
        let mut replaced = false;
        let result = replace(&fixture.image(), |_output| { replaced = true; Ok(()) });
        assert!(result.is_err());
        assert!(!replaced);
        assert_eq!(exit_code(&fixture.image()), Some(17));
    }
}
