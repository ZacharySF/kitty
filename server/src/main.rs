//! `rika-server`: rika!'s local website, fronting the Python backend while routes move to Rust.
//!
//! The same command line and announcement as `kitty-web` (`kitty.web_server.main`): it serves
//! the built UI on 127.0.0.1, prints a link with a one-time code (a fresh one whenever the
//! current link is used or expires), and starts `kitty-web --worker` behind itself for every
//! route not ported yet, after migrating the workspace database itself (crate::startup).
//! Quitting (Ctrl+C, SIGTERM) stops the worker cleanly; a worker that dies stops the front.
//!
//! `--desktop` is the desktop shell's mode (desktop/src/main.rs): no link, the readiness line
//! `{"origin", "secret"}` on stdout instead, and the shell's private owner pipe relayed to the
//! desktop backend behind it. The shell names the workspace with `--root` (to migrate it) and
//! the worker arguments are passed through unchanged.

mod auth;
mod backlinks;
mod blocks;
mod bootstrap;
mod builds;
mod calendar;
mod goals;
mod quick_notes;
mod study;
mod opportunities;
mod schema;
mod effects;
mod flashcards;
mod front;
mod keys;
mod live;
mod math;
mod migrations;
mod modules;
mod packs;
mod notes;
mod ported;
mod private;
mod search;
mod settings;
mod startup;
mod tags;
mod target;
mod terminal;
mod agents;
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

async fn run(options: Options) -> Result<(), String> {
    const UNAVAILABLE: &str = "Unable to open the local website. Check the port, workspace configuration and public example files with the CLI.";
    // Installed first: a stop asked for during start-up still removes a demo workspace and
    // drains the worker, instead of ending the process at once.
    let stopped = private::stop_requested();
    let listener = TcpListener::bind(("127.0.0.1", options.port)).await.map_err(|_| UNAVAILABLE.to_owned())?;
    let port = listener.local_addr().map_err(|_| UNAVAILABLE.to_owned())?.port();
    let origin = format!("http://127.0.0.1:{port}");
    let static_dir = std::fs::canonicalize(&options.static_dir).unwrap_or(options.static_dir.clone());

    if options.desktop && options.root.is_none() {
        return Err(format!("--desktop needs --root, the workspace the shell opens\n{USAGE}"));
    }
    // The workspace database is migrated here, before the worker opens it (crate::startup).
    // A start-up failure ends with its reason as the last line on stderr: the desktop shell
    // shows that line to the user (desktop/src/main.rs `startup_failure`).
    let failed = |reason: &dyn std::fmt::Display| format!("{UNAVAILABLE}\nrika-server: {reason}");
    let database = startup::prepare(options.root.clone(), options.demo && !options.desktop, options.desktop, &options.launch)
        .await.map_err(|message| failed(&message))?;

    // kitty-web takes the front's options; the desktop backend's come whole from the shell.
    let mut flags = Vec::new();
    if !options.desktop {
        match &database.demo {
            Some(folder) => {
                if let Some(root) = &options.root {
                    flags.extend(["--root".to_owned(), root.display().to_string()]);
                }
                flags.extend(["--demo".to_owned(), "--demo-workspace".to_owned(), folder.path().display().to_string()]);
            }
            None => flags.extend(["--root".to_owned(), database.paths.root.display().to_string()]),
        }
        flags.extend(["--static-dir".to_owned(), static_dir.display().to_string()]);
        if options.safe {
            flags.push("--safe".into());
        }
    }
    // The installed packs' folders go to the worker (`$KITTY_PACKS`); it reports the ones its
    // own program carries, which the listing then shows as built in.
    let packs = packs::Packs::open(&static_dir);
    let mut worker = Worker::start(&options.launch, &origin, &flags, &packs.worker_env()).await.map_err(|error| failed(&error))?;
    let packs = packs.with_worker(std::mem::take(&mut worker.packs.built_in));
    if let Err(message) = database.opened_by_worker(&worker.db_path) {
        worker.stop(WORKER_GRACE).await;
        return Err(failed(&message));
    }
    let front = Arc::new(Front {
        authority: format!("127.0.0.1:{port}"),
        origin: origin.clone(),
        port,
        auth: auth::UiAuth::new(),
        packs: Arc::new(packs),
        static_dir,
        backend: Backend {
            authority: worker.authority.clone(),
            origin: worker.origin.clone(),
            secret: worker.secret.clone(),
            front_secret: worker.front_secret.clone(),
        },
        workspace: worker.workspace.take().expect("a started worker reports its workspace"),
        bootstrap: bootstrap::Bootstrap::new(database.paths.db_path.clone(), database.paths.root.clone(),
                                             database.demo.is_some(), worker.digest),
        csp: front::csp(&worker.frame_ports),
    });
    keys::start(database.paths.root.clone(), database.demo.is_some());
    effects::start(database.paths.db_path.clone(), worker.ui_session.clone());
    math::start(worker.math_textbook.take());
    terminal::start(worker.terminal.take());

    live::start(&front, database.paths.db_path.clone());
    builds::service::start(front.clone(), database.paths.db_path.clone(), database.paths.root.clone(), options.safe || options.launch.args.iter().any(|arg| arg == "--safe"));
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
        () = stopped => Ok(()),
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
    // Hidden and test-only: the bootstrap digest on one database, for tests/test_web_bootstrap.py.
    if std::env::args().nth(1).as_deref() == Some("bootstrap-digest") {
        std::process::exit(bootstrap::digest_command(&std::env::args().skip(2).collect::<Vec<_>>()));
    }
    // Hidden and test-only: FSRS reviews on stdin, for tests/test_flashcard_scheduling_parity.py.
    if std::env::args().nth(1).as_deref() == Some("fsrs-review") {
        std::process::exit(flashcards::scheduler::review_command());
    }
    // Hidden and test-only: the search index on one database, for tests/test_search_parity.py.
    match std::env::args().nth(1).as_deref() {
        Some("search") => std::process::exit(search::search_command(&std::env::args().skip(2).collect::<Vec<_>>())),
        Some("search-sync") => std::process::exit(search::sync_command(&std::env::args().skip(2).collect::<Vec<_>>())),
        _ => {}
    }
    // Hidden and test-only: the backlinks' text rules on stdin, for tests/test_backlinks_parity.py.
    if std::env::args().nth(1).as_deref() == Some("backlinks-text") {
        std::process::exit(backlinks::text_command());
    }
    // Hidden and test-only: the block engine's text rules on stdin, for tests/test_blocks_text.py.
    if std::env::args().nth(1).as_deref() == Some("blocks-text") {
        std::process::exit(blocks::text::text_command());
    }
    // Hidden and test-only: math generation and checks on stdin, for tests/test_math_parity.py.
    if std::env::args().nth(1).as_deref() == Some("math") {
        std::process::exit(math::test_command());
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
