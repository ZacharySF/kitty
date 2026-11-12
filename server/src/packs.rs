//! Packs: a module's large payloads (docs/rust-core-design.md, milestone 3), downloaded when the
//! user asks instead of shipped in the base install.
//!
//! ```text
//! GET  /api/packs                  {version, index: {error?}, packs: [...]}
//! POST /api/packs/install   {id}   start (or join) a download; the listing back
//! POST /api/packs/uninstall {id}   remove an installed pack; the listing back
//! ```
//!
//! A release carries, beside its installers, each pack as `rika-pack-<id>-<version>.tar.gz` and
//! an index `packs.json` (scripts/build-packs.py) signed with the updater key as
//! `tauri signer sign` signs the installers (`packs.json.sig`, a base64 minisign signature whose
//! trusted comment names the file). The front fetches its own version's index over HTTPS,
//! verifies it against the updater public key compiled in from desktop/tauri.conf.json and
//! caches it. A download is streamed into a temporary directory and refused unless its size and
//! SHA-256 are the ones the signed index gives; it is unpacked there (regular files and
//! directories only, no path that leaves the pack), and becomes installed only when
//! `installed.json` names it: one atomic rename under the packs lock. A failure before that
//! leaves the previous install untouched, and leftovers are swept at the next start.
//!
//! A pack is tied to one build: its web chunks import the base's own hashed chunks, so only a
//! pack of exactly this version counts. A build that still carries a pack's files (a source
//! checkout's `web/dist`; `pack-files.json` lists them) has it built in.
//!
//! Under `$XDG_DATA_HOME/kitty/packs/`: `installed.json`, one `<id>-<version>-<random>/`
//! directory per installed pack (`pack.json`, `web/...`), the verified index cache
//! (`index.json`, `index.json.sig`), `.tmp-*` downloads in progress and `.lock`.

use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Component, Path, PathBuf};
use std::sync::{Arc, LazyLock, Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime};

use base64::Engine;
use minisign_verify::{PublicKey, Signature};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value as Json};
use sha2::{Digest, Sha256};

use crate::modules::{registry, Failure};

pub const PATH: &str = "/api/packs";
pub const INSTALL_PATH: &str = "/api/packs/install";
pub const UNINSTALL_PATH: &str = "/api/packs/uninstall";

/// The desktop app's own config: its version and the updater's public key.
const TAURI_CONF: &str = include_str!("../../desktop/tauri.conf.json");
/// Where each release's assets are (`scripts/publish-desktop-release.py` REPO).
const RELEASES: &str = "https://github.com/ZacharySF/rika/releases/download";
const INDEX_FILE: &str = "packs.json";
/// Written by the web build (web/vite.config.ts): every pack's files under the website.
const BUILT_IN_LIST: &str = "pack-files.json";
const INSTALLED: &str = "installed.json";
const INDEX_CACHE: &str = "index.json";
const TEMPORARY: &str = ".tmp-";
/// How long a fetched index answers listings before it is fetched again.
const INDEX_FRESH: Duration = Duration::from_secs(600);
const INDEX_LIMIT: usize = 1 << 20;
/// A download older than this with no process finishing it is abandoned.
const ABANDONED: Duration = Duration::from_secs(24 * 3600);

const BAD_SIGNATURE: &str = "The download list is not signed by rika!'s release key";
const UNREADABLE_INDEX: &str = "The download list is not readable";
const BAD_PACK: &str = "The download is not a valid pack; nothing was installed";

struct Release {
    version: String,
    pubkey: String,
}

static RELEASE: LazyLock<Release> = LazyLock::new(|| {
    let conf: Json = serde_json::from_str(TAURI_CONF).expect("desktop/tauri.conf.json is JSON");
    Release {
        version: conf["version"].as_str().expect("tauri.conf.json has a version").to_owned(),
        pubkey: conf["plugins"]["updater"]["pubkey"].as_str().expect("tauri.conf.json has the updater key").to_owned(),
    }
});

/// The signed index of one release's packs.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct Index {
    pub format: u32,
    pub version: String,
    pub packs: Vec<Entry>,
}

/// One pack in the index: its archive (an asset of the same release) and what it must match.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct Entry {
    pub id: String,
    pub version: String,
    pub file: String,
    /// The archive's size and SHA-256 (lowercase hex).
    pub size: u64,
    pub sha256: String,
    /// The sum of its files' sizes once unpacked.
    pub installed_size: u64,
}

/// `installed.json`: pack id → what is installed.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
struct Installed {
    version: String,
    /// The directory under the packs folder.
    dir: String,
    size: u64,
    installed_size: u64,
    sha256: String,
}

/// `pack.json` inside an archive.
#[derive(Deserialize)]
struct Manifest {
    id: String,
    version: String,
}

#[derive(Clone, Default)]
struct Job {
    running: bool,
    received: u64,
    total: u64,
    error: Option<String>,
}

struct Cached {
    index: Option<Index>,
    fetched: Option<Instant>,
    error: Option<String>,
}

pub struct Packs {
    dir: PathBuf,
    static_dir: PathBuf,
    version: String,
    base_url: String,
    key: Result<PublicKey, String>,
    /// `pack-files.json`: pack id → its files under the website.
    built_in_files: HashMap<String, Vec<String>>,
    installed: Mutex<HashMap<String, Installed>>,
    jobs: Mutex<HashMap<String, Job>>,
    index: tokio::sync::Mutex<Cached>,
    /// Built on first use: a listing or download is the only network traffic.
    client: OnceLock<Result<reqwest::Client, String>>,
}

fn fail(status: u16, message: impl Into<String>) -> Failure {
    Failure { status, message: message.into() }
}

/// The updater key as tauri.conf.json holds it: base64 of minisign's public key file.
pub fn decode_key(encoded: &str) -> Result<PublicKey, String> {
    let text = base64::engine::general_purpose::STANDARD.decode(encoded.trim()).map_err(|_| "unreadable release key".to_owned())?;
    let text = String::from_utf8(text).map_err(|_| "unreadable release key".to_owned())?;
    PublicKey::decode(&text).map_err(|_| "unreadable release key".to_owned())
}

fn plain_name(name: &str) -> bool {
    !name.is_empty() && !name.starts_with('.') && name.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}

/// Verify `packs.json` against its signature and read it; only an index for `version` counts.
pub fn verify_index(data: &[u8], signature: &[u8], key: &PublicKey, version: &str) -> Result<Index, String> {
    let text = std::str::from_utf8(signature).ok()
        .and_then(|s| base64::engine::general_purpose::STANDARD.decode(s.trim()).ok())
        .and_then(|s| String::from_utf8(s).ok())
        .ok_or_else(|| BAD_SIGNATURE.to_owned())?;
    let signature = Signature::decode(&text).map_err(|_| BAD_SIGNATURE.to_owned())?;
    key.verify(data, &signature, false).map_err(|_| BAD_SIGNATURE.to_owned())?;
    // The trusted comment is covered by the signature: it must name this file, so no other
    // file the key signed (an installer) can stand in for the index.
    if !signature.trusted_comment().split('\t').any(|field| field == format!("file:{INDEX_FILE}")) {
        return Err(BAD_SIGNATURE.to_owned());
    }
    let index: Index = serde_json::from_slice(data).map_err(|_| UNREADABLE_INDEX.to_owned())?;
    if index.format != 1 {
        return Err(UNREADABLE_INDEX.to_owned());
    }
    if index.version != version {
        return Err(format!("The download list is for rika! {}, not {version}", index.version));
    }
    for entry in &index.packs {
        let hex = entry.sha256.len() == 64 && entry.sha256.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
        if entry.version != version || !plain_name(&entry.id) || !plain_name(&entry.file) || !hex {
            return Err(UNREADABLE_INDEX.to_owned());
        }
    }
    Ok(index)
}

/// A finished download is the signed one only with the signed size and checksum.
pub fn check_download(entry: &Entry, received: u64, sha256: &str) -> Result<(), String> {
    if received != entry.size {
        return Err(format!("The download was cut short ({received} of {} bytes); nothing was installed", entry.size));
    }
    if sha256 != entry.sha256 {
        return Err("The download does not match its signed checksum; nothing was installed".into());
    }
    Ok(())
}

/// Unpack a checked archive into `tree` (a new, empty directory): regular files and directories
/// only, every path inside the pack, exactly the signed unpacked size, and a `pack.json` that
/// names this pack and version.
pub fn unpack(archive: &Path, entry: &Entry, tree: &Path) -> Result<(), String> {
    let bad = |_| BAD_PACK.to_owned();
    let mut reader = tar::Archive::new(flate2::read::GzDecoder::new(File::open(archive).map_err(bad)?));
    let mut total: u64 = 0;
    for item in reader.entries().map_err(bad)? {
        let mut item = item.map_err(bad)?;
        let path = item.path().map_err(bad)?.into_owned();
        let inside = path.components().all(|part| matches!(part, Component::Normal(name) if !name.to_string_lossy().starts_with('.')));
        if !inside || path.as_os_str().is_empty() {
            return Err(BAD_PACK.into());
        }
        let target = tree.join(&path);
        let kind = item.header().entry_type();
        if kind.is_dir() {
            fs::DirBuilder::new().recursive(true).mode(0o755).create(&target).map_err(bad)?;
        } else if kind.is_file() {
            total = total.checked_add(item.header().size().map_err(bad)?).ok_or_else(|| BAD_PACK.to_owned())?;
            if total > entry.installed_size {
                return Err(BAD_PACK.into());
            }
            if let Some(parent) = target.parent() {
                fs::DirBuilder::new().recursive(true).mode(0o755).create(parent).map_err(bad)?;
            }
            let mut output = OpenOptions::new().write(true).create_new(true).mode(0o644).open(&target).map_err(bad)?;
            std::io::copy(&mut item, &mut output).map_err(bad)?;
        } else {
            return Err(BAD_PACK.into());
        }
    }
    if total != entry.installed_size {
        return Err(BAD_PACK.into());
    }
    let manifest: Manifest = serde_json::from_slice(&fs::read(tree.join("pack.json")).map_err(bad)?).map_err(|_| BAD_PACK.to_owned())?;
    if manifest.id != entry.id || manifest.version != entry.version {
        return Err(BAD_PACK.into());
    }
    Ok(())
}

fn data_dir() -> PathBuf {
    let data = match std::env::var_os("XDG_DATA_HOME") {
        Some(path) if Path::new(&path).is_absolute() => PathBuf::from(path),
        _ => PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".local/share"),
    };
    data.join("kitty/packs")
}

/// `content` replaces `path` atomically: a temporary file, fsync, rename, directory fsync.
fn write_atomic(dir: &Path, name: &str, content: &[u8]) -> std::io::Result<()> {
    let temporary = dir.join(format!("{TEMPORARY}{name}.{}", crate::auth::token_hex(6)));
    let written = (|| {
        let mut handle = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&temporary)?;
        handle.write_all(content)?;
        handle.sync_all()?;
        fs::rename(&temporary, dir.join(name))
    })();
    if written.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    written?;
    File::open(dir)?.sync_all()
}

fn read_installed(dir: &Path) -> HashMap<String, Installed> {
    fs::read(dir.join(INSTALLED)).ok().and_then(|data| serde_json::from_slice(&data).ok()).unwrap_or_default()
}

fn remove_any(path: &Path) {
    if fs::symlink_metadata(path).is_ok_and(|meta| meta.is_dir()) {
        let _ = fs::remove_dir_all(path);
    } else {
        let _ = fs::remove_file(path);
    }
}

impl Packs {
    /// The packs this front can list, serve and install. `static_dir` is the website.
    pub fn open(static_dir: &Path) -> Packs {
        let release = &*RELEASE;
        // Test-only overrides: a local index and the key it is signed with. Whoever can set the
        // front's environment can replace the front itself.
        let base_url = std::env::var("KITTY_PACKS_URL").unwrap_or_else(|_| format!("{RELEASES}/v{}/", release.version));
        let key = decode_key(&std::env::var("KITTY_PACKS_KEY").unwrap_or_else(|_| release.pubkey.clone()));
        let built_in_files = fs::read(static_dir.join(BUILT_IN_LIST)).ok()
            .and_then(|data| serde_json::from_slice(&data).ok()).unwrap_or_default();
        let dir = data_dir();
        let packs = Packs {
            installed: Mutex::new(read_installed(&dir)),
            dir,
            static_dir: static_dir.to_owned(),
            version: release.version.clone(),
            base_url,
            key,
            built_in_files,
            jobs: Mutex::new(HashMap::new()),
            index: tokio::sync::Mutex::new(Cached { index: None, fetched: None, error: None }),
            client: OnceLock::new(),
        };
        packs.sweep();
        packs.load_cached_index();
        packs
    }

    fn client(&self) -> Result<&reqwest::Client, String> {
        self.client.get_or_init(|| {
            // The same TLS stack the desktop updater uses (rustls with ring).
            let _ = rustls::crypto::ring::default_provider().install_default();
            reqwest::Client::builder()
                .user_agent(concat!("rika-server/", env!("CARGO_PKG_VERSION")))
                // Plain HTTP only for the test-only loopback index.
                .https_only(!self.base_url.starts_with("http://127.0.0.1:"))
                .connect_timeout(Duration::from_secs(20))
                .read_timeout(Duration::from_secs(60))
                .build()
                .map_err(|_| "No secure connection is available".to_owned())
        }).as_ref().map_err(Clone::clone)
    }

    fn lock(&self) -> std::io::Result<File> {
        fs::DirBuilder::new().recursive(true).mode(0o700).create(&self.dir)?;
        let handle = OpenOptions::new().create(true).truncate(false).write(true).mode(0o600).open(self.dir.join(".lock"))?;
        handle.lock()?;
        Ok(handle)
    }

    /// Remove what a crash left: pack directories `installed.json` does not name, and
    /// downloads nobody has touched for a day (another front may still be writing a newer one).
    fn sweep(&self) {
        if !self.dir.is_dir() {
            return;
        }
        let Ok(_lock) = self.lock() else { return };
        let installed = read_installed(&self.dir);
        let kept: Vec<&str> = installed.values().map(|item| item.dir.as_str()).collect();
        let Ok(entries) = fs::read_dir(&self.dir) else { return };
        for item in entries.flatten() {
            let name = item.file_name().to_string_lossy().into_owned();
            let path = item.path();
            if name.starts_with(TEMPORARY) {
                let old = item.metadata().and_then(|meta| meta.modified()).ok()
                    .and_then(|at| SystemTime::now().duration_since(at).ok()).is_some_and(|age| age > ABANDONED);
                if old {
                    remove_any(&path);
                }
            } else if item.file_type().is_ok_and(|kind| kind.is_dir()) && !kept.contains(&name.as_str()) {
                remove_any(&path);
            }
        }
        *self.installed.lock().unwrap() = installed;
    }

    fn load_cached_index(&self) {
        let (Ok(data), Ok(signature), Ok(key)) = (fs::read(self.dir.join(INDEX_CACHE)), fs::read(self.dir.join(format!("{INDEX_CACHE}.sig"))), &self.key) else {
            return;
        };
        if let Ok(index) = verify_index(&data, &signature, key, &self.version) {
            self.index.try_lock().expect("nothing else holds the index yet").index = Some(index);
        }
    }

    fn built_in(&self, id: &str) -> bool {
        self.built_in_files.get(id).is_some_and(|files| !files.is_empty() && files.iter().all(|file| self.static_dir.join(file).is_file()))
    }

    fn installed_now(&self, id: &str) -> Option<Installed> {
        self.installed.lock().unwrap().get(id).filter(|item| item.version == self.version).cloned()
    }

    /// Whether a module needing pack `id` can be turned on: built in, or installed for this version.
    pub fn ready(&self, id: &str) -> bool {
        self.built_in(id) || self.installed_now(id).is_some()
    }

    /// A website file a pack provides, for the front's static route: only from packs
    /// installed for this version, and never outside the pack's `web/` folder.
    pub fn web_file(&self, relative: &str) -> Option<PathBuf> {
        let installed: Vec<Installed> = self.installed.lock().unwrap().values().filter(|item| item.version == self.version).cloned().collect();
        installed.iter().find_map(|item| {
            let root = fs::canonicalize(self.dir.join(&item.dir).join("web")).ok()?;
            let found = fs::canonicalize(root.join(relative.trim_start_matches('/'))).ok()?;
            (found.starts_with(&root) && found.is_file()).then_some(found)
        })
    }

    async fn fetch(&self, name: &str) -> Result<Vec<u8>, String> {
        let client = self.client()?;
        let offline = |_| "rika! could not reach GitHub to check for downloads".to_owned();
        let mut response = client.get(format!("{}{name}", self.base_url)).timeout(Duration::from_secs(30)).send().await.map_err(offline)?;
        if response.status() == reqwest::StatusCode::NOT_FOUND {
            return Err(format!("No downloads are published for rika! {}", self.version));
        }
        if !response.status().is_success() {
            return Err(format!("The download list is unavailable (HTTP {})", response.status().as_u16()));
        }
        let mut data = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(offline)? {
            data.extend_from_slice(&chunk);
            if data.len() > INDEX_LIMIT {
                return Err(UNREADABLE_INDEX.into());
            }
        }
        Ok(data)
    }

    /// The verified index, fetched again once it is older than `INDEX_FRESH` (or when `fresh`).
    async fn current_index(&self, fresh: bool) -> (Option<Index>, Option<String>) {
        let mut cached = self.index.lock().await;
        let stale = cached.fetched.is_none_or(|at| at.elapsed() > INDEX_FRESH);
        if fresh || stale {
            let fetched = async {
                let key = self.key.as_ref().map_err(Clone::clone)?;
                let data = self.fetch(INDEX_FILE).await?;
                let signature = self.fetch(&format!("{INDEX_FILE}.sig")).await?;
                let index = verify_index(&data, &signature, key, &self.version)?;
                Ok::<_, String>((index, data, signature))
            }.await;
            cached.fetched = Some(Instant::now());
            match fetched {
                Ok((index, data, signature)) => {
                    cached.index = Some(index);
                    cached.error = None;
                    // The cache lets the listing work offline; it is verified again when read.
                    if self.lock().is_ok() {
                        let _ = write_atomic(&self.dir, INDEX_CACHE, &data);
                        let _ = write_atomic(&self.dir, &format!("{INDEX_CACHE}.sig"), &signature);
                    }
                }
                Err(error) => cached.error = Some(error),
            }
        }
        (cached.index.clone(), cached.error.clone())
    }

    /// `GET /api/packs`.
    pub async fn listing(&self) -> Json {
        let (index, error) = self.current_index(false).await;
        self.describe(index.as_ref(), error)
    }

    fn describe(&self, index: Option<&Index>, error: Option<String>) -> Json {
        let installed = self.installed.lock().unwrap().clone();
        let jobs = self.jobs.lock().unwrap().clone();
        let packs: Vec<Json> = registry().packs.iter().map(|pack| {
            let entry = index.and_then(|index| index.packs.iter().find(|entry| entry.id == pack.id));
            let record = installed.get(&pack.id);
            let job = jobs.get(&pack.id);
            let state = if self.built_in(&pack.id) {
                "built-in"
            } else if job.is_some_and(|job| job.running) {
                "downloading"
            } else if record.is_some_and(|item| item.version == self.version) {
                "installed"
            } else if record.is_some() {
                "outdated"
            } else if entry.is_some() {
                "available"
            } else {
                "unavailable"
            };
            let modules: Vec<&str> = registry().modules.iter().filter(|m| m.packs.contains(&pack.id)).map(|m| m.id.as_str()).collect();
            json!({
                "id": pack.id,
                "label": pack.label,
                "description": pack.description,
                "modules": modules,
                "state": state,
                "size": entry.map(|e| e.size).or(record.map(|r| r.size)),
                "installed_size": record.map(|r| r.installed_size).or(entry.map(|e| e.installed_size)),
                "installed_version": record.map(|r| r.version.clone()),
                "progress": job.filter(|job| job.running).map(|job| json!({ "received": job.received, "total": job.total })),
                "error": job.and_then(|job| job.error.clone()),
            })
        }).collect();
        let mut index_state = serde_json::Map::new();
        if let Some(error) = error {
            index_state.insert("error".into(), json!(error));
        }
        json!({ "version": self.version, "index": index_state, "packs": packs })
    }

    fn known(id: Option<&Json>) -> Result<String, Failure> {
        let id = id.and_then(Json::as_str).ok_or_else(|| fail(400, "Expected {\"id\": pack id}"))?;
        registry().packs.iter().find(|pack| pack.id == id).map(|pack| pack.id.clone()).ok_or_else(|| fail(404, format!("unknown pack {id:?}")))
    }

    fn label(id: &str) -> &'static str {
        registry().packs.iter().find(|pack| pack.id == id).map_or("This pack", |pack| pack.label.as_str())
    }

    /// `POST /api/packs/install {id}`: start the download in the background.
    pub async fn install(self: &Arc<Self>, body: &serde_json::Map<String, Json>) -> Result<Json, Failure> {
        if body.len() != 1 {
            return Err(fail(400, "Expected {\"id\": pack id}"));
        }
        let id = Self::known(body.get("id"))?;
        if self.built_in(&id) {
            return Err(fail(409, format!("{} is part of this build already", Self::label(&id))));
        }
        let (index, error) = self.current_index(true).await;
        let entry = index.as_ref().and_then(|index| index.packs.iter().find(|entry| entry.id == id)).cloned();
        let Some(entry) = entry else {
            return Err(fail(503, error.unwrap_or_else(|| format!("No {} download is published for rika! {}", Self::label(&id), self.version))));
        };
        let up_to_date = self.installed_now(&id).is_some_and(|item| item.sha256 == entry.sha256);
        {
            let mut jobs = self.jobs.lock().unwrap();
            let running = jobs.get(&id).is_some_and(|job| job.running);
            if !running && !up_to_date {
                jobs.insert(id.clone(), Job { running: true, received: 0, total: entry.size, error: None });
                let packs = self.clone();
                tokio::spawn(async move {
                    let outcome = packs.download(&entry).await;
                    let mut jobs = packs.jobs.lock().unwrap();
                    let job = jobs.entry(entry.id.clone()).or_default();
                    job.running = false;
                    job.error = outcome.err();
                });
            }
        }
        Ok(self.describe(index.as_ref(), error))
    }

    /// Download, check, unpack and install one pack; on any error nothing changes.
    async fn download(self: &Arc<Self>, entry: &Entry) -> Result<(), String> {
        let storage = |_| "rika! could not write the download to disk; nothing was installed".to_owned();
        fs::DirBuilder::new().recursive(true).mode(0o700).create(&self.dir).map_err(storage)?;
        let work = self.dir.join(format!("{TEMPORARY}{}-{}", entry.id, crate::auth::token_hex(6)));
        fs::DirBuilder::new().mode(0o700).create(&work).map_err(storage)?;
        let outcome = async {
            let archive = work.join("archive");
            self.receive(entry, &archive).await?;
            let packs = self.clone();
            let (entry, work) = (entry.clone(), work.clone());
            tokio::task::spawn_blocking(move || {
                let tree = work.join("tree");
                fs::DirBuilder::new().mode(0o755).create(&tree).map_err(storage)?;
                unpack(&archive, &entry, &tree)?;
                packs.commit(&entry, &tree).map_err(storage)
            }).await.map_err(|_| BAD_PACK.to_owned())?
        }.await;
        remove_any(&work);
        outcome
    }

    /// Stream the archive into `archive`, counting progress; it must be exactly the signed bytes.
    async fn receive(&self, entry: &Entry, archive: &Path) -> Result<(), String> {
        use tokio::io::AsyncWriteExt;
        let client = self.client()?;
        let offline = |_| "The download stopped: rika! lost its connection to GitHub; nothing was installed".to_owned();
        let storage = |_| "rika! could not write the download to disk; nothing was installed".to_owned();
        let mut response = client.get(format!("{}{}", self.base_url, entry.file)).send().await.map_err(offline)?;
        if !response.status().is_success() {
            return Err(format!("The download is unavailable (HTTP {}); nothing was installed", response.status().as_u16()));
        }
        let mut output = tokio::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(archive).await.map_err(storage)?;
        let mut digest = Sha256::new();
        let mut received: u64 = 0;
        while let Some(chunk) = response.chunk().await.map_err(offline)? {
            received += chunk.len() as u64;
            if received > entry.size {
                return Err("The download is larger than its signed size; nothing was installed".into());
            }
            digest.update(&chunk);
            output.write_all(&chunk).await.map_err(storage)?;
            if let Some(job) = self.jobs.lock().unwrap().get_mut(&entry.id) {
                job.received = received;
            }
        }
        output.sync_all().await.map_err(storage)?;
        check_download(entry, received, &hex::encode(digest.finalize()))
    }

    /// Make an unpacked `tree` the installed pack: moved beside the others, then named by
    /// `installed.json` in one atomic write; the previous version goes only after that.
    fn commit(&self, entry: &Entry, tree: &Path) -> std::io::Result<()> {
        let _lock = self.lock()?;
        let name = format!("{}-{}-{}", entry.id, entry.version, crate::auth::token_hex(4));
        let target = self.dir.join(&name);
        fs::rename(tree, &target)?;
        let mut installed = read_installed(&self.dir);
        let previous = installed.insert(entry.id.clone(), Installed {
            version: entry.version.clone(),
            dir: name,
            size: entry.size,
            installed_size: entry.installed_size,
            sha256: entry.sha256.clone(),
        });
        if let Err(error) = write_atomic(&self.dir, INSTALLED, &serde_json::to_vec_pretty(&installed).expect("records serialize")) {
            remove_any(&target);
            return Err(error);
        }
        if let Some(previous) = previous.filter(|previous| !previous.dir.is_empty()) {
            remove_any(&self.dir.join(previous.dir));
        }
        *self.installed.lock().unwrap() = installed;
        Ok(())
    }

    /// `POST /api/packs/uninstall {id}`: refused while the next start would need the pack.
    pub async fn uninstall(&self, body: &serde_json::Map<String, Json>, configured: &[String]) -> Result<Json, Failure> {
        if body.len() != 1 {
            return Err(fail(400, "Expected {\"id\": pack id}"));
        }
        let id = Self::known(body.get("id"))?;
        let label = Self::label(&id);
        if self.built_in(&id) {
            return Err(fail(409, format!("{label} is part of this build and cannot be removed")));
        }
        if self.jobs.lock().unwrap().get(&id).is_some_and(|job| job.running) {
            return Err(fail(409, format!("{label} is downloading; wait for it to finish")));
        }
        if let Some(module) = registry().modules.iter().find(|m| m.packs.contains(&id) && configured.contains(&m.id)) {
            return Err(fail(409, format!("{} is turned on: turn it off and save before removing its download", module.label)));
        }
        let removed = tokio::task::block_in_place(|| -> std::io::Result<()> {
            if !self.dir.is_dir() {
                return Ok(());
            }
            let _lock = self.lock()?;
            let mut installed = read_installed(&self.dir);
            if let Some(previous) = installed.remove(&id) {
                write_atomic(&self.dir, INSTALLED, &serde_json::to_vec_pretty(&installed).expect("records serialize"))?;
                remove_any(&self.dir.join(previous.dir));
            }
            *self.installed.lock().unwrap() = installed;
            Ok(())
        });
        removed.map_err(|_| fail(500, "Local operation failed; inspect CLI diagnostics"))?;
        self.jobs.lock().unwrap().remove(&id);
        Ok(self.listing().await)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // A throwaway minisign key pair (`tauri signer generate`, no password) and signatures made
    // with `tauri signer sign`, for the checks below; it signs nothing real.
    const KEY: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEE1RTEzNkEzODAwODI2QTYKUldTbUpnaUFvemJocFNsRTRuVHJHaUxDNUxtSWlIeHl6Q3I5a1J6TnVzbmhJUVF1c3VCNDl2anQK";

    #[test]
    fn the_release_key_and_version_come_from_the_desktop_config() {
        assert!(decode_key(&RELEASE.pubkey).is_ok());
        assert!(RELEASE.version.split('.').count() == 3);
        assert!(decode_key(KEY).is_ok());
        assert!(decode_key("not base64").is_err());
    }

    #[test]
    fn plain_names_only() {
        assert!(plain_name("rika-pack-brain-0.3.36.tar.gz"));
        for name in ["", ".hidden", "a/b", "..", "a b", "ü"] {
            assert!(!plain_name(name), "{name}");
        }
    }

    fn entry(size: u64, sha256: &str) -> Entry {
        Entry { id: "brain".into(), version: "1.0.0".into(), file: "x.tar.gz".into(), size, sha256: sha256.into(), installed_size: 0 }
    }

    #[test]
    fn a_download_must_be_the_signed_bytes() {
        let sum = "a".repeat(64);
        assert!(check_download(&entry(10, &sum), 10, &sum).is_ok());
        assert!(check_download(&entry(10, &sum), 9, &sum).unwrap_err().contains("cut short"));
        assert!(check_download(&entry(10, &sum), 10, &"b".repeat(64)).unwrap_err().contains("checksum"));
    }

    const INDEX: &str = r#"{"format":1,"version":"1.0.0","packs":[{"id":"brain","version":"1.0.0","file":"rika-pack-brain-1.0.0.tar.gz","size":10,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","installed_size":5}]}"#;
    /// `tauri signer sign packs.json` of INDEX.
    const INDEX_SIG: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVTbUpnaUFvemJocFJQSUI4dnEyUUluQ1VQZlZESWN4UnlpckdDWVJCOUdZOVVXbDhTTVkvV0tlTXhsejY5dWlnQlF4bDE1L0FKYVBvbUhIa3JvTmZxZ2RKY2gzd2d3SWdzPQp0cnVzdGVkIGNvbW1lbnQ6IHRpbWVzdGFtcDoxNzk0NTA0Nzk3CWZpbGU6cGFja3MuanNvbgp2Q251SXdCM0NTeVNpTGNUbFBEMFM5UThyUC85aWl5K3o4Z0xHNGpaZGdIVVluVVZWcnV1Q2YzWVprL3Zkbm9GZWJJTWFnTVFoZ2JIRlRMV1QxamVEdz09Cg==";
    /// The same bytes signed as `other.json`: a valid signature of the wrong file.
    const OTHER_SIG: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVTbUpnaUFvemJocFh5a01nditNRHlPTmtIeUVnbDlFVU01ZWlkc3dtRUJWWWNlbmdRZ1FOa1FUelJwNFdScnRVRWZGdGNuVGtBNzFHR3FRMkdhWnQ3S053WW9Daldndmd3PQp0cnVzdGVkIGNvbW1lbnQ6IHRpbWVzdGFtcDoxNzk0NTA0ODAwCWZpbGU6b3RoZXIuanNvbgo4MHRFc012MUxXam9YTEtDSXh5UjgxS1pwQkhvK1R6SjA3d253bnhrTkE3cENwYTY3ckpGVFlpTkRTRlZUNVhUd01WWW5GR1R3dnpxa2x5QWZLU0tDZz09Cg==";

    #[test]
    fn only_the_signed_index_of_this_version_is_read() {
        let key = decode_key(KEY).unwrap();
        let index = verify_index(INDEX.as_bytes(), INDEX_SIG.as_bytes(), &key, "1.0.0").unwrap();
        assert_eq!(index.packs[0].file, "rika-pack-brain-1.0.0.tar.gz");
        let tampered = INDEX.replace("\"size\":10", "\"size\":11");
        assert_eq!(verify_index(tampered.as_bytes(), INDEX_SIG.as_bytes(), &key, "1.0.0").unwrap_err(), BAD_SIGNATURE);
        assert_eq!(verify_index(INDEX.as_bytes(), OTHER_SIG.as_bytes(), &key, "1.0.0").unwrap_err(), BAD_SIGNATURE);
        assert_eq!(verify_index(INDEX.as_bytes(), b"garbage", &key, "1.0.0").unwrap_err(), BAD_SIGNATURE);
        assert_eq!(verify_index(INDEX.as_bytes(), INDEX_SIG.as_bytes(), &decode_key(&RELEASE.pubkey).unwrap(), "1.0.0").unwrap_err(), BAD_SIGNATURE);
        assert_eq!(verify_index(INDEX.as_bytes(), INDEX_SIG.as_bytes(), &key, "1.0.1").unwrap_err(), "The download list is for rika! 1.0.0, not 1.0.1");
    }

    /// A gzipped tar of `members` (path, data; a `None` data is a symbolic link), written raw so
    /// that paths the tar crate would refuse to write can be tested.
    fn archive(dir: &Path, members: &[(&str, Option<&[u8]>)]) -> PathBuf {
        let path = dir.join("archive");
        let mut builder = tar::Builder::new(flate2::write::GzEncoder::new(File::create(&path).unwrap(), flate2::Compression::fast()));
        for (name, data) in members {
            let mut header = tar::Header::new_gnu();
            header.as_old_mut().name[..name.len()].copy_from_slice(name.as_bytes());
            header.set_mode(0o644);
            match data {
                Some(data) => {
                    header.set_size(data.len() as u64);
                    header.set_entry_type(tar::EntryType::Regular);
                    header.set_cksum();
                    builder.append(&header, *data).unwrap();
                }
                None => {
                    header.set_size(0);
                    header.set_entry_type(tar::EntryType::Symlink);
                    header.set_link_name("/etc/passwd").unwrap();
                    header.set_cksum();
                    builder.append(&header, std::io::empty()).unwrap();
                }
            }
        }
        builder.into_inner().unwrap().finish().unwrap();
        path
    }

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("rika-packs-{name}-{}", crate::auth::token_hex(4)));
        fs::create_dir_all(dir.join("tree")).unwrap();
        dir
    }

    #[test]
    fn unpacking_keeps_every_file_inside_the_pack_and_checks_it_whole() {
        const MANIFEST: &[u8] = br#"{"id": "brain", "version": "1.0.0"}"#;
        let sized = |members: &[(&str, Option<&[u8]>)]| Entry {
            installed_size: members.iter().map(|(_, data)| data.map_or(0, |d| d.len() as u64)).sum(),
            ..entry(0, "")
        };
        let good: &[(&str, Option<&[u8]>)] = &[("pack.json", Some(MANIFEST)), ("web/assets/a.js", Some(b"a"))];
        let dir = scratch("good");
        unpack(&archive(&dir, good), &sized(good), &dir.join("tree")).unwrap();
        assert_eq!(fs::read(dir.join("tree/web/assets/a.js")).unwrap(), b"a");
        // A wrong unpacked size (a file missing or added) is not the signed pack.
        let dir = scratch("size");
        let wrong = Entry { installed_size: sized(good).installed_size + 1, ..sized(good) };
        assert_eq!(unpack(&archive(&dir, good), &wrong, &dir.join("tree")).unwrap_err(), BAD_PACK);
        for (name, members) in [
            ("escape", &[("pack.json", Some(MANIFEST)), ("../escaped.js", Some(b"x" as &[u8]))] as &[(&str, Option<&[u8]>)]),
            ("absolute", &[("pack.json", Some(MANIFEST)), ("/tmp/escaped.js", Some(b"x"))]),
            ("hidden", &[("pack.json", Some(MANIFEST)), ("web/.hidden", Some(b"x"))]),
            ("link", &[("pack.json", Some(MANIFEST)), ("web/link", None)]),
            ("manifest", &[("pack.json", Some(br#"{"id": "other", "version": "1.0.0"}"#)), ("web/a.js", Some(b"x"))]),
            ("duplicate", &[("pack.json", Some(MANIFEST)), ("web/a.js", Some(b"x")), ("web/a.js", Some(b"y"))]),
        ] {
            let dir = scratch(name);
            assert_eq!(unpack(&archive(&dir, members), &sized(members), &dir.join("tree")).unwrap_err(), BAD_PACK, "{name}");
            assert!(!dir.join("escaped.js").exists() && !Path::new("/tmp/escaped.js").exists(), "{name}");
        }
    }
}
