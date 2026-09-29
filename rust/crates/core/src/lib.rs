//! Pure PicoBot logic — everything that can be tested without a game,
//! a screen or a Pico. Modules arrive milestone by milestone (see
//! `docs/rust-migration.md`).

pub mod config;
pub mod error;
pub mod fileio;
pub mod json;
pub mod maps;
pub mod reach;
pub mod rotation;
pub mod skills;

pub use error::{Error, Result};
