//! Pure PicoBot logic — everything that can be tested without a game,
//! a screen or a Pico. Modules arrive milestone by milestone (see
//! `docs/rust-migration.md`).

pub mod anchor_stats;
pub mod config;
pub mod error;
pub mod fileio;
pub mod json;
pub mod maps;
pub mod navgraph;
pub mod planner;
pub mod platform_fit;
pub mod reach;
pub mod rotation;
pub mod skills;
pub mod summons;
pub mod timing;

pub use error::{Error, Result};
