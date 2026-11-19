//! Settings → AI's local half (`kitty.web_ai`): `GET /api/ai/status` (the settings row, whether
//! an OpenRouter key is stored, today's spend from the `ai_reviews` ledger, Rika's tier models
//! and the refreshed catalogue) and `POST /api/ai/settings` (the key into the key store, the
//! rest into the build file's `settings.ai`, projected into `ai_settings`).

use rusqlite::Connection;
use serde_json::{json, Map, Value};

use super::decimal::{self, Decimal};
use crate::ported::time::Dt;
use crate::ported::{db, fail, internal, py, Ctx, Done};

const TIERS: [&str; 3] = ["fast", "standard", "deep"];
/// `router.DEFAULTS`: tier models per provider family of the main model.
const DEFAULTS: [(&str, [&str; 3]); 4] = [
    ("anthropic", ["anthropic/claude-haiku-4.5", "anthropic/claude-sonnet-4.5", "anthropic/claude-opus-4.5"]),
    ("openai", ["openai/gpt-5-mini", "openai/gpt-5", "openai/gpt-5-pro"]),
    ("google", ["google/gemini-2.5-flash", "google/gemini-2.5-pro", "google/gemini-2.5-pro"]),
    ("", ["google/gemini-2.5-flash", "anthropic/claude-sonnet-4.5", "anthropic/claude-opus-4.5"]),
];
pub const PRIVACY_NOTICE: &str = "Only text you select and review, and what you ask Rika with the records its tools read for that request, is sent to OpenRouter. Note text is shared only while Notes allow AI. Providers must decline data collection. AI output is a draft, not verified fact.";
const FIELDS: [&str; 9] = ["revision", "model", "routing", "max_output_tokens", "daily_budget_usd", "rika_tiers", "key", "remove_key", "confirmed"];

fn keys_failure((status, message): (u16, String)) -> crate::ported::Fault {
    fail(status, message)
}

/// `web_ai._settings`: the settings row as a dict.
fn settings(conn: &Connection) -> Done<Map<String, Value>> {
    db::row(conn, "SELECT * FROM ai_settings WHERE id=1", [])?.ok_or_else(internal)
}

/// `str(value)` as `_number` hands it to `Decimal`, read as `Decimal` reads text.
fn decimal_of(value: &Value) -> Option<Decimal> {
    match value {
        Value::String(text) => decimal::parse(text),
        Value::Number(number) => match number.as_i64().map(|n| n.to_string()).or_else(|| number.as_u64().map(|n| n.to_string())) {
            Some(text) => decimal::parse(&text),
            None => decimal::parse(&py::float_repr(number.as_f64()?)),
        },
        // `str(None)`, `str([...])`, `str({...})` are no numbers.
        _ => None,
    }
}

/// `web_ai._spent`: today's (UTC) reservations, or the actual cost once reported, summed in
/// the default decimal context.
fn spent(conn: &Connection) -> Done<f64> {
    let today = Dt::now_utc().date().iso();
    let mut statement = conn.prepare("SELECT reserved_usd,actual_usd FROM ai_reviews WHERE created_at>=?")?;
    let mut rows = statement.query([today])?;
    let mut total = crate::rika::decimal::Decimal::ZERO;
    while let Some(row) = rows.next()? {
        let reserved = db::column(row.get_ref(0)?)?;
        let actual = db::column(row.get_ref(1)?)?;
        let chosen = if actual.is_null() { reserved } else { actual };
        let parsed = match &chosen {
            Value::String(text) => decimal::parse(text),
            Value::Number(number) if number.is_i64() => decimal::parse(&number.to_string()),
            _ => None,
        };
        let amount = parsed.and_then(|value| value.ascii()).and_then(|text| crate::rika::decimal::Decimal::parse(&text)).ok_or_else(internal)?;
        total = total.add(amount).ok_or_else(internal)?;
    }
    total.to_f64().ok_or_else(internal)
}

fn number(value: f64) -> Done<Value> {
    serde_json::Number::from_f64(value).map(Value::Number).ok_or_else(internal)
}

/// `router.configured(conn)`: the saved tier models.
fn configured(conn: &Connection) -> Done<Map<String, Value>> {
    let row = db::row(conn, "SELECT rika_fast_model,rika_standard_model,rika_deep_model FROM ai_settings WHERE id=1", [])?.ok_or_else(internal)?;
    Ok(TIERS.iter().zip(row.into_iter().map(|(_, value)| value)).map(|(tier, value)| ((*tier).to_owned(), value)).collect())
}

/// `router.defaults(conn)`: the defaults for the main model's provider family.
fn defaults(model: &Value) -> Done<Value> {
    let main = model.as_str().ok_or_else(internal)?;
    let family = main.split_once('/').map_or("", |(family, _)| family);
    let models = DEFAULTS.iter().find(|(name, _)| *name == family).unwrap_or(&DEFAULTS[3]).1;
    Ok(Value::Object(TIERS.iter().zip(models).map(|(tier, model)| ((*tier).to_owned(), json!(model))).collect()))
}

/// `float(text)` of the stored budget (Python's `float` takes what `Decimal` takes here).
fn budget(value: &Value) -> Done<f64> {
    match value {
        Value::Number(number) => number.as_f64().ok_or_else(internal),
        Value::String(text) => decimal::parse(text).and_then(|parsed| parsed.to_f64()).ok_or_else(internal),
        _ => Err(internal()),
    }
}

/// `web_ai._status`.
pub fn status(ctx: &Ctx) -> Done<Value> {
    let conn = ctx.conn;
    let row = settings(conn)?;
    let field = |key: &str| row.get(key).cloned().ok_or_else(internal);
    let configured_key = !crate::keys::read("OPENROUTER").map_err(keys_failure)?.is_empty();
    let model = field("model")?;
    Ok(json!({
        "configured": configured_key, "model": model, "routing": field("routing")?,
        "max_output_tokens": field("max_output_tokens")?, "daily_budget_usd": number(budget(&field("daily_budget_usd")?)?)?,
        "spent_today": number(spent(conn)?)?, "revision": field("revision")?,
        "rika_tiers": configured(conn)?, "rika_tier_defaults": defaults(&model)?,
        "models": db::rows(conn, "SELECT * FROM ai_models ORDER BY name", [])?,
        "privacy_notice": PRIVACY_NOTICE,
    }))
}

fn listed(conn: &Connection, model: &str) -> Done<bool> {
    db::exists(conn, "SELECT 1 FROM ai_models WHERE id=?", [model])
}

/// `POST /api/ai/settings`, in `web_ai.post`'s order of checks.
pub fn save(ctx: &Ctx, body: &Map<String, Value>) -> Done<Value> {
    let conn = ctx.conn;
    if body.keys().any(|key| !FIELDS.contains(&key.as_str())) {
        return Err(fail(400, "Unknown AI request field"));
    }
    if body.get("confirmed") != Some(&Value::Bool(true)) {
        return Err(fail(400, "Confirm the AI settings change"));
    }
    let row = settings(conn)?;
    let stored = |key: &str| row.get(key).cloned().ok_or_else(internal);
    let given = |key: &str| -> Done<Value> { body.get(key).cloned().map_or_else(|| stored(key), Ok) };
    let revision = body.get("revision").filter(|value| value.is_i64() || value.is_u64());
    let current = stored("revision")?;
    if !revision.is_some_and(|value| py::eq(value, &current)) {
        return Err(fail(409, "AI settings changed; reload before saving"));
    }
    let model = given("model")?;
    let model = match model.as_str() {
        Some(model) if model.is_empty() || listed(conn, model)? => model.to_owned(),
        _ => return Err(fail(400, "Choose a model from the refreshed list")),
    };
    let routing = given("routing")?;
    let routing = match &routing {
        Value::String(text) if matches!(text.as_str(), "price" | "latency" | "throughput") => text.clone(),
        // `routing not in {...}` hashes the value: a list or dict raises TypeError.
        Value::Array(_) | Value::Object(_) => return Err(internal()),
        _ => return Err(fail(400, "Choose price, latency, or throughput routing")),
    };
    let maximum = given("max_output_tokens")?.as_i64().filter(|value| (128..=8192).contains(value))
        .ok_or_else(|| fail(400, "Output limit must be 128–8192 tokens"))?;
    let cap = given("daily_budget_usd")?;
    if cap.is_boolean() {
        return Err(fail(400, "Daily budget must be a finite number"));
    }
    let cap = decimal_of(&cap).ok_or_else(|| fail(400, "Daily budget must be a finite number"))?;
    if !cap.within(100) {
        return Err(fail(400, "Daily budget must be between 0 and 100"));
    }
    let cap = cap.to_f64().ok_or_else(internal)?;
    let mut tiers = configured(conn)?;
    let empty = Value::Object(Map::new());
    let chosen = body.get("rika_tiers").unwrap_or(&empty);
    let Some(chosen) = chosen.as_object().filter(|chosen| chosen.keys().all(|tier| TIERS.contains(&tier.as_str()))) else {
        return Err(fail(400, "Rika tiers are fast, standard and deep"));
    };
    for value in chosen.values() {
        match value.as_str() {
            Some(model) if model.is_empty() || listed(conn, model)? => {}
            _ => return Err(fail(400, "Choose each Rika tier model from the refreshed list, or none for the default")),
        }
    }
    for (tier, value) in chosen {
        tiers.insert(tier.clone(), value.clone());
    }
    let truthy = |key: &str| body.get(key).is_some_and(py::truthy);
    if truthy("key") && truthy("remove_key") {
        return Err(fail(400, "Cannot add and remove the key together"));
    }
    if truthy("key") {
        let key = body["key"].as_str().filter(|key| (10..=512).contains(&key.len())
            && key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-'))
            .ok_or_else(|| fail(400, "Enter a valid OpenRouter API key"))?;
        crate::keys::set("OPENROUTER", key).map_err(keys_failure)?;
    } else if body.get("remove_key") == Some(&Value::Bool(true)) {
        crate::keys::remove("OPENROUTER").map_err(keys_failure)?;
    }
    use crate::builds::value::{text, Int, Value as Build};
    let tier = |name: &str| -> Done<Build> { tiers.get(name).and_then(Value::as_str).map(|model| Build::Str(text(model))).ok_or_else(internal) };
    let values = vec![
        ("model", Build::Str(text(&model))), ("routing", Build::Str(text(&routing))),
        ("max_output_tokens", Build::Int(Int::Small(maximum.into()))), ("daily_budget_usd", Build::Float(cap)),
        ("rika_fast_model", tier("fast")?), ("rika_standard_model", tier("standard")?), ("rika_deep_model", tier("deep")?),
    ];
    crate::builds::edit_settings(&["settings", "ai"], values, |_| Ok(())).map_err(|(status, message)| fail(status, message))?;
    status(ctx)
}
