use std::{ffi::OsString, path::{Path, PathBuf}};

pub fn select(
    args: impl IntoIterator<Item = OsString>,
    kitty_home: Option<OsString>,
    xdg_data_home: Option<OsString>,
    home: Option<OsString>,
) -> Result<PathBuf, String> {
    let mut args = args.into_iter();
    let mut explicit = None;
    while let Some(argument) = args.next() {
        if argument == "--workspace" {
            if explicit.is_some() {
                return Err("--workspace must be supplied only once".into());
            }
            let root = PathBuf::from(args.next().ok_or("--workspace requires an absolute path")?);
            if !root.is_absolute() {
                return Err("--workspace requires a nonempty absolute path".into());
            }
            explicit = Some(root);
        }
    }
    if let Some(root) = explicit { return Ok(root); }
    if let Some(root) = kitty_home {
        if root.is_empty() { return Err("KITTY_HOME must not be empty".into()); }
        return Ok(PathBuf::from(root));
    }
    let data = match xdg_data_home {
        Some(path) if Path::new(&path).is_absolute() => PathBuf::from(path),
        _ => PathBuf::from(home.ok_or("HOME is unavailable")?).join(".local/share"),
    };
    Ok(data.join("kitty/workspace"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }

    #[test]
    fn installer_workspace_overrides_developer_environment() {
        assert_eq!(select(args(&["--workspace", "/selected/workspace"]), Some("/developer/checkout".into()), None, None).unwrap(), PathBuf::from("/selected/workspace"));
        assert_eq!(select(args(&["--workspace", "/selected/workspace"]), Some("".into()), None, None).unwrap(), PathBuf::from("/selected/workspace"));
    }

    #[test]
    fn explicit_workspace_must_be_unique_absolute_and_nonempty() {
        for values in [vec!["--workspace"], vec!["--workspace", ""], vec!["--workspace", "relative"], vec!["--workspace", "/one", "--workspace", "/two"]] {
            assert!(select(args(&values), Some("/fallback".into()), None, None).is_err(), "invalid arguments: {values:?}");
        }
    }

    #[test]
    fn absent_option_preserves_existing_environment_selection() {
        assert_eq!(select(args(&[]), Some("/existing".into()), None, None).unwrap(), PathBuf::from("/existing"));
        assert_eq!(select(args(&[]), None, Some("/data".into()), None).unwrap(), PathBuf::from("/data/kitty/workspace"));
        assert_eq!(select(args(&[]), None, Some("relative".into()), Some("/home/test".into())).unwrap(), PathBuf::from("/home/test/.local/share/kitty/workspace"));
        assert!(select(args(&[]), Some("".into()), None, Some("/home/test".into())).is_err());
    }
}
