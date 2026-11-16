//! `kitty.core.math.topics`: the authored topic graph (`src/kitty/core/math/topics.json`,
//! compiled in; Python reads the same file), its validation and its sync into `math_topics`
//! and `math_prereqs`.

use std::collections::HashMap;
use std::sync::LazyLock;

use rusqlite::Connection;
use serde::Deserialize;

use crate::ported::db::transaction;
use crate::ported::{fail, Done};

const GRAPH: &str = include_str!("../../../src/kitty/core/math/topics.json");

#[derive(Deserialize)]
pub struct Topic {
    pub id: String,
    pub strand: String,
    pub title: String,
    pub prereqs: Vec<String>,
    /// Per-prerequisite FIRe-lite weight overrides (0 = prerequisite, not encompassed).
    #[serde(default)]
    pub credit: serde_json::Map<String, serde_json::Value>,
    /// The solution method; mixed review never puts two problems of one strategy in a row.
    pub strategy: String,
}

#[derive(Deserialize)]
struct Graph {
    strands: serde_json::Map<String, serde_json::Value>,
    credit: f64,
    topics: Vec<Topic>,
}

static LOADED: LazyLock<Graph> = LazyLock::new(|| serde_json::from_str(GRAPH).expect("topics.json is a valid topic graph"));
static BY_ID: LazyLock<HashMap<&'static str, &'static Topic>> = LazyLock::new(|| LOADED.topics.iter().map(|topic| (topic.id.as_str(), topic)).collect());

/// `TOPICS`, in curriculum order.
pub fn topics() -> &'static [Topic] {
    &LOADED.topics
}

/// `BY_ID.get(id)`.
pub fn by_id(id: &str) -> Option<&'static Topic> {
    BY_ID.get(id).copied()
}

impl Topic {
    /// `Topic.weight(prereq)`.
    pub fn weight(&self, prereq: &str) -> f64 {
        self.credit.get(prereq).and_then(serde_json::Value::as_f64).unwrap_or(LOADED.credit)
    }
}

/// `validate()`: topic ids in prerequisite order, or the `GraphError` message.
pub fn validate() -> Result<Vec<&'static str>, String> {
    let mut known: HashMap<&str, &Topic> = HashMap::new();
    for topic in topics() {
        if known.insert(&topic.id, topic).is_some() {
            return Err(format!("Duplicate topic {}", topic.id));
        }
        if !LOADED.strands.contains_key(&topic.strand) {
            return Err(format!("Topic {} has unknown strand {}", topic.id, topic.strand));
        }
    }
    for topic in topics() {
        if let Some(prereq) = topic.prereqs.iter().find(|prereq| !known.contains_key(prereq.as_str())) {
            return Err(format!("Topic {} needs unknown topic {prereq}", topic.id));
        }
        let bad_weight = topic.credit.iter().any(|(prereq, weight)| {
            !topic.prereqs.contains(prereq) || !weight.as_f64().is_some_and(|weight| (0.0..=1.0).contains(&weight))
        });
        if bad_weight {
            return Err(format!("Topic {} credits a non-prerequisite or a weight outside 0–1", topic.id));
        }
    }
    fn visit<'a>(id: &'a str, path: &mut Vec<&'a str>, known: &HashMap<&str, &'a Topic>, state: &mut HashMap<&'a str, bool>,
                 order: &mut Vec<&'a str>) -> Result<(), String> {
        match state.get(id) {
            Some(true) => return Ok(()),
            Some(false) => {
                let start = path.iter().position(|item| *item == id).unwrap_or(0);
                let mut cycle: Vec<&str> = path[start..].to_vec();
                cycle.push(id);
                return Err(format!("Prerequisite cycle: {}", cycle.join(" → ")));
            }
            None => {}
        }
        state.insert(id, false);
        path.push(id);
        for prereq in &known[id].prereqs {
            visit(prereq, path, known, state, order)?;
        }
        path.pop();
        state.insert(id, true);
        order.push(id);
        Ok(())
    }
    let (mut state, mut order) = (HashMap::new(), Vec::new());
    for topic in topics() {
        visit(&topic.id, &mut Vec::new(), &known, &mut state, &mut order)?;
    }
    Ok(order)
}

/// `sync(conn)`: make the tables match the authored graph; a no-op when they already do. A
/// graph error is Python's 500 with its message.
pub fn sync(conn: &Connection) -> Done<()> {
    validate().map_err(|message| fail(500, message))?;
    let want_topics: Vec<(String, String, String, i64)> = topics().iter().enumerate()
        .map(|(index, topic)| (topic.id.clone(), topic.strand.clone(), topic.title.clone(), index as i64)).collect();
    let mut want_edges: Vec<(String, String, f64)> = topics().iter()
        .flat_map(|topic| topic.prereqs.iter().map(move |prereq| (topic.id.clone(), prereq.clone(), topic.weight(prereq)))).collect();
    let order = |a: &(String, String, f64), b: &(String, String, f64)| (&a.0, &a.1).cmp(&(&b.0, &b.1)).then(a.2.total_cmp(&b.2));
    want_edges.sort_by(order);
    let mut statement = conn.prepare("SELECT id, strand, title, position FROM math_topics ORDER BY position")?;
    let have_topics = statement.query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)))?.collect::<Result<Vec<_>, _>>()?;
    let mut statement = conn.prepare("SELECT topic_id, prereq_id, credit FROM math_prereqs")?;
    let mut have_edges = statement.query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))?.collect::<Result<Vec<_>, _>>()?;
    have_edges.sort_by(order);
    if have_topics == want_topics && have_edges == want_edges {
        return Ok(());
    }
    transaction(conn, true, || {
        conn.execute("DELETE FROM math_prereqs", [])?;
        let keep: Vec<&str> = want_topics.iter().map(|row| row.0.as_str()).collect();
        let marks = vec!["?"; keep.len()].join(",");
        conn.execute(&format!("DELETE FROM math_topics WHERE id NOT IN ({marks})"), rusqlite::params_from_iter(keep.iter()))?;
        let mut insert = conn.prepare("INSERT INTO math_topics(id, strand, title, position) VALUES (?,?,?,?) \
            ON CONFLICT(id) DO UPDATE SET strand=excluded.strand, title=excluded.title, position=excluded.position")?;
        for row in &want_topics {
            insert.execute(rusqlite::params![row.0, row.1, row.2, row.3])?;
        }
        let mut insert = conn.prepare("INSERT INTO math_prereqs(topic_id, prereq_id, credit) VALUES (?,?,?)")?;
        for edge in &want_edges {
            insert.execute(rusqlite::params![edge.0, edge.1, edge.2])?;
        }
        Ok(())
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn the_authored_graph_is_valid() {
        let order = super::validate().unwrap();
        assert_eq!(order.len(), super::topics().len());
        assert_eq!(super::by_id("prob.bayes").unwrap().weight("prob.total-probability"), 0.5);
        assert_eq!(super::by_id("prob.bayes").unwrap().weight("arith.percentages"), 0.3);
    }
}
