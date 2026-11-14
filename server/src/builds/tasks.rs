//! The front's background tasks, as `kitty.web_tasks.TaskRunner` keeps them: a snapshot
//! `{id, kind, status, started_at, finished_at, result, error}` per task, 409 while one of the
//! same kind runs. `GET /api/tasks/<id>` answers the front's own ids; every other id is the
//! worker's.

use std::collections::HashMap;
use std::future::Future;
use std::sync::{LazyLock, Mutex};

use super::clock::Moment;
use super::failure::Failure;
use super::value::dump_str;

struct Record {
    kind: &'static str,
    status: &'static str,
    started_at: String,
    finished_at: Option<String>,
    /// The result as JSON text (Python's `result`), when completed.
    result: Option<String>,
    error: Option<String>,
}

static RECORDS: LazyLock<Mutex<HashMap<String, Record>>> = LazyLock::new(Default::default);

/// `uuid4().hex`.
fn task_id() -> String {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).expect("the operating system's random source failed");
    bytes[6] = bytes[6] & 0x0f | 0x40;
    bytes[8] = bytes[8] & 0x3f | 0x80;
    hex::encode(bytes)
}

fn snapshot(id: &str, record: &Record) -> String {
    let mut out = String::from("{\"id\":");
    dump_str(&super::value::text(id), &mut out);
    out.push_str(&format!(",\"kind\":\"{}\",\"status\":\"{}\",\"started_at\":\"{}\",\"finished_at\":", record.kind, record.status, record.started_at));
    match &record.finished_at {
        Some(at) => out.push_str(&format!("\"{at}\"")),
        None => out.push_str("null"),
    }
    out.push_str(",\"result\":");
    out.push_str(record.result.as_deref().unwrap_or("null"));
    out.push_str(",\"error\":");
    match &record.error {
        Some(error) => dump_str(&super::value::text(error), &mut out),
        None => out.push_str("null"),
    }
    out.push('}');
    out
}

/// `TaskRunner.start`: run `work` in the background; its `Ok` is the result's JSON text, its
/// `Err` the failure message. Returns the initial snapshot's JSON text.
pub fn start(kind: &'static str, work: impl Future<Output = Result<String, String>> + Send + 'static) -> Result<String, Failure> {
    let mut records = RECORDS.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    if records.values().any(|record| record.kind == kind && record.status == "running") {
        return Err(Failure::new(409, format!("{kind} is already running")));
    }
    let id = task_id();
    let record = Record { kind, status: "running", started_at: Moment::now().iso_seconds(), finished_at: None, result: None, error: None };
    let initial = snapshot(&id, &record);
    records.insert(id.clone(), record);
    drop(records);
    tokio::spawn(async move {
        let outcome = work.await;
        let mut records = RECORDS.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        if let Some(record) = records.get_mut(&id) {
            match outcome {
                Ok(result) => {
                    record.status = "completed";
                    record.result = Some(result);
                }
                Err(error) => {
                    record.status = "failed";
                    record.error = Some(error);
                }
            }
            record.finished_at = Some(Moment::now().iso_seconds());
        }
    });
    Ok(initial)
}

/// The snapshot of one of the front's tasks, or None for an id the front never issued.
pub fn find(id: &str) -> Option<String> {
    let records = RECORDS.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    records.get(id).map(|record| snapshot(id, record))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_task_of_a_kind_runs_at_a_time_and_ends_with_its_outcome() {
        let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        runtime.block_on(async {
            let (release, wait) = tokio::sync::oneshot::channel::<()>();
            let first = start("test-kind", async move {
                let _ = wait.await;
                Err("That is not a valid link".to_owned())
            }).ok().unwrap();
            let id = first[7..39].to_owned();
            assert!(first.contains("\"status\":\"running\"") && first.contains("\"finished_at\":null"));
            assert_eq!(start("test-kind", async { Ok("null".into()) }).err().map(|f| (f.status, f.message)),
                       Some((409, "test-kind is already running".to_owned())));
            release.send(()).unwrap();
            for _ in 0..100 {
                tokio::task::yield_now().await;
            }
            let done = find(&id).unwrap();
            assert!(done.contains("\"status\":\"failed\"") && done.contains("\"error\":\"That is not a valid link\""), "{done}");
            assert!(find(&"0".repeat(32)).is_none());
        });
    }
}
