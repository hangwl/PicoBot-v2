//! Pure PicoBot logic — everything that can be tested without a game,
//! a screen or a Pico. Modules arrive milestone by milestone (see
//! `docs/rust-migration.md`).

// `BotConfig::snapshot` is one large `json!` literal.
#![recursion_limit = "256"]

pub mod anchor_stats;
pub mod bot;
pub mod config;
pub mod effects;
pub mod error;
pub mod fileio;
pub mod fuzzy;
pub mod identity;
pub mod json;
pub mod layout;
pub mod maps;
pub mod minimap;
pub mod navgraph;
pub mod planner;
pub mod platform_fit;
pub mod reach;
pub mod rotation;
pub mod rune;
pub mod rune_arrows;
pub mod skills;
pub mod summons;
pub mod taps;
pub mod timing;
pub mod title;
pub mod vision;

pub use error::{Error, Result};
