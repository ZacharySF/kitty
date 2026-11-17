//! `GET /api/apply/health`: `kitty.core.apply.health.report` over the attempt records, the
//! unattended ready rate per ATS family with Wilson 95% intervals, the top causes and the
//! per-class field correctness.

use std::collections::HashMap;

use indexmap::IndexMap;
use rusqlite::types::ValueRef;
use rusqlite::Connection;
use serde_json::{json, Value};

use crate::ported::{internal, Done};

const MIN_N: i64 = 20;
const Z: f64 = 1.959963984540054;
const TOP_CAUSES: usize = 8;

/// `attempts.PROVENANCE`, in order.
const PROVENANCE: [&str; 7] = ["profile", "saved_answer", "confirmed_rule", "resume_derived", "ai_draft", "consent_ruling", "account"];

/// `attempts.CAUSES`: cause -> (outcome, label).
const CAUSES: &[(&str, &str, &str)] = &[
    ("ready", "ready", "Ready for your Submit"),
    ("closed", "closed", "Posting closed"),
    ("captcha", "needs_owner", "CAPTCHA"),
    ("verification", "needs_owner", "Email or security verification"),
    ("credentials", "needs_owner", "Saved credentials missing or invalid"),
    ("login_not_authorized", "needs_owner", "Account access not authorized for this origin"),
    ("login_manual", "needs_owner", "Account set to manual sign-in"),
    ("login_rejected", "needs_owner", "Sign-in rejected or account locked"),
    ("account_exists", "needs_owner", "Existing account needs its sign-in"),
    ("login_no_advance", "needs_owner", "Sign-in did not advance"),
    ("consent_account", "needs_owner", "Account terms or consent"),
    ("account_problem", "needs_owner", "Employer account needs you (reset, security question, refused)"),
    ("account_cap", "needs_owner", "Daily account cap reached"),
    ("keyring_locked", "needs_owner", "OS keyring locked"),
    ("consent", "needs_owner", "Consent dialog"),
    ("missing_answer", "needs_owner", "Missing answer"),
    ("upload_deferred", "needs_owner", "Resume upload deferred on a retained tab"),
    ("parse_changed", "needs_owner", "Resume parsing changed values kitty could not put back"),
    ("hidden_target_refused", "needs_owner", "Hidden or trap field refused"),
    ("approval_changed", "needs_owner", "Reviewed plan changed"),
    ("handoff", "needs_owner", "Other handoff"),
    ("owner_took_over", "needs_owner", "You acted in the tab while kitty filled it"),
    ("restart_firefox", "needs_owner", "Browser connection lost; restart kitty's Firefox"),
    ("login_unsupported", "unsupported", "Unsupported sign-in form"),
    ("profile_records", "unsupported", "Profile records need manual entry"),
    ("custom_controls", "unsupported", "Unsupported custom controls"),
    ("choice_failed", "unsupported", "Choice control failed"),
    ("invalid_field", "unsupported", "Saved answer rejected by the field"),
    ("validation_errors", "unsupported", "Page validation errors"),
    ("eightfold_profile", "unsupported", "Eightfold profile step"),
    ("resume_unverified", "unsupported", "Resume upload not verified"),
    ("review_unverified", "unsupported", "Review page could not be verified"),
    ("step_repeated", "unsupported", "Step did not advance"),
    ("step_limit", "unsupported", "Step limit reached"),
    ("unsupported_step", "unsupported", "Unsupported application step"),
    ("unsupported_page", "unsupported", "Unsupported application page"),
    ("unsupported", "unsupported", "Other unsupported page"),
    ("unsupported_flow", "unsupported", "Excluded flow (handed to you)"),
    ("site_error", "failed", "Employer site server error (try again later)"),
    ("loading", "failed", "Page kept loading"),
    ("upload_pending", "failed", "Upload or resume parsing still pending"),
    ("stopped", "failed", "Stopped or out of time"),
    ("evidence_save", "failed", "Recovery evidence could not be saved"),
    ("tab_closed", "failed", "Tab closed or replaced"),
    ("browser_frame", "failed", "Browser frame lost (no such frame)"),
    ("browser_connection", "failed", "Browser connection lost"),
    ("submit_vetoed", "failed", "Submit guard blocked an action"),
    ("submit_blocked_network", "failed", "Submit guard blocked a network request"),
    ("browser_error", "failed", "Browser error"),
    ("unknown", "failed", "Unclassified stop"),
];

/// One job's attempts folded together.
struct Job {
    ready: bool,
    ready_no_input: bool,
    closed_seen: bool,
    inputs: i64,
    family: String,
    cause: String,
}

impl Job {
    fn closed(&self) -> bool {
        self.closed_seen && !self.ready
    }
}

/// `wilson(successes, n)` (n > 0).
fn wilson(successes: i64, n: i64) -> (f64, f64) {
    let (s, n) = (successes as f64, n as f64);
    let p = s / n;
    let denominator = 1.0 + Z * Z / n;
    let centre = (p + Z * Z / (2.0 * n)) / denominator;
    let half = Z * (p * (1.0 - p) / n + Z * Z / (4.0 * n * n)).sqrt() / denominator;
    ((centre - half).max(0.0), (centre + half).min(1.0))
}

/// `_rate`: (rate, low, high), all null below the sample floor.
fn rate(successes: i64, n: i64) -> (Value, Value, Value) {
    if n < MIN_N {
        return (Value::Null, Value::Null, Value::Null);
    }
    let (low, high) = wilson(successes, n);
    (json!(successes as f64 / n as f64), json!(low), json!(high))
}

/// `_family`.
fn family(name: &str, jobs: &[&Job]) -> Value {
    let live: Vec<&&Job> = jobs.iter().filter(|job| !job.closed()).collect();
    let n = live.len() as i64;
    let ready = live.iter().filter(|job| job.ready).count() as i64;
    let unattended = live.iter().filter(|job| job.ready_no_input).count() as i64;
    let (urr, low, high) = rate(ready, n);
    let (urr_a, low_a, high_a) = rate(unattended, n);
    json!({
        "family": name, "jobs": n, "ready": ready, "closed": jobs.len() as i64 - n,
        "urr": urr, "low": low, "high": high, "insufficient": n < MIN_N,
        "ready_no_input": unattended, "urr_no_input": urr_a, "no_input_low": low_a, "no_input_high": high_a,
    })
}

fn text(value: ValueRef<'_>) -> Done<String> {
    match value {
        ValueRef::Text(text) => String::from_utf8(text.to_vec()).map_err(|_| internal()),
        _ => Err(internal()),
    }
}

fn integer(value: ValueRef<'_>) -> Done<i64> {
    match value {
        ValueRef::Integer(number) => Ok(number),
        _ => Err(internal()),
    }
}

/// `health.report(conn)`.
pub fn report(conn: &Connection) -> Done<Value> {
    let mut jobs: IndexMap<i64, Job> = IndexMap::new();
    let mut attempt_causes: HashMap<String, i64> = HashMap::new();
    let mut last_attempt_at = Value::Null;
    let mut total = 0;
    {
        let mut statement = conn.prepare("SELECT job_id, ats_family, outcome, cause, owner_inputs, ended_at FROM apply_attempts ORDER BY id")?;
        let mut rows = statement.query([])?;
        while let Some(row) = rows.next()? {
            total += 1;
            last_attempt_at = crate::ported::db::column(row.get_ref(5)?)?;
            let job = jobs.entry(integer(row.get_ref(0)?)?).or_insert_with(|| Job {
                ready: false, ready_no_input: false, closed_seen: false, inputs: 0, family: "other".into(), cause: String::new(),
            });
            job.family = text(row.get_ref(1)?)?;
            if !job.ready {
                job.inputs += integer(row.get_ref(4)?)?;
            }
            let outcome = text(row.get_ref(2)?)?;
            let cause = text(row.get_ref(3)?)?;
            if outcome == "ready" {
                if !job.ready {
                    job.ready_no_input = job.inputs == 0;
                }
                job.ready = true;
            } else if outcome == "closed" {
                job.closed_seen = true;
            } else {
                *attempt_causes.entry(cause.clone()).or_insert(0) += 1;
            }
            job.cause = cause;
        }
    }
    let mut by_family: IndexMap<&str, Vec<&Job>> = IndexMap::new();
    for job in jobs.values() {
        by_family.entry(job.family.as_str()).or_default().push(job);
    }
    let mut families: Vec<(i64, &str, Value)> = by_family.iter()
        .map(|(name, members)| {
            let value = family(name, members);
            (value["jobs"].as_i64().unwrap_or(0), *name, value)
        })
        .collect();
    families.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.cmp(b.1)));

    // Counter(...).items() in first-seen order, then sorted by (-jobs, -attempts, cause).
    let mut job_causes: IndexMap<&str, i64> = IndexMap::new();
    for job in jobs.values() {
        if !job.ready && !job.closed() && job.cause != "ready" {
            *job_causes.entry(job.cause.as_str()).or_insert(0) += 1;
        }
    }
    let attempts_of = |cause: &str| attempt_causes.get(cause).copied().unwrap_or(0);
    let mut ranked: Vec<(&str, i64)> = job_causes.into_iter().collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| attempts_of(b.0).cmp(&attempts_of(a.0))).then_with(|| a.0.cmp(b.0)));
    let causes: Vec<Value> = ranked.into_iter().take(TOP_CAUSES).map(|(cause, count)| {
        let (outcome, label) = CAUSES.iter().find(|(name, _, _)| *name == cause)
            .map_or(("failed", cause), |(_, outcome, label)| (*outcome, *label));
        json!({"cause": cause, "label": label, "outcome": outcome, "jobs": count, "attempts": attempts_of(cause)})
    }).collect();

    let class = |provenance: &str| provenance.split(':').next().unwrap_or("").to_owned();
    let mut filled: IndexMap<String, i64> = IndexMap::new();
    {
        let mut statement = conn.prepare("SELECT provenance FROM apply_attempt_fields")?;
        let mut rows = statement.query([])?;
        while let Some(row) = rows.next()? {
            *filled.entry(class(&text(row.get_ref(0)?)?)).or_insert(0) += 1;
        }
    }
    let mut checked: IndexMap<String, (i64, i64)> = IndexMap::new();
    {
        let mut statement = conn.prepare(
            "SELECT f.provenance, c.edited FROM apply_field_checks c
             JOIN apply_attempt_fields f ON f.attempt_id = c.attempt_id AND f.position = c.position")?;
        let mut rows = statement.query([])?;
        while let Some(row) = rows.next()? {
            let counts = checked.entry(class(&text(row.get_ref(0)?)?)).or_insert((0, 0));
            counts.0 += 1;
            counts.1 += integer(row.get_ref(1)?)?;
        }
    }
    // sorted(set(filled) | set(checked), key=PROVENANCE position, else 99): a stable sort, so
    // the unknown classes keep the set's order, which Python leaves to string hashing; here
    // they follow by name.
    let mut kinds: Vec<&String> = filled.keys().chain(checked.keys()).collect::<std::collections::BTreeSet<_>>().into_iter().collect();
    kinds.sort_by_key(|kind| PROVENANCE.iter().position(|known| known == kind).unwrap_or(99));
    let correctness: Vec<Value> = kinds.into_iter().map(|kind| {
        let (compared, edited) = checked.get(kind).copied().unwrap_or((0, 0));
        let (rate, low, high) = rate(compared - edited, compared);
        json!({"class": kind, "filled": filled.get(kind).copied().unwrap_or(0), "compared": compared, "edited": edited,
               "rate": rate, "low": low, "high": high, "insufficient": compared < MIN_N})
    }).collect();

    let all: Vec<&Job> = jobs.values().collect();
    Ok(json!({
        "min_n": MIN_N, "confidence": 0.95, "attempts": total, "last_attempt_at": last_attempt_at,
        "overall": family("all", &all), "families": families.into_iter().map(|(_, _, value)| value).collect::<Vec<_>>(),
        "causes": causes, "correctness": correctness,
    }))
}
