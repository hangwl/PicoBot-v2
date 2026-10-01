//! The Python host's navigator tests, ported onto the simulator.

mod sim;

use std::sync::Arc;

use picobot_core::bot::{Navigator, StepStatus};
use picobot_core::navgraph::{Leg, MoveKind, NavGraph};
use picobot_core::reach::{Move, Reach};
use rand::rngs::StdRng;
use rand::SeedableRng;
use sim::*;

fn nav(g: Arc<NavGraph>) -> Navigator {
    Navigator::new(g, &mut StdRng::seed_from_u64(0))
}

fn no_rope(mut s: Sim) -> Sim {
    s.rope = 0.0;
    s
}

#[test]
fn climbs_with_up_flashes() {
    let mut b = no_rope(Sim::new(&[FLOOR, MID, TOP], (10.0, 100.0)));
    assert!(nav(b.graph()).go(&mut b, (80.0, 66.0), 3, 40));
    assert_eq!(b.pos, (80.0, 66.0));
    assert_eq!(b.moves, ["up_flash", "up_flash"]);
}

#[test]
fn rope_lift_preferred_when_ready() {
    let mut b = Sim::new(&[FLOOR, MID], (60.0, 100.0));
    assert!(nav(b.graph()).go(&mut b, (80.0, 84.0), 3, 40));
    assert_eq!(b.moves, ["rope_lift"]);
}

#[test]
fn descends() {
    let mut b = Sim::new(&[FLOOR, MID, TOP], (80.0, 66.0));
    assert!(nav(b.graph()).go(&mut b, (10.0, 100.0), 3, 40));
    assert_eq!(b.pos.1, 100.0);
}

#[test]
fn flash_and_double_flash_gaps() {
    let mut b = Sim::new(&[MID, SIDE], (60.0, 84.0));
    assert!(nav(b.graph()).go(&mut b, (170.0, 84.0), 3, 40));
    assert_eq!(b.moves, ["flash"]);
    let mut b = Sim::new(&[MID, [150.0, 84.0, 190.0, 84.0]], (60.0, 84.0));
    assert!(nav(b.graph()).go(&mut b, (170.0, 84.0), 3, 40));
    assert_eq!(b.moves, ["double_flash"]);
}

#[test]
fn up_side_flash() {
    let mut b = Sim::new(
        &[[0.0, 100.0, 100.0, 100.0], [115.0, 86.0, 160.0, 86.0]],
        (50.0, 100.0),
    );
    assert!(nav(b.graph()).go(&mut b, (140.0, 86.0), 3, 40));
    assert_eq!(b.moves, ["up_side_flash"]);
}

#[test]
fn a_cooling_rope_lift_is_not_waited_for() {
    let mut b = Sim::new(&[FLOOR, MID], (80.0, 100.0));
    b.rope_cd = 0.5;
    assert!(nav(b.graph()).go(&mut b, (80.0, 84.0), 3, 40));
    assert_eq!(b.moves, ["up_flash"]);
}

#[test]
fn a_long_rope_cooldown_excludes_rope_lift() {
    let mut b = Sim::new(&[FLOOR, [60.0, 70.0, 100.0, 70.0]], (80.0, 100.0));
    b.rope_cd = 10.0;
    assert_eq!(
        nav(b.graph()).step(&mut b, (80.0, 70.0)),
        StepStatus::NoRoute
    );
    assert!(b.moves.is_empty());
}

#[test]
fn a_step_is_one_move_and_replans_after_a_miss() {
    let mut b = no_rope(Sim::new(&[FLOOR, MID, TOP], (80.0, 100.0)));
    b.fizzle = 1;
    let n = nav(b.graph());
    assert_eq!(n.step(&mut b, (80.0, 66.0)), StepStatus::Failed); // fizzled up flash
    assert_eq!(b.pos.1, 100.0);
    assert_eq!(n.step(&mut b, (80.0, 66.0)), StepStatus::Moved); // retried from the floor
    assert_eq!(n.step(&mut b, (80.0, 66.0)), StepStatus::Arrived);
}

#[test]
fn a_tiny_leading_walk_does_not_stall() {
    let mut b = no_rope(Sim::new(&[FLOOR, MID], (78.0, 100.0)));
    let n = nav(b.graph());
    let statuses: Vec<StepStatus> = (0..3).map(|_| n.step(&mut b, (80.0, 84.0))).collect();
    assert!(statuses.contains(&StepStatus::Arrived));
    assert_eq!(b.moves, ["up_flash"]);
}

#[test]
fn landing_outcomes_teach_reach() {
    let mut b = Sim::new(&[MID, SIDE], (60.0, 84.0));
    b.flash = 30.0;
    assert!(nav(b.graph()).go(&mut b, (170.0, 84.0), 3, 40));
    assert!(b.state.reach.get(Move::Flash).dx > 25.0);
}

#[test]
fn short_real_reach_shrinks_the_model_and_gives_up() {
    let mut b = no_rope(Sim::new(&[FLOOR, MID], (60.0, 100.0)));
    b.up = 5.0;
    assert!(!nav(b.graph()).go(&mut b, (80.0, 84.0), 2, 40));
    assert!(b.state.reach.get(Move::UpFlash).rise < 20.0);
    assert!(b.log_has("giving up"));
}

#[test]
fn landing_tolerance_off_the_drawn_row() {
    let b = Sim::new(&[FLOOR, [60.0, 70.0, 100.0, 70.0]], (10.0, 100.0));
    let g = b.graph();
    let n = nav(g.clone());
    let top = g.locate(80.0, 70.0);
    assert!(g.locate(80.0, 79.0).is_none()); // 9px off: snap fails
    assert!(n.on_platform((80.0, 79.0), top));
    assert!(!n.on_platform((80.0, 100.0), top));
}

#[test]
fn a_gap_jump_releases_when_the_flash_fizzles() {
    let mut b = Sim::new(&[MID, SIDE], (60.0, 84.0));
    b.flash = 0.0;
    assert!(!nav(b.graph()).go(&mut b, (170.0, 84.0), 1, 3));
    assert!(b.clock < 8.0); // failed fast
    assert!(b.ups.contains(&"right".to_owned())); // direction released
}

#[test]
fn a_failed_rope_grab_exits_the_rope() {
    let mut b = Sim::new(&[FLOOR, [80.0, 40.0, 120.0, 40.0]], (20.0, 100.0));
    b.ropes = vec![[98.0, 80.0, 102.0, 40.0]];
    b.fail_rope = true;
    assert!(!nav(b.graph()).go(&mut b, (100.0, 40.0), 1, 2));
    assert!(b.moves.contains(&"rope_exit".to_owned()));
}

#[test]
fn a_teleport_leg_executes() {
    let plats = [FLOOR, MID, [140.0, 84.0, 190.0, 84.0]];
    let mut b = no_rope(Sim::new(&plats, (60.0, 84.0)).with_reach(reach_with(&[
        (
            Move::Teleport,
            Reach {
                dx: 30.0,
                rise: 12.0,
            },
        ),
        (Move::RopeLift, Reach { dx: 0.0, rise: 0.0 }),
    ])));
    b.opts.allow_flash = false;
    b.opts.allow_teleport = true;
    assert!(nav(b.graph()).go(&mut b, (170.0, 84.0), 3, 40));
    assert!(b.moves.iter().any(|m| m.starts_with("teleport")));
}

#[test]
fn rope_lift_fires_without_a_precise_stop() {
    let mut b = Sim::new(&[FLOOR, [60.0, 70.0, 100.0, 70.0]], (20.0, 100.0));
    assert!(nav(b.graph()).go(&mut b, (80.0, 70.0), 3, 40));
    assert_eq!(b.moves, ["rope_lift"]);
}

#[test]
fn a_rope_chain_replans_while_the_lift_cools() {
    let mut b = Sim::new(&[FLOOR, MID, TOP], (10.0, 100.0));
    assert!(nav(b.graph()).go(&mut b, (80.0, 66.0), 3, 40));
    assert_eq!(b.moves, ["rope_lift", "up_flash"]);
}

#[test]
fn the_route_is_published_for_the_dashboard() {
    let mut b = no_rope(Sim::new(&[FLOOR, MID], (10.0, 100.0)));
    nav(b.graph()).step(&mut b, (80.0, 84.0));
    let route = b.state.viz.route.clone().unwrap();
    assert!(route.iter().any(|(k, ..)| k == "up_flash"));
}

#[test]
fn a_rope_lift_wind_up_is_not_a_landing() {
    // Stands still while the rope grapples, rises, hangs at the top in the
    // air (steady but off every platform), then lands on MID.
    let mut b = Sim::new(&[FLOOR, MID], (80.0, 100.0));
    b.script = std::iter::repeat_n((80.0, 100.0), 12)
        .chain([
            (80.0, 95.0),
            (80.0, 88.0),
            (80.0, 80.0),
            (80.0, 74.0),
            (80.0, 74.0),
            (80.0, 74.0),
            (80.0, 78.0),
            (80.0, 84.0),
        ])
        .collect();
    let leg = Leg {
        kind: MoveKind::RopeLift,
        x0: 80.0,
        y0: 100.0,
        x1: 80.0,
        y1: 84.0,
        cost: 0.5,
    };
    let n = nav(b.graph());
    assert_eq!(
        n.execute_leg(&mut b, &leg),
        picobot_core::bot::LegStatus::Ok
    );
    assert_eq!(b.pos, (80.0, 84.0));
}

#[test]
fn a_move_that_never_leaves_is_a_miss_after_the_takeoff_wait() {
    let mut b = Sim::new(&[FLOOR, MID], (80.0, 100.0));
    b.script = [(80.0, 100.0)].into();
    let leg = Leg {
        kind: MoveKind::RopeLift,
        x0: 80.0,
        y0: 100.0,
        x1: 80.0,
        y1: 84.0,
        cost: 0.5,
    };
    assert_eq!(
        nav(b.graph()).execute_leg(&mut b, &leg),
        picobot_core::bot::LegStatus::Failed
    );
    assert!(b.slept < 3.0); // bounded wait
}

fn tap_table(b: &mut Sim) {
    let rows: Vec<serde_json::Value> = [(30, 1.5), (80, 4.0), (180, 9.0)]
        .iter()
        .map(|(ms, dx)| serde_json::json!({"ms": ms, "n": 4, "dx": dx, "sd": 0.3}))
        .collect();
    b.state.reach.set_profile("walk_taps", rows);
}

#[test]
fn a_hop_takeoff_is_not_tapped_in_even_with_a_tap_table() {
    let mut b = Sim::new(&[MID, SIDE], (50.0, 84.0));
    tap_table(&mut b);
    let leg = Leg {
        kind: MoveKind::Flash,
        x0: 60.0,
        y0: 84.0,
        x1: 140.0,
        y1: 84.0,
        cost: 0.8,
    };
    nav(b.graph()).execute_leg(&mut b, &leg);
    assert!(
        !b.presses.iter().any(|k| k == "left" || k == "right"),
        "takeoff alignment tapped: {:?}",
        b.presses
    );
}

#[test]
fn legs_are_counted_by_kind_in_the_session() {
    let mut b = Sim::new(&[FLOOR, MID], (60.0, 100.0));
    assert!(nav(b.graph()).go(&mut b, (80.0, 84.0), 3, 40));
    assert_eq!(b.state.session.moves_line(), "rope_lift 1 · walk 1");
    assert!(b
        .state
        .session
        .summary(10.0)
        .contains("moves: rope_lift 1 · walk 1"));
}
