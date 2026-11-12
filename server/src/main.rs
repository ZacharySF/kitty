//! `rika-server`: rika!'s local website, fronting the Python backend while routes move to Rust.
//!
//! The same command line and announcement as `kitty-web` (`kitty.web_server.main`): it serves
//! the built UI on 127.0.0.1, prints a link with a one-time code (a fresh one whenever the
//! current link is used or expires), and starts `kitty-web --worker` behind itself for every
//! route not ported yet. Quitting (Ctrl+C, SIGTERM) stops the worker cleanly; a worker that
//! dies stops the front.
//!
//! `--desktop` is the desktop shell's mode (desktop/src/main.rs): no link, the readiness line
//! `{"origin", "secret"}` on stdout instead, and the shell's private owner pipe relayed to the
//! desktop backend behind it. The worker arguments are passed through unchanged.

mod auth;
mod builds;
mod front;
mod migrations;
mod modules;
mod settings;
mod target;
mod worker;

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper_util::rt::{TokioIo, TokioTimer};
use tokio::net::TcpListener;

use front::{Backend, Front};
use worker::{Launch, Worker};

const USAGE: &str = "usage: rika-server [--port N] [--root DIR] [--static-dir DIR] [--demo] [--safe] [--desktop] [--worker-program PATH] [--worker-arg ARG]...";
/// How long a quitting front waits for the worker to drain its accepted writes.
const WORKER_GRACE: Duration = Duration::from_secs(30);

struct Options {
    port: u16,
    root: Option<PathBuf>,
    static_dir: PathBuf,
    demo: bool,
    safe: bool,
    desktop: bool,
    launch: Launch,
}

fn options() -> Result<Options, String> {
    let mut args = std::env::args().skip(1);
    let mut options = Options {
        port: 8765,
        root: None,
        static_dir: PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../web/dist")),
        demo: false,
        safe: false,
        desktop: false,
        launch: Launch { program: "kitty-web".into(), args: Vec::new() },
    };
    while let Some(flag) = args.next() {
        let mut value = || args.next().ok_or_else(|| format!("{flag} needs a value\n{USAGE}"));
        match flag.as_str() {
            "--port" => options.port = value()?.parse().map_err(|_| "--port must be between 0 and 65535".to_owned())?,
            "--root" => options.root = Some(PathBuf::from(value()?)),
            "--static-dir" => options.static_dir = PathBuf::from(value()?),
            "--demo" => options.demo = true,
            "--safe" => options.safe = true,
            "--desktop" => options.desktop = true,
            "--worker-program" => options.launch.program = value()?,
            "--worker-arg" => options.launch.args.push(value()?),
            "-h" | "--help" => return Err(USAGE.into()),
            other => return Err(format!("unknown argument {other}\n{USAGE}")),
        }
    }
    Ok(options)
}

/// Print a fresh link whenever the current one is used or expires (`_announce_links`).
async fn announce_links(front: Arc<Front>, mut code: String) {
    loop {
        front.auth.wait_code(&code).await;
        code = front.auth.issue_code();
        println!("rika! next link (one browser tab each, works once within {} minutes): {}/?code={code}",
                 auth::CODE_TTL.as_secs() / 60, front.origin);
    }
}

async fn stopped() {
    let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).expect("SIGTERM handler");
    tokio::select! {
        _ = tokio::signal::ctrl_c() => {}
        _ = terminate.recv() => {}
    }
}

async fn run(options: Options) -> Result<(), String> {
    const UNAVAILABLE: &str = "Unable to open the local website. Check the port, workspace configuration and public example files with the CLI.";
    let listener = TcpListener::bind(("127.0.0.1", options.port)).await.map_err(|_| UNAVAILABLE.to_owned())?;
    let port = listener.local_addr().map_err(|_| UNAVAILABLE.to_owned())?.port();
    let origin = format!("http://127.0.0.1:{port}");
    let static_dir = std::fs::canonicalize(&options.static_dir).unwrap_or(options.static_dir.clone());

    // kitty-web takes the front's options; the desktop backend's come whole from the shell.
    let mut flags = Vec::new();
    if !options.desktop {
        if let Some(root) = &options.root {
            flags.extend(["--root".to_owned(), root.display().to_string()]);
        }
        flags.extend(["--static-dir".to_owned(), static_dir.display().to_string()]);
        if options.demo {
            flags.push("--demo".into());
        }
        if options.safe {
            flags.push("--safe".into());
        }
    }
    let mut worker = Worker::start(&options.launch, &origin, &flags).await.map_err(|error| {
        eprintln!("rika-server: {error}");
        UNAVAILABLE.to_owned()
    })?;
    let front = Arc::new(Front {
        authority: format!("127.0.0.1:{port}"),
        origin: origin.clone(),
        port,
        auth: auth::UiAuth::new(),
        static_dir,
        backend: Backend {
            authority: worker.authority.clone(),
            origin: worker.origin.clone(),
            secret: worker.secret.clone(),
            front_secret: worker.front_secret.clone(),
        },
        workspace: worker.workspace.take().expect("a started worker reports its workspace"),
    });

    if options.desktop {
        // The shell's readiness contract (desktop/src/owner.rs `readiness`): it injects this
        // secret into rika!'s own windows. This pipe is private to the shell.
        println!("{}", serde_json::json!({ "origin": origin, "secret": front.auth.secret() }));
        worker.relay_owner_pipe();
    } else {
        worker.drain_output();
        let code = front.auth.issue_code();
        println!("rika! {}website: {origin}/?code={code}", if options.demo { "synthetic demo " } else { "" });
        tokio::spawn(announce_links(front.clone(), code));
    }

    let serving = async {
        loop {
            let Ok((stream, peer)) = listener.accept().await else { continue };
            let front = front.clone();
            tokio::spawn(async move {
                let service = service_fn(move |request| front.clone().handle(peer.port(), request));
                let _ = http1::Builder::new()
                    .timer(TokioTimer::new())
                    // Python's handler gives a client 10 s per request too.
                    .header_read_timeout(Duration::from_secs(10))
                    .serve_connection(TokioIo::new(stream), service)
                    .with_upgrades()
                    .await;
            });
        }
    };
    let outcome = tokio::select! {
        () = serving => Ok(()),
        () = stopped() => Ok(()),
        // The shell's quit reaches the worker through the owner pipe: a clean exit ends the front too.
        status = worker.exited() => match status {
            Ok(status) if status.success() => Ok(()),
            status => Err(format!("rika-server: the Python backend stopped ({})",
                                  status.map(|s| s.to_string()).unwrap_or_else(|e| e.to_string()))),
        },
    };
    worker.stop(WORKER_GRACE).await;
    outcome
}

#[tokio::main]
async fn main() {
    // Hidden and test-only: the build reader on stdin, for tests/test_build_reader_parity.py.
    if std::env::args().nth(1).as_deref() == Some("check-build") {
        std::process::exit(builds::check_build_command());
    }
    // Hidden and test-only: the migration runner on one database, for tests/test_migration_parity.py.
    if std::env::args().nth(1).as_deref() == Some("migrate") {
        std::process::exit(migrations::migrate_command(&std::env::args().skip(2).collect::<Vec<_>>()));
    }
    let options = match options() {
        Ok(options) => options,
        Err(message) => {
            eprintln!("{message}");
            std::process::exit(2);
        }
    };
    if let Err(message) = run(options).await {
        eprintln!("{message}");
        std::process::exit(1);
    }
    // The worker has stopped and nothing is left to drain. Exit now: returning would make the
    // runtime wait for the blocking stdin reader (the owner-pipe relay), which only ends when
    // the shell closes our stdin, and a `quit` does not.
    use std::io::Write;
    let _ = std::io::stdout().flush();
    std::process::exit(0);
}
