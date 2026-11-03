use std::path::{Component, Path, PathBuf};

use url::Url;

/// The global the web page reads the UI secret from (`web/src/lib/api.ts`).
pub const SECRET_GLOBAL: &str = "__KITTY_UI_SECRET__";

/// The sidecar's one readiness line, `{"origin", "secret"}` and nothing else: its origin
/// (checked by the caller) and this launch's 256-bit UI secret as 64 lowercase hex digits.
pub fn readiness(line: &str) -> Option<(String, String)> {
    let value: serde_json::Value = serde_json::from_str(line).ok()?;
    let object = value.as_object()?;
    if object.len() != 2 { return None; }
    let origin = object.get("origin")?.as_str()?;
    let secret = object.get("secret")?.as_str()?;
    let hex = secret.len() == 64 && secret.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
    hex.then(|| (origin.to_string(), secret.to_string()))
}

/// The initialisation script that hands the UI secret to one rika! window. Tauri runs it
/// only on the main frame (never the LearnHarness frame) before page scripts, on every
/// top-level navigation, so it also checks the page is on the server's exact origin.
pub fn window_script(origin: &Url, secret: &str) -> String {
    let origin = serde_json::Value::String(origin.origin().ascii_serialization());
    let secret = serde_json::Value::String(secret.to_string());
    format!("if (window.location.origin === {origin}) Object.defineProperty(window, {global}, {{ value: {secret} }});",
            global = serde_json::Value::String(SECRET_GLOBAL.into()))
}

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
                     r#"{"origin":"http://127.0.0.1:1","secret":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}"#,
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

    const SECRET: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    #[test]
    fn readiness_carries_exactly_the_origin_and_a_256_bit_secret() {
        let line = format!(r#"{{"origin":"http://127.0.0.1:43721","secret":"{SECRET}"}}"#);
        assert_eq!(readiness(&line), Some(("http://127.0.0.1:43721".into(), SECRET.into())));
        let short = &SECRET[..63];
        let upper = SECRET.to_uppercase();
        for bad in [
            r#"{"origin":"http://127.0.0.1:43721"}"#.to_string(),
            format!(r#"{{"secret":"{SECRET}"}}"#),
            format!(r#"{{"origin":"http://127.0.0.1:43721","secret":"{short}"}}"#),
            format!(r#"{{"origin":"http://127.0.0.1:43721","secret":"{SECRET}0"}}"#),
            format!(r#"{{"origin":"http://127.0.0.1:43721","secret":"{upper}"}}"#),
            format!(r#"{{"origin":"http://127.0.0.1:43721","secret":"{}g"}}"#, &SECRET[..63]),
            format!(r#"{{"origin":"http://127.0.0.1:43721","secret":"{SECRET}","extra":1}}"#),
            format!(r#"{{"origin":43721,"secret":"{SECRET}"}}"#),
            r#"{"origin":"http://127.0.0.1:43721","secret":null}"#.to_string(),
            "Traceback (most recent call last):".to_string(),
        ] {
            assert_eq!(readiness(&bad), None, "{bad}");
        }
    }

    #[test]
    fn window_script_defines_the_secret_only_on_the_exact_origin() {
        let origin = Url::parse("http://127.0.0.1:43721/").unwrap();
        let script = window_script(&origin, SECRET);
        assert_eq!(script, format!(
            "if (window.location.origin === \"http://127.0.0.1:43721\") Object.defineProperty(window, \"__KITTY_UI_SECRET__\", {{ value: \"{SECRET}\" }});"));
        // Quotes and script terminators in a value can never escape the string literal.
        let hostile = window_script(&origin, "\"); alert(1); (\"</script>");
        assert!(hostile.contains(r#"value: "\"); alert(1); (\"</script>""#), "{hostile}");
    }
}
