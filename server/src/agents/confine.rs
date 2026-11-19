//! `agent_sessions.host_guarantee`: what this kernel lets an agent launcher close (the paper's
//! kernel matrix for this machine), as `kitty.core.confine` probes it. The launcher itself and
//! the checks of its exchange stay in the worker (`kitty.agent_launch`, `agent_sessions.Launches`).

use serde_json::{json, Value};

/// `landlock_create_ruleset` (the same number on x86_64 and aarch64).
const SYS_CREATE_RULESET: libc::c_long = 444;
const CREATE_RULESET_VERSION: u32 = 1;

/// `confine.REQUIRED` with `confine._OPEN`'s reason for each route left open.
const REQUIRED: [(&str, &str); 7] = [
    ("files", "the database, rika!'s private files, configuration and resumes can be opened directly"),
    ("ptrace", "rika! can be traced"),
    ("tcp", "any TCP port can be reached"),
    ("abstract-unix-sockets", "abstract UNIX sockets (X11, D-Bus) outside the tab can be reached"),
    ("signals", "rika! and other processes outside the tab can be signalled"),
    ("unix-sockets", "pathname UNIX sockets (herdr's and Tern's, which open unconfined panes; D-Bus, systemd-run, \
                      Wayland, X11) can be reached: Landlock closes them from ABI 9 (Linux 7.1)"),
    ("tiocsti", "input can be pushed into the terminal (TIOCSTI/TIOCLINUX)"),
];

/// `platform.uname()`: system, release and machine.
fn uname() -> (String, String, String) {
    let mut names: libc::utsname = unsafe { std::mem::zeroed() };
    if unsafe { libc::uname(&mut names) } != 0 {
        return (String::new(), String::new(), String::new());
    }
    let text = |field: &[libc::c_char]| {
        let bytes: Vec<u8> = field.iter().take_while(|&&c| c != 0).map(|&c| c as u8).collect();
        String::from_utf8_lossy(&bytes).into_owned()
    };
    (text(&names.sysname), text(&names.release), text(&names.machine))
}

/// `confine.abi`: the highest Landlock ABI this kernel offers; 0 when it is missing or disabled.
fn abi(system: &str) -> i64 {
    if system != "Linux" {
        return 0;
    }
    let version = unsafe { libc::syscall(SYS_CREATE_RULESET, std::ptr::null::<libc::c_void>(), 0usize, CREATE_RULESET_VERSION) };
    if version > 0 { version as i64 } else { 0 }
}

/// `confine.closures`: the routes a Landlock domain of `version` closes.
fn closures(version: i64) -> Vec<&'static str> {
    let mut found = if version >= 1 { vec!["files", "ptrace"] } else { Vec::new() };
    if version >= 4 {
        found.push("tcp");
    }
    if version >= 5 {
        found.push("device-ioctl");
    }
    if version >= 6 {
        found.extend(["abstract-unix-sockets", "signals"]);
    }
    if version >= 9 {
        found.push("unix-sockets");
    }
    if version >= 10 {
        found.push("udp");
    }
    found
}

/// `agent_sessions.host_guarantee()`.
pub fn host() -> Value {
    let (system, release, machine) = uname();
    let version = abi(&system);
    let mut closed = closures(version);
    // `confine.seccomp_program()` builds a TIOCSTI filter for these machines only.
    if matches!(machine.to_lowercase().as_str(), "x86_64" | "amd64" | "aarch64" | "arm64") {
        closed.push("tiocsti");
    }
    if version == 0 {
        closed.clear();
    }
    let open: Vec<&str> = REQUIRED.iter().filter(|(route, _)| !closed.contains(route)).map(|(_, reason)| *reason).collect();
    json!({"kernel": format!("{system} {release}"), "abi": version, "guarantee": if open.is_empty() { "full" } else { "attribution" },
           "closed": closed, "open": open})
}
