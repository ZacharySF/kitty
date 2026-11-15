//! `schema_maps.network`: the saved maps merged into one read-only network. Nodes sharing a
//! source record unify across maps; unsourced nodes stay per map unless labels are merged;
//! connections with the same merged endpoints and label collapse into one weighted edge.
//! Clusters are connected components, numbered by descending size then smallest node id.

use std::collections::{BTreeSet, HashMap};

use rusqlite::params_from_iter;
use serde_json::{json, Value};

use super::{at, bad, identity, out_of_range, string, Maps, Obj, MAX_MAPS, MODES};
use crate::ported::{db, internal, py, Done};

struct Node {
    id: String,
    kind: Value,
    label: Value,
    source: Value,
    live: Value,
    url: Value,
    maps: Vec<i64>,
    members: Vec<Value>,
    degree: i64,
    cluster: usize,
}

struct Edge {
    id: String,
    from: usize,
    to: usize,
    label: Value,
    weight: i64,
    maps: Vec<i64>,
    members: Vec<Value>,
}

/// `' '.join(label.casefold().split())`.
fn normalized(label: &str) -> String {
    py::casefold(label).split(py::is_space).filter(|word| !word.is_empty()).collect::<Vec<_>>().join(" ")
}

/// `str(value)` inside a node key: a kind, or a source's id.
fn shown(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => py::dumps(other),
    }
}

fn list(value: &Value) -> Done<&Vec<Value>> {
    value.as_array().ok_or_else(internal)
}

fn object(value: &Value) -> Done<&Obj> {
    value.as_object().ok_or_else(internal)
}

fn root(parent: &mut [usize], mut key: usize) -> usize {
    while parent[key] != key {
        parent[key] = parent[parent[key]];
        key = parent[key];
    }
    key
}

pub(super) fn network(maps: &Maps, mode: Option<&str>, map_ids: Option<&[i128]>, merge_labels: bool) -> Done<Value> {
    if mode.is_some_and(|mode| !MODES.contains(&mode)) {
        return Err(bad("Mode must be study, general or jobs"));
    }
    let mut query = "SELECT id,title,mode,graph_json FROM schema_maps".to_owned();
    let (mut bound, mut clauses) = (Vec::new(), Vec::new());
    if let Some(given) = map_ids {
        if given.is_empty() || given.len() > MAX_MAPS {
            return Err(bad(format!("Choose between 1 and {MAX_MAPS} maps")));
        }
        let mut wanted = BTreeSet::new();
        for value in given {
            wanted.insert(i64::try_from(*value).ok().filter(|id| *id >= 1).ok_or_else(|| out_of_range("Map ID", 1))?);
        }
        let marks = vec!["?"; wanted.len()].join(",");
        let found: i64 = maps.conn.query_row(&format!("SELECT count(*) FROM schema_maps WHERE id IN ({marks})"), params_from_iter(wanted.iter()), |row| row.get(0))?;
        if found as usize != wanted.len() {
            return Err(bad("Map not found"));
        }
        clauses.push(format!("id IN ({marks})"));
        bound.extend(wanted.into_iter().map(rusqlite::types::Value::Integer));
    }
    if let Some(mode) = mode {
        clauses.push("mode=?".to_owned());
        bound.push(rusqlite::types::Value::Text(mode.to_owned()));
    }
    if !clauses.is_empty() {
        query.push_str(" WHERE ");
        query.push_str(&clauses.join(" AND "));
    }
    bound.push(rusqlite::types::Value::Integer(MAX_MAPS as i64));
    let rows = db::rows(maps.conn, &format!("{query} ORDER BY id LIMIT ?"), params_from_iter(bound))?;

    let (mut nodes, mut node_at): (Vec<Node>, HashMap<String, usize>) = (Vec::new(), HashMap::new());
    let (mut edges, mut edge_at): (Vec<Edge>, HashMap<(usize, usize, String), usize>) = (Vec::new(), HashMap::new());
    let mut sources: HashMap<String, Obj> = HashMap::new();
    let mut summaries = Vec::new();
    for row in &rows {
        let row_id = at(row, "id")?.as_i64().ok_or_else(internal)?;
        let document: Obj = serde_json::from_str(string(at(row, "graph_json")?)?)?;
        let (items, links) = (list(at(&document, "nodes")?)?, list(at(&document, "edges")?)?);
        summaries.push(json!({"id": row_id, "title": row["title"], "mode": row["mode"], "nodes": items.len(), "edges": links.len()}));
        let mut lookup: HashMap<&str, usize> = HashMap::new();
        for item in items {
            let item = object(item)?;
            let (source, item_id) = (at(item, "source")?, string(at(item, "id")?)?);
            let key = if !source.is_null() {
                let source = object(source)?;
                format!("{}:{}", shown(at(source, "kind")?), shown(at(source, "id")?))
            } else if merge_labels {
                format!("label:{}:{}", shown(at(item, "kind")?), normalized(string(at(item, "label")?)?))
            } else {
                format!("map:{row_id}:{item_id}")
            };
            let index = match node_at.get(&key) {
                Some(index) => {
                    let entry = &mut nodes[*index];
                    if !py::truthy(&entry.url) && py::truthy(at(item, "url")?) {
                        entry.url = at(item, "url")?.clone();
                    }
                    *index
                }
                None => {
                    let live = match source.as_object() {
                        Some(source) => {
                            let named = identity(source)?;
                            if !sources.contains_key(&named) {
                                sources.insert(named.clone(), maps.lookup(string(at(source, "kind")?)?, at(source, "id")?)?);
                            }
                            Some(&sources[&named])
                        }
                        None => None,
                    };
                    let preferred = |name: &str| -> Done<Value> {
                        Ok(live.and_then(|live| live.get(name)).filter(|value| py::truthy(value)).unwrap_or(at(item, name)?).clone())
                    };
                    nodes.push(Node { id: key.clone(), kind: at(item, "kind")?.clone(), label: preferred("label")?, source: source.clone(),
                                      live: live.map_or(Value::Null, |live| Value::Object(live.clone())), url: preferred("url")?,
                                      maps: Vec::new(), members: Vec::new(), degree: 0, cluster: 0 });
                    node_at.insert(key, nodes.len() - 1);
                    nodes.len() - 1
                }
            };
            lookup.insert(item_id, index);
            let entry = &mut nodes[index];
            if !entry.maps.contains(&row_id) {
                entry.maps.push(row_id);
            }
            let notes: String = string(at(item, "notes")?)?.chars().take(280).collect();
            entry.members.push(json!({"map_id": row_id, "node_id": item_id, "label": at(item, "label")?, "notes": notes}));
        }
        for item in links {
            let item = object(item)?;
            let start = *lookup.get(string(at(item, "from")?)?).ok_or_else(internal)?;
            let end = *lookup.get(string(at(item, "to")?)?).ok_or_else(internal)?;
            let key = (start, end, normalized(string(at(item, "label")?)?));
            let index = match edge_at.get(&key) {
                Some(index) => *index,
                None => {
                    edges.push(Edge { id: format!("e{}", edges.len()), from: start, to: end, label: at(item, "label")?.clone(), weight: 0,
                                      maps: Vec::new(), members: Vec::new() });
                    edge_at.insert(key, edges.len() - 1);
                    edges.len() - 1
                }
            };
            let entry = &mut edges[index];
            entry.weight += 1;
            if !entry.maps.contains(&row_id) {
                entry.maps.push(row_id);
            }
            entry.members.push(json!({"map_id": row_id, "edge_id": at(item, "id")?}));
        }
    }

    let mut parent: Vec<usize> = (0..nodes.len()).collect();
    for edge in &edges {
        nodes[edge.from].degree += 1;
        if edge.to != edge.from {
            nodes[edge.to].degree += 1;
        }
        let (a, b) = (root(&mut parent, edge.from), root(&mut parent, edge.to));
        if a != b {
            // The component is named by its smallest key.
            let (low, high) = if nodes[a].id < nodes[b].id { (a, b) } else { (b, a) };
            parent[high] = low;
        }
    }
    let mut order: Vec<usize> = (0..nodes.len()).collect();
    order.sort_by(|a, b| nodes[*a].id.cmp(&nodes[*b].id));
    let (mut components, mut component_at): (Vec<Vec<usize>>, HashMap<usize, usize>) = (Vec::new(), HashMap::new());
    for index in &order {
        let top = root(&mut parent, *index);
        let slot = *component_at.entry(top).or_insert_with(|| {
            components.push(Vec::new());
            components.len() - 1
        });
        components[slot].push(*index);
    }
    components.sort_by(|a, b| b.len().cmp(&a.len()).then_with(|| nodes[a[0]].id.cmp(&nodes[b[0]].id)));
    let mut clusters = Vec::new();
    for (index, keys) in components.iter().enumerate() {
        let hub = *keys.iter().min_by(|a, b| nodes[**b].degree.cmp(&nodes[**a].degree).then_with(|| nodes[**a].id.cmp(&nodes[**b].id))).ok_or_else(internal)?;
        for key in keys {
            nodes[*key].cluster = index;
        }
        clusters.push(json!({"id": index, "size": keys.len(), "hub": nodes[hub].id, "label": nodes[hub].label}));
    }
    let unified = nodes.iter().filter(|node| node.members.len() > 1).count();
    let edges: Vec<Value> = edges.into_iter().map(|edge| json!({"id": edge.id, "from": nodes[edge.from].id, "to": nodes[edge.to].id, "label": edge.label,
                                                                 "weight": edge.weight, "maps": edge.maps, "members": edge.members})).collect();
    let nodes: Vec<Value> = order.iter().map(|index| {
        let node = &nodes[*index];
        json!({"id": node.id, "kind": node.kind, "label": node.label, "source": node.source, "live": node.live, "url": node.url,
               "maps": node.maps, "members": node.members, "degree": node.degree, "cluster": node.cluster})
    }).collect();
    Ok(json!({"mode": mode, "merge_labels": merge_labels, "maps": summaries, "nodes": nodes, "edges": edges, "clusters": clusters, "unified": unified}))
}
