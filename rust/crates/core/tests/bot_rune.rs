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
    b.cfg.rune_action = RuneAction::Approach;
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

/// The arrow presses after the latest rune-key press: (time, key).
fn answer_after_last_activation(b: &Sim) -> Vec<(f64, String)> {
    let last_y = b
        .pressed_at
        .iter()
        .rposition(|(_, k)| k == "y")
        .expect("activated");
    b.pressed_at[last_y + 1..]
        .iter()
        .filter(|(_, k)| ["up", "down", "left", "right"].contains(&k.as_str()))
        .take(4)
        .cloned()
        .collect()
}

#[test]
fn a_rune_is_solved_standing_on_it_then_farming_resumes() {
    let mut b = farming();
    b.rune = Some(RUNE);
    let mut m = Machine::default();
    run(&mut m, &mut b, 200, |m, b| {
        b.rune.is_none() && m.state == State::Grind
    });
    assert_eq!(m.state, State::Grind);
    assert!(b.rune.is_none());
    assert_eq!(b.activations, 1);
    assert!(b.log_has("Rune: read up down left right — answering"));
    assert!(b.log_has("Rune solved (attempt 1)"));
    // Answered like a person: a moment to read, then uneven gaps.
    let y_at = b.pressed_at.iter().find(|(_, k)| k == "y").unwrap().0;
    let ans = answer_after_last_activation(&b);
    let keys: Vec<&str> = ans.iter().map(|(_, k)| k.as_str()).collect();
    assert_eq!(keys, ["up", "down", "left", "right"]);
    assert!(ans[0].0 - y_at >= 0.4, "read for {:.2}s", ans[0].0 - y_at);
    let gaps: Vec<f64> = ans.windows(2).map(|w| w[1].0 - w[0].0).collect();
    assert!(gaps.iter().all(|g| (0.17..=0.7).contains(g)), "{gaps:?}");
    assert!(
        gaps.windows(2).any(|w| (w[0] - w[1]).abs() > 1e-6),
        "{gaps:?}"
    );
}

#[test]
fn a_failed_solve_wiggles_through_the_lock_and_retries() {
    let mut b = farming();
    b.rune = Some(RUNE);
    b.puzzle_fail = 1; // the first attempt fails whatever is pressed
    let mut m = Machine::default();
    run(&mut m, &mut b, 400, |m, b| {
        b.rune.is_none() && m.state == State::Grind
    });
    assert!(b.rune.is_none(), "{:?}", b.logs);
    assert_eq!(b.activations, 2);
    assert!(b.log_has("Rune: attempt 1 failed"));
    assert!(b.log_has("Rune solved (attempt 2)"));
    // The retry waited out the 3s lock.
    let ys: Vec<f64> = b
        .pressed_at
        .iter()
        .filter(|(_, k)| k == "y")
        .map(|(t, _)| *t)
        .collect();
    let first_fail = b
        .pressed_at
        .iter()
        .find(|(t, k)| *t > ys[0] && ["up", "down", "left", "right"].contains(&k.as_str()))
        .unwrap()
        .0;
    let retry = *ys.last().unwrap();
    assert!(
        retry >= first_fail + 3.0,
        "retried {:.2}s after failing",
        retry - first_fail
    );
    // Wiggled meanwhile: direction taps between the failure and the retry.
    let taps = b
        .pressed_at
        .iter()
        .filter(|(t, k)| *t > first_fail && *t < retry && (k == "left" || k == "right"))
        .count();
    assert!(taps >= 2, "{taps} taps");
}

#[test]
fn an_unreadable_puzzle_is_given_up_with_a_pause() {
    let mut b = farming();
    b.rune = Some(RUNE);
    b.puzzle_hidden = true;
    let mut m = Machine::default();
    run(&mut m, &mut b, 400, |m, _| m.state == State::Pause);
    assert_eq!(m.state, State::Pause);
    assert!(b.log_has("Rune: couldn't read its arrows — pausing"));
    assert_eq!(b.state.viz.hazard.as_deref(), Some("rune"));
    // No arrow was ever pressed into an unread puzzle.
    let arrows_while_open = b
        .pressed_at
        .iter()
        .filter(|(_, k)| k == "up" || k == "down")
        .count();
    assert_eq!(arrows_while_open, 0);
}
