//! Start at login: a systemd user unit that opens rika! with its window hidden
//! (`--background`), so reminders and notifications arrive while the window is closed.
//! The unit belongs to the graphical session; turning the switch on writes the unit and
//! the `graphical-session.target.wants` link (what `systemctl --user enable` would make),
//! turning it off removes both. Nothing outside `systemd/user` in the user's config
//! folder is touched.
use std::{ffi::OsString, fs, io, path::{Path, PathBuf}, process::Command};

pub const UNIT: &str = "rika.service";
const WANTS: &str = "graphical-session.target.wants";

/// The user's systemd unit folder: `$XDG_CONFIG_HOME/systemd/user`, else `~/.config/systemd/user`.
pub fn unit_dir(xdg_config_home: Option<OsString>, home: Option<OsString>) -> Option<PathBuf> {
    let config = match xdg_config_home {
        Some(path) if Path::new(&path).is_absolute() => PathBuf::from(path),
        _ => PathBuf::from(home.filter(|home| !home.is_empty())?).join(".config"),
    };
    Some(config.join("systemd/user"))
}

/// The unit's text. `ExecStart` is one quoted word: `\`, `"` and `%` in the path are escaped
/// as systemd asks, so a path with spaces or specifiers starts the same program.
pub fn unit_text(program: &Path) -> Option<String> {
    let path = program.to_str().filter(|path| program.is_absolute() && !path.contains(['\n', '\r', '\0']))?;
    let quoted = path.replace('\\', "\\\\").replace('"', "\\\"").replace('%', "%%");
    Some(format!("[Unit]\nDescription=rika! in the background (reminders with the window closed)\n\
PartOf=graphical-session.target\nAfter=graphical-session.target\n\n\
[Service]\nExecStart=\"{quoted}\" --background\n\n\
[Install]\nWantedBy=graphical-session.target\n"))
}

/// The program a login should start: the installed launcher when it names itself (NixOS runs the
/// AppImage through appimage-run, so the bare AppImage cannot start there), else the AppImage
/// itself when rika! runs from one, else this executable.
pub fn program(launcher: Option<OsString>, appimage: Option<OsString>, current: io::Result<PathBuf>) -> Option<PathBuf> {
    let absolute = |value: OsString| Some(PathBuf::from(value)).filter(|path| path.is_absolute());
    launcher.and_then(absolute).or_else(|| appimage.and_then(absolute)).or_else(|| current.ok())
}

/// Start at login can be set here: there is a unit folder to write to and systemd runs this session.
pub fn available(dir: Option<&Path>) -> bool {
    dir.is_some() && Path::new("/run/systemd/system").is_dir()
}

pub fn enabled(dir: &Path) -> bool {
    dir.join(UNIT).is_file() && dir.join(WANTS).join(UNIT).exists()
}

/// Turn start at login on (write the unit and its link) or off (remove both).
pub fn set(dir: &Path, program: &Path, on: bool) -> io::Result<()> {
    let unit = dir.join(UNIT);
    let link = dir.join(WANTS).join(UNIT);
    if on {
        let text = unit_text(program).ok_or_else(|| io::Error::other("rika!'s own path cannot be written into a unit"))?;
        fs::create_dir_all(dir.join(WANTS))?;
        fs::write(&unit, text)?;
        match fs::remove_file(&link) {
            Err(error) if error.kind() != io::ErrorKind::NotFound => return Err(error),
            _ => {}
        }
        #[cfg(unix)]
        std::os::unix::fs::symlink(&unit, &link)?;
        // Never reached off Linux: `available` finds no systemd there.
        #[cfg(not(unix))]
        return Err(io::Error::other("Start at login needs systemd"));
    } else {
        for path in [&link, &unit] {
            match fs::remove_file(path) {
                Err(error) if error.kind() != io::ErrorKind::NotFound => return Err(error),
                _ => {}
            }
        }
    }
    // systemd reads the change at the next login either way; this only makes it current now.
    let _ = Command::new("systemctl").args(["--user", "daemon-reload"]).status();
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_unit_starts_this_program_hidden_with_the_graphical_session() {
        let text = unit_text(Path::new("/opt/rika/rika-workspace")).unwrap();
        assert!(text.contains("ExecStart=\"/opt/rika/rika-workspace\" --background\n"));
        assert!(text.contains("WantedBy=graphical-session.target\n") && text.contains("PartOf=graphical-session.target\n"));
    }

    #[test]
    fn a_path_with_spaces_quotes_or_specifiers_stays_one_word() {
        let text = unit_text(Path::new("/home/me/My Apps/100% \"rika\"/rika\\app")).unwrap();
        assert!(text.contains("ExecStart=\"/home/me/My Apps/100%% \\\"rika\\\"/rika\\\\app\" --background\n"));
        assert_eq!(unit_text(Path::new("relative/rika")), None);
        assert_eq!(unit_text(Path::new("/tmp/two\nlines")), None);
    }

    #[test]
    fn the_unit_folder_follows_xdg_config_home_then_home() {
        assert_eq!(unit_dir(Some("/cfg".into()), Some("/home/me".into())), Some(PathBuf::from("/cfg/systemd/user")));
        assert_eq!(unit_dir(Some("relative".into()), Some("/home/me".into())), Some(PathBuf::from("/home/me/.config/systemd/user")));
        assert_eq!(unit_dir(None, None), None);
    }

    #[test]
    fn an_appimage_is_started_by_its_own_file() {
        assert_eq!(program(None, Some("/apps/rika.AppImage".into()), Ok("/tmp/.mount_x/usr/bin/rika".into())), Some(PathBuf::from("/apps/rika.AppImage")));
        assert_eq!(program(None, None, Ok("/usr/bin/rika".into())), Some(PathBuf::from("/usr/bin/rika")));
    }

    #[test]
    fn a_named_launcher_is_started_instead_of_the_appimage_it_wraps() {
        // NixOS runs the AppImage through appimage-run; only the launcher can start it at login.
        assert_eq!(program(Some("/home/me/.local/bin/kitty-workspace".into()), Some("/apps/rika.AppImage".into()), Ok("/x".into())),
                   Some(PathBuf::from("/home/me/.local/bin/kitty-workspace")));
        // The v0.3.41 launcher only said "1": that is not a path, so the AppImage stays.
        assert_eq!(program(Some("1".into()), Some("/apps/rika.AppImage".into()), Ok("/x".into())), Some(PathBuf::from("/apps/rika.AppImage")));
    }

    #[test]
    fn turning_it_on_writes_the_unit_and_its_link_and_off_removes_both() {
        let dir = std::env::temp_dir().join(format!("rika-autostart-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        assert!(!enabled(&dir));
        set(&dir, Path::new("/opt/rika/rika-workspace"), true).unwrap();
        assert!(enabled(&dir));
        assert_eq!(fs::read_link(dir.join(WANTS).join(UNIT)).unwrap(), dir.join(UNIT));
        // Again is fine (the link is replaced), and off leaves nothing behind.
        set(&dir, Path::new("/opt/rika/rika-workspace"), true).unwrap();
        set(&dir, Path::new("/opt/rika/rika-workspace"), false).unwrap();
        assert!(!enabled(&dir) && !dir.join(UNIT).exists() && !dir.join(WANTS).join(UNIT).exists());
        set(&dir, Path::new("/opt/rika/rika-workspace"), false).unwrap();
        let _ = fs::remove_dir_all(&dir);
    }
}
