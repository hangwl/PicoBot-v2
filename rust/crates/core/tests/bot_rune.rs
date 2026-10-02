//! Runes on the simulator: the detour to stand beside one, the pause that
//! holds while the player covers it, and the fallbacks.

mod sim;

use picobot_core::bot::{Dir, Machine, State};
use picobot_core::config::RuneAction;
use picobot_core::rune::gap;
use sim::*;

/// A 6x6 rune standing on MID (row 84), centred on x 80.
const RUNE: (i32, i32, i32, i32) = (78, 79, 83, 84);

fn farming() -> Sim {
    Sim::patrol(
        &[FLOOR, MID],
        (10.0, 100.0),
        &[(20.0, 100.0), (180.0, 100.0)],
    )
}

/// Run the machine until `done` holds (or `n` rounds).
fn run(m: &mut Machine, b: &mut Sim, n: usize, done: impl Fn(&Machine, &Sim) -> bool) {
    for _ in 0..n {
        if done(m, b) {
            return;
        }
        if !m.switch(b) {
            m.execute(b);
        }
    }
}

#[test]
fn a_rune_is_walked_to_then_held_beside_until_it_is_gone() {
    let mut b = farming();
    let mut m = Machine::default();
    b.rune = Some(RUNE);
    assert!(m.switch(&mut b));
    assert_eq!(m.state, State::Rune);
    assert!(b.log_has("Rune spotted at (80, 82)"));
    run(&mut m, &mut b, 60, |m, _| m.state == State::Pause);
    assert_eq!(m.state, State::Pause);
    assert!(b.log_has("Rune reached"));
    // Beside it on its platform, glyphs touching, facing it.
    assert_eq!(b.pos.1, 84.0);
    assert_eq!(gap(b.pos.0.round() as i32, RUNE), Some(0), "at {:?}", b.pos);
    let toward = if b.pos.0 < 80.0 {
        Dir::Right
    } else {
        Dir::Left
    };
    assert_eq!(b.facing, Some(toward));
    assert_eq!(b.state.viz.hazard.as_deref(), Some("rune"));
    let events: Vec<&str> = b.rune_events.iter().map(|e| e.0.as_str()).collect();
    assert_eq!(events, ["seen", "arrived"]);
    // Still there: hold.
    for _ in 0..10 {
        assert!(!m.switch(&mut b));
    }
    // Solved: a few reads without it, then back to farming.
    b.rune = None;
    run(&mut m, &mut b, 20, |m, _| m.state != State::Pause);
    assert_eq!(m.state, State::Grind);
}

#[test]
fn a_rune_under_the_player_keeps_the_pause() {
    let mut b = farming();
    b.cfg.rune_action = RuneAction::Pause;
    b.pos = (80.0, 84.0);
    b.rune = Some(RUNE);
    let mut m = Machine::default();
    // Seen from beside it first, then the player steps onto it.
    b.pos = (60.0, 84.0);
    assert!(m.switch(&mut b));
    assert_eq!(m.state, State::Pause);
    b.pos = (80.0, 84.0); // covers it: the dot doesn't show
    for _ in 0..20 {
        assert!(!m.switch(&mut b), "resumed while the rune was covered");
    }
    // Off it, and the rune really is gone.
    b.pos = (60.0, 84.0);
    b.rune = None;
    run(&mut m, &mut b, 20, |m, _| m.state != State::Pause);
    assert_eq!(m.state, State::Grind);
}

#[test]
fn a_rune_off_every_platform_falls_back_to_a_pause() {
    let mut b = farming();
    b.rune = Some((78, 20, 83, 25)); // nothing drawn under it
    let mut m = Machine::default();
    run(&mut m, &mut b, 10, |m, _| m.state == State::Pause);
    assert_eq!(m.state, State::Pause);
    assert!(b.log_has("isn't over a drawn platform — pausing"));
    assert_eq!(b.rune_events.last().map(|e| e.0.as_str()), Some("failed"));
}

#[test]
fn runes_are_ignored_when_turned_off() {
    let mut b = farming();
    b.cfg.stop_when_rune_appears = false;
    b.rune = Some(RUNE);
    let mut m = Machine::default();
    for _ in 0..5 {
        if !m.switch(&mut b) {
            m.execute(&mut b);
        }
    }
    assert_eq!(m.state, State::Grind);
    assert!(!b.log_has("Rune"));
}
