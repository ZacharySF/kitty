//! The live build (`web_build.BuildService` before it moved here): the active build of this
//! front and the file watcher behind it.
//!
//! Whoever saves `config/build.json` (the build editor, a Settings screen of the worker, an
//! install or rollback, another rika! process, any text editor), a valid save is projected into
//! SQLite and pushed to every window over the live channel; a save with an error changes
//! nothing but the problems every window shows. The watcher polls the file's
//! `(mtime_ns, size, inode)` every 0.25 s and acts once it stood still for one tick; the routes
//! refresh after every write, and the live channel calls [`refresh`] whenever the worker
//! committed a mutation. When the active build changes, the worker hears it
//! (`POST /api/internal/build/applied`) so its `build_applied` hooks still run.
//!
//! Pushed to every window: `{"type": "build", "revision", "active_revision", "build", "text",
//! "problems", "invalid", "restart_required", "safe"}`.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::Duration;

use rusqlite::Connection;

use super::document::push_problem_json;
use super::failure::Failure;
use super::project::{self, Synced};
use super::store::Store;
use super::value::{dump, dump_str};
use crate::front::{Backend, Front};

/// `web_build.WATCH_SECONDS`.
const WATCH: Duration = Duration::from_millis(250);
pub const APPLIED_PATH: &str = "/api/internal/build/applied";

type Signature = Option<(i128, u64, u64)>;

pub struct Inner {
    conn: Option<Connection>,
    pub synced: Option<Synced>,
    signature: Signature,
}

impl Inner {
    /// The workspace database, opened once and kept (SQLite parses the schema once).
    pub fn conn(&mut self, service: &Service) -> Result<&mut Connection, Failure> {
        if self.conn.is_none() {
            self.conn = Some(project::open(&service.db_path)?);
        }
        Ok(self.conn.as_mut().expect("opened above"))
    }
}

pub struct Service {
    pub front: Arc<Front>,
    pub store: Store,
    db_path: PathBuf,
    pub safe: bool,
    /// Every sync and build write of this process, one at a time (Python's mutation lock and
    /// `BuildService._lock`).
    state: Mutex<Inner>,
    /// Keeps pushes and worker notices in the order the syncs happened.
    order: Mutex<()>,
    /// The active revision, readable while a sync waits on the database.
    active: Mutex<Option<String>>,
}

/// What a sync found that windows and the worker must hear.
struct Announcement {
    message: String,
    applied: Option<String>,
}

static SERVICE: OnceLock<Service> = OnceLock::new();

pub fn get() -> Option<&'static Service> {
    SERVICE.get()
}

/// Start the build service of this front: sync now, then watch the file.
pub fn start(front: Arc<Front>, db_path: PathBuf, root: PathBuf, safe: bool) {
    let store = Store { root, version: front.workspace.version.clone() };
    let signature = store.signature();
    let service = Service {
        front,
        store,
        db_path,
        safe,
        state: Mutex::new(Inner { conn: None, synced: None, signature }),
        order: Mutex::new(()),
        active: Mutex::new(None),
    };
    if SERVICE.set(service).is_err() {
        return;
    }
    let service = get().expect("set above");
    // As `BuildService.__init__`: the first sync before the front serves; a failure (a busy
    // database) leaves it to the first refresh.
    let _ = tokio::task::block_in_place(|| {
        let mut inner = service.lock();
        let synced = project::sync(inner.conn(service)?, &service.store)?;
        *service.active.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(synced.active_revision.clone());
        inner.synced = Some(synced);
        Ok::<(), Failure>(())
    });
    std::thread::Builder::new()
        .name("rika-build-watch".into())
        .spawn(move || service.watch(signature))
        .expect("a thread for the build watcher");
}

/// The active build's revision (the live hello's `build`), None before the service started.
pub fn active_revision() -> Option<String> {
    let service = get()?;
    service.active.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).clone()
}

/// Pick up a changed build file (blocking): project it when valid, push it to every window.
pub fn refresh() {
    if let Some(service) = get() {
        let _ = service.refresh();
    }
}

impl Service {
    pub fn lock(&self) -> MutexGuard<'_, Inner> {
        self.state.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn watch(&self, mut seen: Signature) {
        // An editor may write a file in pieces: act once it has stood still for one tick.
        loop {
            std::thread::sleep(WATCH);
            let current = self.store.signature();
            if current == seen {
                let _ = self.refresh(); // a bad save must never stop watching
            }
            seen = current;
        }
    }

    /// `BuildService.refresh`.
    pub fn refresh(&self) -> Result<(), Failure> {
        let mut inner = self.lock();
        let announcement = self.sync(&mut inner)?;
        let order = self.order.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        drop(inner);
        if let Some(announcement) = announcement {
            crate::live::broadcast_text(announcement.message);
            if let Some(build) = announcement.applied {
                // The worker's `build_applied` hooks (web_opportunities' monitoring schedule).
                let _ = post_internal(&self.front.backend, APPLIED_PATH, &format!("{{\"build\":{build}}}"));
            }
        }
        drop(order);
        Ok(())
    }

    fn sync(&self, inner: &mut Inner) -> Result<Option<Announcement>, Failure> {
        let signature = self.store.signature();
        if inner.synced.is_some() && signature == inner.signature {
            return Ok(None);
        }
        let before = inner.signature;
        inner.signature = signature;
        let synced = match project::sync(inner.conn(self)?, &self.store) {
            Ok(synced) => synced,
            Err(failure) => {
                // Unlike Python, a failed sync (a busy database) is retried at the next tick.
                inner.signature = before;
                return Err(failure);
            }
        };
        *self.active.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(synced.active_revision.clone());
        let previous = inner.synced.replace(synced);
        let synced = inner.synced.as_ref().expect("set above");
        if previous.as_ref().is_some_and(|p| p.revision == synced.revision && p.active_revision == synced.active_revision) {
            return Ok(None);
        }
        let applied = previous.as_ref().is_none_or(|p| p.active_revision != synced.active_revision).then(|| {
            let mut build = String::new();
            dump(&synced.build, &mut build);
            build
        });
        let mut message = String::from("{\"type\":\"build\",");
        self.public(synced, &mut message)?;
        message.push('}');
        // The first sync at start-up only sets the state: the worker applied that build itself.
        Ok(previous.map(|_| Announcement { message, applied }))
    }

    /// `BuildService.public`'s fields, without the braces.
    pub fn public(&self, synced: &Synced, out: &mut String) -> Result<(), Failure> {
        let modules = self.front.workspace.state().map_err(|failure| Failure::new(failure.status, failure.message))?;
        let restart = modules.get("restart_required").and_then(serde_json::Value::as_bool).unwrap_or(false);
        out.push_str(&format!("\"revision\":\"{}\",\"active_revision\":", synced.revision));
        dump_str(&super::value::text(&synced.active_revision), out);
        out.push_str(",\"build\":");
        dump(&synced.build, out);
        out.push_str(",\"text\":");
        dump_str(&synced.text, out);
        out.push_str(",\"problems\":[");
        for (i, problem) in synced.problems.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            push_problem_json(problem, out);
        }
        out.push_str(&format!("],\"invalid\":{},\"restart_required\":{restart},\"safe\":{}", synced.invalid(), self.safe));
        Ok(())
    }
}

/// One front → worker internal call, as a forwarded mutation reaches it (its Host, secrets,
/// `X-Kitty-Request`, JSON, one Content-Length); returns the status.
fn post_internal(backend: &Backend, path: &str, body: &str) -> std::io::Result<u16> {
    let mut stream = TcpStream::connect(&backend.authority)?;
    stream.set_read_timeout(Some(Duration::from_secs(60)))?;
    let head = format!(
        "POST {path} HTTP/1.1\r\nHost: {}\r\nX-Kitty-Secret: {}\r\nX-Kitty-Front: {}\r\nX-Kitty-Request: 1\r\n\
         Content-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        backend.authority, backend.secret, backend.front_secret, body.len()
    );
    stream.write_all(head.as_bytes())?;
    stream.write_all(body.as_bytes())?;
    let mut answer = Vec::new();
    stream.read_to_end(&mut answer)?;
    let status = std::str::from_utf8(&answer).ok().and_then(|text| text.split(' ').nth(1)).and_then(|code| code.parse().ok());
    status.ok_or_else(|| std::io::Error::other("the worker's answer has no status"))
}

#[cfg(test)]
mod tests {
    use std::io::{BufRead, BufReader};
    use std::net::TcpListener;

    use super::*;

    /// The worker sees the front's call as a forwarded mutation: its Host, both secrets,
    /// `X-Kitty-Request`, JSON and one Content-Length.
    #[test]
    fn the_worker_hears_the_front_as_a_mutation() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let authority = listener.local_addr().unwrap().to_string();
        let backend = Backend { authority: authority.clone(), origin: format!("http://{authority}"), secret: "s".repeat(64), front_secret: "f".repeat(64) };
        let worker = std::thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut head = Vec::new();
            loop {
                let mut line = String::new();
                reader.read_line(&mut line).unwrap();
                if line == "\r\n" {
                    break;
                }
                head.push(line.trim_end().to_owned());
            }
            let mut body = vec![0; 12];
            reader.read_exact(&mut body).unwrap();
            (&stream).write_all(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n{}").unwrap();
            (head, body)
        });
        assert_eq!(post_internal(&backend, APPLIED_PATH, "{\"build\":{}}").unwrap(), 200);
        let (head, body) = worker.join().unwrap();
        assert_eq!(head[0], "POST /api/internal/build/applied HTTP/1.1");
        for expected in [format!("Host: {authority}"), format!("X-Kitty-Secret: {}", "s".repeat(64)),
                         format!("X-Kitty-Front: {}", "f".repeat(64)), "X-Kitty-Request: 1".into(),
                         "Content-Type: application/json".into(), "Content-Length: 12".into()] {
            assert!(head.contains(&expected), "{expected} in {head:?}");
        }
        assert_eq!(body, b"{\"build\":{}}");
    }
}
