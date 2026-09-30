//! The GRIND / TRAVEL / PAUSE state machine on the simulator.

mod sim;

use picobot_core::bot::{Machine, State};
use sim::*;

fn anchored() -> Sim {
    Sim::patrol(&[FLOOR], (10.0, 100.0), &[(20.0, 100.0), (180.0, 100.0)])
}

#[test]
fn runs_grind_until_stopped_then_releases_keys() {
    let mut b = Sim::new(&[FLOOR], (30.0, 100.0));
    b.stop_at = 2.0;
    Machine::default().run(&mut b);
    assert!(b.log_has("GRIND: farming"));
    assert_eq!(b.state.viz.state, "STOPPED");
    assert!(b.held.is_none());
}

#[test]
fn an_unfocused_window_pauses_and_resumes() {
    let mut b = Sim::new(&[FLOOR], (30.0, 100.0));
    let mut m = Machine::default();
    b.focus = false;
    assert!(m.switch(&mut b));
    assert_eq!(m.state, State::Pause);
    m.execute(&mut b);
    assert!(!m.switch(&mut b)); // still unfocused
    b.focus = true;
    assert!(m.switch(&mut b));
    assert_eq!(m.state, State::Grind);
    assert!(!m.switch(&mut b)); // nothing left to resume
}

#[test]
fn a_hazard_pauses_with_an_alert() {
    let mut b = Sim::new(&[FLOOR], (30.0, 100.0));
    b.hazard = Some("rune".into());
    let mut m = Machine::default();
    assert!(m.switch(&mut b));
    assert_eq!(m.state, State::Pause);
    assert!(b.log_has("Rune detected — pausing"));
    assert!(!m.switch(&mut b)); // stays while it persists
    b.hazard = None;
    assert!(m.switch(&mut b));
    assert_eq!(m.state, State::Grind);
}

#[test]
fn a_long_pause_raises_the_watchdog_alert() {
    let mut b = Sim::new(&[FLOOR], (30.0, 100.0));
    b.focus = false;
    b.stop_at = 100.0;
    Machine::default().run(&mut b);
    assert!(b.log_has("Paused for"));
}

#[test]
fn grind_without_a_rotation_never_leaves_grind() {
    let mut b = Sim::new(&[FLOOR], (30.0, 100.0));
    b.state.travel_target = Some(0);
    let mut m = Machine::default();
    assert!(!m.switch(&mut b));
    assert_eq!(m.state, State::Grind);
}

#[test]
fn a_queued_travel_runs_then_hands_back_to_grind() {
    let mut b = anchored();
    b.state.travel_target = Some(1);
    let mut m = Machine::default();
    assert!(m.switch(&mut b));
    assert_eq!(m.state, State::Travel);
    assert!(b.log_has("TRAVEL: → a1"));
    m.execute(&mut b);
    assert_eq!(b.state.anchor_idx, 1);
    assert!(m.switch(&mut b));
    assert_eq!(m.state, State::Grind);
}

#[test]
fn grind_with_anchors_runs_the_patrol() {
    let mut b = anchored();
    let mut m = Machine::default();
    for _ in 0..6 {
        m.execute(&mut b);
    }
    assert!(b.log_has("Patrol plan"));
    assert!(b.log_has("Checkpoint:"));
    assert!(b.state.viz.patrol.is_some());
}
