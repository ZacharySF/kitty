//! The Python backend behind the front (`kitty-web --worker`, or the desktop backend with
//! `--worker`).
//!
//! The worker binds a port of the system's choice on 127.0.0.1, reads the front's private
//! secret and this launch's window session as the first two lines on stdin, and answers only
//! requests that carry the secret
//! (`web_server.RequestHandler._front`). Its readiness line, the first line on stdout, is
//! `{"origin", "secret", "config_dir", "db_path", "modules", "version", "digest"}`
//! (`web_server.readiness`): its own origin and UI secret, which only the front ever sees, and
//! the workspace it opened with its frozen module snapshot and, with ApplyAI, the digest
//! settings it validated, which the routes answered by the front need.
//! Closing its stdin stops it, so it never outlives the front.

use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use serde::Deserialize;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::Mutex;

/// How long the worker may take to open the workspace (the desktop shell allows the same).
pub const READY_TIMEOUT: Duration = Duration::from_secs(90);

/// The running worker: where to reach it and the secrets it requires.
pub struct Worker {
    /// `127.0.0.1:<port>`, the Host header it expects.
    pub authority: String,
    /// `http://127.0.0.1:<port>`, the Origin header it expects.
    pub origin: String,
    /// The worker's own UI secret.
    pub secret: String,
    /// The secret the front sends on every request it forwards.
    pub front_secret: String,
    /// This launch's window session (`"ui-" + token_hex(12)`), the worker's `app.ui_session`:
    /// effect approvals made by the front (crate::effects) and by the worker's inline views
    /// are bound to it.
    pub ui_session: String,
    /// The workspace the worker opened (`Settings.config_dir`), its active modules and version;
    /// the front takes it once.
    pub workspace: Option<crate::modules::Workspace>,
    /// The database the worker opened.
    pub db_path: std::path::PathBuf,
    /// `config.apply`'s digest settings, while ApplyAI is active.
    pub digest: Option<crate::bootstrap::DigestSettings>,
    /// The packs the worker has and lacks (`kitty.packs.readiness`).
    pub packs: WorkerPacks,
    /// The loopback ports of the worker's framed external-module servers, fixed at its start-up
    /// (`kitty.web_external_modules`); the front's Content-Security-Policy frames them.
    pub frame_ports: Vec<u16>,
    /// The folder of the math textbook's `exercises.json` (`kitty.core.math.textbook.directory()`).
    pub math_textbook: Option<std::path::PathBuf>,
    child: Child,
    /// Held open for the worker's lifetime; closing it tells the worker to stop. Shared with
    /// the desktop owner-pipe relay, which writes the shell's lines into it.
    stdin: Arc<Mutex<Option<ChildStdin>>>,
    /// The worker's stdout after its readiness line, until drained or relayed.
    output: Option<BufReader<ChildStdout>>,
}

#[derive(Deserialize)]
struct Readiness {
    origin: String,
    secret: String,
    config_dir: String,
    db_path: String,
    modules: Vec<String>,
    version: String,
    digest: Option<crate::bootstrap::DigestSettings>,
    #[serde(default)]
    packs: WorkerPacks,
    #[serde(default)]
    frame_ports: Vec<u16>,
    #[serde(default)]
    math_textbook: Option<String>,
}

/// The readiness line's `packs`: those built into the worker program, and those its active
/// modules need but it lacks.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct WorkerPacks {
    #[serde(default)]
    pub built_in: Vec<String>,
    #[serde(default)]
    pub missing: Vec<String>,
}

/// How to start the worker: a program and its arguments before rika!'s own flags.
#[derive(Debug, Clone)]
pub struct Launch {
    pub program: String,
    pub args: Vec<String>,
}

#[derive(Debug)]
pub struct WorkerError(pub String);

impl std::fmt::Display for WorkerError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for WorkerError {}

fn loopback_port(origin: &str) -> Option<u16> {
    let port = origin.strip_prefix("http://127.0.0.1:")?;
    (!port.starts_with('0') && port.bytes().all(|b| b.is_ascii_digit())).then(|| port.parse().ok()).flatten()
}

impl Worker {
    /// Start the worker for the front at `public_origin` and wait for its readiness line.
    /// `packs` is `$KITTY_PACKS`, the installed packs' folders (crate::packs `worker_env`).
    pub async fn start(launch: &Launch, public_origin: &str, flags: &[String], packs: &str) -> Result<Self, WorkerError> {
        let front_secret = crate::auth::token_hex(32);
        let ui_session = format!("ui-{}", crate::auth::token_hex(12));
        let mut child = Command::new(&launch.program)
            .args(&launch.args)
            .arg("--worker")
            .arg("--public-origin")
            .arg(public_origin)
            .args(flags)
            .env("KITTY_PACKS", packs)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .kill_on_drop(true)
            .spawn()
            .map_err(|error| WorkerError(format!("could not start the Python backend ({}): {error}", launch.program)))?;
        let mut stdin = child.stdin.take().expect("stdin is piped");
        stdin
            .write_all(format!("{front_secret}\n{ui_session}\n").as_bytes())
            .await
            // A closed input means the backend is gone: the same reason as an early exit, whichever
            // of the two the front notices first.
            .map_err(|_| WorkerError("the Python backend exited before becoming ready".into()))?;
        let mut stdout = BufReader::new(child.stdout.take().expect("stdout is piped"));
        let mut line = String::new();
        let read = tokio::time::timeout(READY_TIMEOUT, stdout.read_line(&mut line))
            .await
            .map_err(|_| WorkerError("the Python backend did not become ready in time".into()))?
            .map_err(|error| WorkerError(format!("reading the Python backend's readiness line: {error}")))?;
        if read == 0 {
            return Err(WorkerError("the Python backend exited before becoming ready".into()));
        }
        let ready: Readiness = serde_json::from_str(line.trim())
            .map_err(|_| WorkerError("the Python backend's readiness line is malformed".into()))?;
        let port = loopback_port(&ready.origin)
            .ok_or_else(|| WorkerError("the Python backend must listen on 127.0.0.1".into()))?;
        if ready.secret.len() != 64 || !ready.secret.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(WorkerError("the Python backend's secret is malformed".into()));
        }
        Ok(Self {
            authority: format!("127.0.0.1:{port}"),
            origin: ready.origin,
            secret: ready.secret,
            front_secret,
            ui_session,
            workspace: Some(crate::modules::Workspace {
                config_dir: ready.config_dir.into(),
                active: ready.modules,
                version: ready.version,
                missing: ready.packs.missing.clone(),
            }),
            db_path: ready.db_path.into(),
            digest: ready.digest,
            packs: ready.packs,
            frame_ports: ready.frame_ports,
            math_textbook: ready.math_textbook.map(Into::into),
            child,
            stdin: Arc::new(Mutex::new(Some(stdin))),
            output: Some(stdout),
        })
    }

    /// Browser mode: anything the worker prints later is not for the page; keep the pipe from filling.
    pub fn drain_output(&mut self) {
        if let Some(mut output) = self.output.take() {
            tokio::spawn(async move {
                let mut sink = Vec::new();
                let _ = output.read_to_end(&mut sink).await;
            });
        }
    }

    /// Desktop mode: the front stands in the shell's private owner pipe (desktop/src/owner.rs).
    /// The worker's lines after readiness (its fixed commands: check-updates, new-quick-note,
    /// ...) go to the front's stdout for the shell to parse; the shell's lines on the front's
    /// stdin (`quit`, keep-running reports) go to the worker. The shell closing the front's
    /// stdin closes the worker's, which stops it.
    pub fn relay_owner_pipe(&mut self) {
        if let Some(output) = self.output.take() {
            tokio::spawn(async move {
                let mut lines = output.lines();
                let mut shell = tokio::io::stdout();
                while let Ok(Some(line)) = lines.next_line().await {
                    if shell.write_all(format!("{line}\n").as_bytes()).await.is_err() || shell.flush().await.is_err() {
                        break;
                    }
                }
            });
        }
        let input = self.stdin.clone();
        tokio::spawn(async move {
            let mut lines = BufReader::new(tokio::io::stdin()).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let mut guard = input.lock().await;
                let Some(worker) = guard.as_mut() else { return };
                if worker.write_all(format!("{line}\n").as_bytes()).await.is_err() {
                    return;
                }
            }
            input.lock().await.take();
        });
    }

    /// Wait until the worker exits by itself (it crashed, failed, or the shell said quit).
    pub async fn exited(&mut self) -> std::io::Result<std::process::ExitStatus> {
        self.child.wait().await
    }

    /// Ask the worker to stop (close its stdin) and wait up to `grace` for it to drain.
    pub async fn stop(mut self, grace: Duration) {
        self.stdin.lock().await.take();
        if tokio::time::timeout(grace, self.child.wait()).await.is_err() {
            let _ = self.child.kill().await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_plain_loopback_origin_is_accepted() {
        assert_eq!(loopback_port("http://127.0.0.1:8765"), Some(8765));
        for origin in ["http://localhost:8765", "http://127.0.0.1", "http://127.0.0.1:08765",
                       "http://127.0.0.1:99999", "https://127.0.0.1:1", "http://127.0.0.1:1/"] {
            assert_eq!(loopback_port(origin), None, "{origin}");
        }
    }
}
