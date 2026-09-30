//! The bot: a body with movement primitives, the navigator that runs graph
//! routes, the patrol loop, and the farming routines around them.

pub mod body;
pub mod flight;
pub mod grind;
pub mod machine;
pub mod measure;
pub mod navigator;
pub mod patrol;
pub mod watchdog;

pub use body::{Body, BotState, Dir, Keys, LegViz, PatrolStatus, Travel, Viz};
pub use flight::{Flight, FlightRecorder};
pub use machine::{Machine, State};
pub use measure::{MeasureStatus, Mode, MoveMeasurer, Recorder};
pub use navigator::{LegStatus, Navigator, StepStatus};
pub use patrol::Patrol;
