//! The block engine (`kitty.core.blocks`) and the block routes the front answers: the
//! editor's page load and live transactions, history and Trash, agent sessions, pinned
//! citations, the block API (`kitty.core.block_api`) and quick-note writes.
//!
//! - `order`, `doc`, `crdt`, `store`, `tagging`, `searchdocs`, `actor`: the engine
//!   (fractional keys, page documents and operations, Loro mirroring, transactions, auto-tags,
//!   search documents, who wrote a row);
//! - `text`, `difflib`, `tables`, `json`: Python's `re` expressions, `difflib`, `str` facts and
//!   `json.dumps` text the engine depends on;
//! - `routes`, `live`, `api`, `history`, `sessions`, `quick`: what the routes answer.

pub mod actor;
pub mod api;
pub mod crdt;
pub mod difflib;
pub mod doc;
pub mod history;
pub mod json;
pub mod live;
pub mod order;
pub mod quick;
pub mod routes;
pub mod searchdocs;
pub mod sessions;
pub mod store;
pub mod tables;
pub mod tagging;
pub mod text;
