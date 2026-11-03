use std::path::{Component, Path, PathBuf};

#[derive(Debug, PartialEq, Eq)]
pub enum Command {
    CheckUpdates,
    NewQuickNote,
    KeepRunning(bool),
    Quit,
    /// One agent skill's SKILL.md for the desktop opener (kitty.web_agent_skills).
    OpenSkill(PathBuf),
}

/// Accept only the page's fixed command envelope. The one argument is `open-skill`'s
/// path: absolute, no `.`/`..` components, and a file named exactly SKILL.md.
pub fn parse(line: &str) -> Option<Command> {
    let value: serde_json::Value = serde_json::from_str(line).ok()?;
    let object = value.as_object()?;
    let command = object.get("command")?.as_str()?;
    if command == "open-skill" {
        if object.len() != 2 { return None; }
        return skill_file(object.get("path")?.as_str()?).map(Command::OpenSkill);
    }
    if object.len() != 1 { return None; }
    match command {
        "check-updates" => Some(Command::CheckUpdates),
        "new-quick-note" => Some(Command::NewQuickNote),
        "keep-running-on" => Some(Command::KeepRunning(true)),
        "keep-running-off" => Some(Command::KeepRunning(false)),
        "quit" => Some(Command::Quit),
        _ => None,
    }
}

fn skill_file(text: &str) -> Option<PathBuf> {
    let path = Path::new(text);
    let plain = path.components().all(|part| matches!(part, Component::RootDir | Component::Prefix(_) | Component::Normal(_)));
    (path.is_absolute() && plain && !text.contains('\0') && path.file_name()? == "SKILL.md").then(|| path.to_path_buf())
}

pub fn state_line(keep_running: bool, keep_running_available: bool) -> String {
    let mut line = serde_json::json!({
        "keep_running": keep_running,
        "keep_running_available": keep_running_available,
    }).to_string();
    line.push('\n');
    line
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_settings_desktop_command_is_recognised() {
        assert_eq!(parse(r#"{"command":"check-updates"}"#), Some(Command::CheckUpdates));
        assert_eq!(parse(r#"{"command": "new-quick-note"}"#), Some(Command::NewQuickNote));
        assert_eq!(parse(r#"{"command":"keep-running-on"}"#), Some(Command::KeepRunning(true)));
        assert_eq!(parse(r#"{"command":"keep-running-off"}"#), Some(Command::KeepRunning(false)));
        assert_eq!(parse("{\"command\":\"quit\"}\n"), Some(Command::Quit));
    }

    #[test]
    fn anything_outside_the_fixed_allowlist_is_ignored() {
        for line in ["", "quit", "\"quit\"", "[\"quit\"]", r#"{"origin":"http://127.0.0.1:1"}"#,
                     r#"{"command":"open-url"}"#, r#"{"command":"QUIT"}"#, r#"{"command":" quit"}"#,
                     r#"{"command":"quit","url":"https://example.invalid"}"#, r#"{"command":["quit"]}"#,
                     r#"{"command":null}"#, "Traceback (most recent call last):",
                     r#"{"command":"open-skill"}"#, r#"{"command":"open-skill","path":"SKILL.md"}"#,
                     r#"{"command":"open-skill","path":"/home/me/.omp/agent/skills/x/notes.md"}"#,
                     r#"{"command":"open-skill","path":"/home/me/.omp/agent/skills/../../.ssh/SKILL.md"}"#,
                     r#"{"command":"open-skill","path":"https://example.invalid/SKILL.md"}"#,
                     r#"{"command":"open-skill","path":"/a/SKILL.md","extra":1}"#,
                     r#"{"command":"quit","path":"/a/SKILL.md"}"#] {
            assert_eq!(parse(line), None, "{line}");
        }
    }

    #[test]
    fn open_skill_carries_one_absolute_skill_file() {
        assert_eq!(parse(r#"{"command":"open-skill","path":"/home/me/.omp/agent/skills/lavish/SKILL.md"}"#),
                   Some(Command::OpenSkill(PathBuf::from("/home/me/.omp/agent/skills/lavish/SKILL.md"))));
    }

    #[test]
    fn state_report_is_one_exact_json_line() {
        let line = state_line(true, false);
        assert!(line.ends_with('\n') && !line[..line.len() - 1].contains('\n'));
        let value: serde_json::Value = serde_json::from_str(&line).unwrap();
        assert_eq!(value, serde_json::json!({"keep_running": true, "keep_running_available": false}));
    }
}
