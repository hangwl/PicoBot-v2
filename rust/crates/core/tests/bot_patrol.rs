//! The Python host's patrol tests, ported onto the simulator.

mod sim;

use std::collections::HashSet;

use picobot_core::bot::{Body, Patrol};
use picobot_core::config::PatrolPolicy;
use picobot_core::navgraph::{Leg, MoveKind};
use picobot_core::planner::PlanSegment;
use picobot_core::reach::{Move, Reach};
use picobot_core::rotation::Step;
use sim::*;

fn ticks(p: &mut Patrol, b: &mut Sim, n: usize) {
    for _ in 0..n {
        p.tick(b);
    }
}

fn arrivals(b: &Sim) -> Vec<String> {
    b.logs
        .iter()
        .filter_map(|l| l.strip_prefix("Checkpoint: "))
        .map(str::to_owned)
        .collect()
}

/// No rope lift at all: the physics and the planner's reach (as the
/// Python sim's `rope=0`).
fn no_rope(s: Sim) -> Sim {
    let mut s = s.with_reach(reach_with(&[(
        Move::RopeLift,
        Reach { dx: 3.0, rise: 0.0 },
    )]));
    s.rope = 0.0;
    s
}

/// A plan that sends the bot up an up flash to a1 from (60, 100).
fn up_flash_plan() -> Vec<PlanSegment> {
    let leg = Leg {
        kind: MoveKind::UpFlash,
        x0: 60.0,
        y0: 100.0,
        x1: 80.0,
        y1: 84.0,
        cost: 1.0,
    };
    vec![PlanSegment {
        anchor: 1,
        legs: Some(vec![leg]),
        from: None,
        sweep: None,
    }]
}

fn weak_up_flash() -> Sim {
    let mut b = no_rope(Sim::patrol(
        &[FLOOR, MID],
        (60.0, 100.0),
        &[(20.0, 100.0), (80.0, 84.0)],
    ));
    b.up = 5.0;
    b
}

#[test]
fn plans_a_full_loop_and_publishes_the_traversal() {
    let mut b = no_rope(Sim::patrol(
        &[FLOOR, MID, TOP],
        (10.0, 100.0),
        &[(20.0, 100.0), (80.0, 66.0), (180.0, 100.0)],
    ));
    Patrol::default().tick(&mut b);
    let plan = b
        .logs
        .iter()
        .find_map(|l| l.strip_prefix("Patrol plan: "))
        .unwrap();
    let mut names: Vec<&str> = plan.split(" → ").collect();
    names.sort();
    assert_eq!(names, ["a0", "a1", "a2"]);
    let kinds: HashSet<String> = b
        .state
        .viz
        .plan
        .clone()
        .unwrap()
        .into_iter()
        .map(|l| l.0)
        .collect();
    assert!(kinds.contains("up_flash"));
}

#[test]
fn keeps_moving_through_every_anchor_then_replans() {
    let mut b = Sim::patrol(
        &[FLOOR, MID, TOP, SIDE],
        (10.0, 100.0),
        &[(20.0, 100.0), (80.0, 66.0), (170.0, 84.0)],
    );
    let mut p = Patrol::default();
    ticks(&mut p, &mut b, 60);
    let got = arrivals(&b);
    assert!(got.len() >= 5, "{got:?}");
    assert_eq!(
        got.iter().cloned().collect::<HashSet<_>>(),
        ["a0", "a1", "a2"].map(String::from).into()
    );
    assert!(!b.presses.contains(&"a".to_owned()) || !b.cfg.skills.is_empty());
}

#[test]
fn no_linger_moves_on_immediately() {
    let mut b = Sim::patrol(&[FLOOR], (10.0, 100.0), &[(20.0, 100.0), (180.0, 100.0)]);
    let mut p = Patrol::default();
    ticks(&mut p, &mut b, 12);
    assert!(arrivals(&b).len() >= 3);
}

#[test]
fn an_unreachable_anchor_is_skipped_not_stuck_on() {
    // a1 floats 60px above everything — no move reaches it.
    let mut b = Sim::patrol(
        &[FLOOR, [60.0, 40.0, 100.0, 40.0]],
        (10.0, 100.0),
        &[(20.0, 100.0), (80.0, 40.0), (180.0, 100.0)],
    );
    let mut p = Patrol::default();
    ticks(&mut p, &mut b, 12);
    assert!(p.plan.iter().all(|s| s.anchor != 1));
    assert!(b.log_has("no route from here"));
    assert!(b.state.bans.contains_key(&1));
}

#[test]
fn nothing_plannable_halts_all_actions() {
    let mut b = Sim::patrol(
        &[FLOOR, [60.0, 40.0, 100.0, 40.0]],
        (10.0, 100.0),
        &[(80.0, 40.0), (90.0, 40.0)],
    );
    let mut p = Patrol::default();
    ticks(&mut p, &mut b, 10);
    assert!(b.moves.is_empty() && b.presses.is_empty());
    assert!(b.slept >= 2.0); // taking a break
                             // Logged once per 5s of break, not every tick.
    let breaks = b
        .logs
        .iter()
        .filter(|l| l.contains("taking a break"))
        .count();
    assert!(
        (1..=(b.slept / 5.0) as usize + 1).contains(&breaks),
        "{breaks}"
    );
    assert!(b.state.viz.patrol.as_ref().unwrap().halted);
    assert!(b.state.viz.patrol.as_ref().unwrap().target.is_none());
}

#[test]
fn continues_after_the_first_arrival_near_a_takeoff() {
    // Arriving 2px from the next up-flash takeoff must not stall.
    let mut b = no_rope(Sim::patrol(
        &[FLOOR, MID],
        (10.0, 100.0),
        &[(78.0, 100.0), (80.0, 84.0)],
    ));
    b.cfg.patrol_policy = PatrolPolicy::Greedy;
    let mut p = Patrol::default();
    ticks(&mut p, &mut b, 6);
    assert!(b.moves.contains(&"up_flash".to_owned()));
    assert!(arrivals(&b).len() >= 2);
}

#[test]
fn anchor_stats_record_visits_misses_and_skips() {
    let mut b = weak_up_flash();
    let mut p = Patrol::default();
    ticks(&mut p, &mut b, 12);
    assert!(b.stat_count("visit", "a0") > 0);
    assert!(b.stat_count("miss", "a1") > 0); // can't reach MID
    assert!(b
        .stats
        .iter()
        .any(|s| s.0 == "skip" && s.1 == "a1" && s.2 == "unreachable after retries"));
}

#[test]
fn status_tracks_target_next_and_arrivals() {
    let mut b = Sim::patrol(
        &[FLOOR],
        (10.0, 100.0),
        &[(20.0, 100.0), (100.0, 100.0), (180.0, 100.0)],
    );
    b.cfg.patrol_policy = PatrolPolicy::Greedy;
    let mut p = Patrol::default();
    p.tick(&mut b);
    let st = b.state.viz.patrol.clone().unwrap();
    assert_eq!(st.target.as_deref(), Some("a0"));
    assert_eq!(st.next[..2], ["a1", "a2"]);
    assert_eq!((st.misses, st.arrived, st.halted), (0, 0, false));
    ticks(&mut p, &mut b, 8);
    let st = b.state.viz.patrol.clone().unwrap();
    assert!(st.arrived >= 2);
    assert!(["a0", "a1", "a2"].contains(&st.target.as_deref().unwrap()));
}

#[test]
fn status_counts_misses_on_a_rerouted_leg() {
    let mut b = weak_up_flash();
    let mut p = Patrol::default();
    p.plan = up_flash_plan();
    p.tick(&mut b);
    let st = b.state.viz.patrol.clone().unwrap();
    assert_eq!((st.target.as_deref(), st.misses), (Some("a1"), 1));
    assert!(matches!(st.move_kind.as_deref(), Some("walk" | "up_flash")));
}

#[test]
fn an_anchor_removed_mid_run_replans_instead_of_crashing() {
    let mut b = Sim::patrol(
        &[FLOOR],
        (10.0, 100.0),
        &[(20.0, 100.0), (100.0, 100.0), (180.0, 100.0)],
    );
    let mut p = Patrol::default();
    p.tick(&mut b);
    assert!(!p.plan.is_empty());
    b.edit_map(|e| {
        e.rotation.anchors.remove(2); // dashboard delete
    });
    b.state.bans.insert(2, 9e9);
    ticks(&mut p, &mut b, 6);
    assert!(b.log_has("anchors changed"));
    assert!(p.plan.iter().all(|s| s.anchor < 2));
    assert!(b.state.bans.is_empty());
}

#[test]
fn a_failed_leg_splices_a_reroute_then_two_misses_ban() {
    let mut b = weak_up_flash();
    let mut p = Patrol::default();
    p.plan = up_flash_plan();
    ticks(&mut p, &mut b, 2);
    assert!(!b.state.bans.contains_key(&1)); // only 1 miss so far
    p.tick(&mut b);
    assert!(b.state.bans.contains_key(&1)); // the 2nd miss bans
    assert_eq!(
        b.logs
            .iter()
            .filter(|l| l.contains("missed a landing"))
            .count(),
        2
    );
}

#[test]
fn a_blind_tick_neither_attacks_nor_moves() {
    let mut b = Sim::patrol(&[FLOOR], (10.0, 100.0), &[(20.0, 100.0), (180.0, 100.0)]);
    b.hidden = true;
    let mut p = Patrol::default();
    ticks(&mut p, &mut b, 5);
    assert!(b.moves.is_empty() && b.presses.is_empty() && b.downs.is_empty());
    assert!(b.log_has("Player dot not visible"));
}

#[test]
fn no_platforms_falls_back_to_the_straight_line_patrol() {
    let mut b = Sim::patrol(&[], (10.0, 40.0), &[(20.0, 100.0), (180.0, 100.0)]);
    Patrol::default().tick(&mut b);
    assert!(!b.log_has("not on any drawn platform"));
    assert!(!b.log_has("Patrol plan"));
}

#[test]
fn a_player_off_the_graph_waits() {
    // Platforms drawn, player mid-air: planning from there would ban every anchor.
    let mut b = Sim::patrol(&[FLOOR], (10.0, 40.0), &[(20.0, 100.0), (180.0, 100.0)]);
    Patrol::default().tick(&mut b);
    assert!(b.log_has("not on any drawn platform"));
    assert!(b.moves.is_empty() && b.state.bans.is_empty());
}

#[test]
fn each_spell_off_the_platforms_is_reported_once_with_its_context() {
    let mut b = Sim::patrol(
        &[FLOOR, MID],
        (80.0, 90.0),
        &[(20.0, 100.0), (180.0, 100.0)],
    );
    let mut p = Patrol::default();
    ticks(&mut p, &mut b, 3);
    assert_eq!(b.off.len(), 1);
    let (pos, info) = &b.off[0];
    assert_eq!(*pos, (80.0, 90.0));
    assert_eq!(info["pos"], serde_json::json!([80.0, 90.0]));
    assert_eq!(info["above"]["dy"], -6.0); // MID's row, 6px over the feet
    assert_eq!(info["below"]["dy"], 10.0); // the floor
    assert!(b.log_has("not on any drawn platform at (80, 90)"));
    b.pos = (80.0, 100.0); // back on the floor
    p.tick(&mut b);
    b.pos = (150.0, 60.0); // and off again
    p.tick(&mut b);
    assert_eq!(b.off.len(), 2);
}

fn rope_sim(pos: (f64, f64)) -> Sim {
    Sim::patrol(
        &[FLOOR, [40.0, 40.0, 160.0, 40.0]],
        pos,
        &[(20.0, 100.0), (180.0, 100.0)],
    )
}

/// One tick after holding still at `pos` for 3s.
fn stuck_tick(b: &mut Sim, pos: (f64, f64)) {
    let mut p = Patrol::default();
    p.stuck = Some((b.clock - 3.0, pos));
    p.tick(b);
}

fn ropes(b: &Sim) -> Vec<[f64; 4]> {
    b.entry.as_ref().unwrap().ropes.clone().unwrap_or_default()
}

#[test]
fn a_persistent_blind_dot_never_learns_or_leaps() {
    let mut b = rope_sim((60.0, 100.0));
    b.probe = Some(true);
    b.hidden = true;
    let mut p = Patrol::default();
    p.stuck = Some((-3.0, (60.0, 100.0)));
    ticks(&mut p, &mut b, 5);
    assert!(b.moves.is_empty());
    assert_eq!(b.saves, 0);
}

#[test]
fn a_confirmed_hang_learns_a_rope() {
    let mut b = rope_sim((60.0, 60.0));
    b.probe = Some(true);
    stuck_tick(&mut b, (60.0, 60.0));
    assert_eq!(b.moves, ["rope_exit"]);
    let r = ropes(&b);
    assert_eq!(r.len(), 1);
    assert_eq!(r[0][0], 0.3); // stuck x
    assert_eq!(r[0][3], (37.0f64 / 150.0 * 1e4).round() / 1e4);
    assert!(b.log_has("Learned a rope"));
}

#[test]
fn undrawn_ground_is_not_learned_as_a_rope() {
    let mut b = rope_sim((60.0, 60.0));
    b.probe = Some(false);
    stuck_tick(&mut b, (60.0, 60.0));
    assert!(ropes(&b).is_empty());
    assert_eq!(b.moves, ["rope_exit"]); // hop back
    assert!(b.log_has("not a rope"));
}

#[test]
fn an_unreadable_probe_does_nothing() {
    let mut b = rope_sim((60.0, 60.0));
    stuck_tick(&mut b, (60.0, 60.0));
    assert!(b.moves.is_empty());
}

#[test]
fn hangs_on_one_rope_extend_it() {
    let mut b = rope_sim((60.0, 60.0));
    b.probe = Some(true);
    stuck_tick(&mut b, (60.0, 60.0));
    b.pos = (61.0, 75.0); // lower on the same rope
    stuck_tick(&mut b, (61.0, 75.0));
    let r = ropes(&b);
    assert_eq!(r.len(), 1);
    assert_eq!(r[0][1], 0.5); // bottom grew
    assert_eq!(r[0][3], (37.0f64 / 150.0 * 1e4).round() / 1e4); // top kept
}

#[test]
fn a_learned_rope_keeps_its_bottom_off_the_platform_below() {
    let mut b = rope_sim((61.0, 70.0));
    let r4 = |v: f64| (v * 1e4).round() / 1e4;
    // A rope saved earlier that runs all the way onto FLOOR (y=100).
    b.edit_map(|e| e.ropes = Some(vec![[0.3, r4(100.0 / 150.0), 0.3, r4(40.0 / 150.0)]]));
    b.probe = Some(true);
    stuck_tick(&mut b, (61.0, 70.0));
    let r = ropes(&b);
    assert_eq!(r.len(), 1);
    assert_eq!(r[0][1], r4(95.0 / 150.0)); // 100 - 5px gap
}

#[test]
fn an_anchor_off_every_platform_is_banned_with_a_message() {
    let mut b = Sim::patrol(&[FLOOR], (10.0, 100.0), &[(20.0, 100.0), (95.0, 20.0)]);
    Patrol::default().tick(&mut b);
    assert!(b.state.bans.contains_key(&1));
    assert!(b.log_has("not on a drawn platform"));
}

#[test]
fn reachable_anchors_are_still_patrolled_past_a_bad_one() {
    let mut b = Sim::patrol(
        &[FLOOR, [60.0, 40.0, 100.0, 40.0]],
        (10.0, 100.0),
        &[(20.0, 100.0), (80.0, 40.0), (180.0, 100.0)],
    );
    let mut p = Patrol::default();
    ticks(&mut p, &mut b, 14);
    let got: HashSet<String> = arrivals(&b).into_iter().collect();
    assert_eq!(got, ["a0", "a2"].map(String::from).into());
}

#[test]
fn the_next_loop_is_planned_before_the_current_one_ends() {
    let mut b = no_rope(Sim::patrol(
        &[FLOOR, MID, TOP],
        (10.0, 100.0),
        &[(20.0, 100.0), (80.0, 66.0), (180.0, 100.0)],
    ));
    let mut p = Patrol::default();
    let (mut next_at, mut done_at) = (None, None);
    for i in 0..40 {
        p.tick(&mut b);
        if next_at.is_none() && b.count_logs("Next loop planned:") > 0 {
            next_at = Some(i);
        }
        if done_at.is_none() && arrivals(&b).len() >= 3 {
            done_at = Some(i);
        }
    }
    assert!(next_at.unwrap() < done_at.unwrap());
    assert!(arrivals(&b).len() >= 5);
    assert!(!b.log_has("taking a break"));
}

#[test]
fn a_recorded_leg_wins() {
    let mut b = Sim::patrol(&[FLOOR, MID], (20.0, 100.0), &[(20.0, 100.0), (80.0, 84.0)]);
    b.edit_map(|e| {
        e.rotation
            .legs
            .insert((0, 1), vec![Step::Wait { seconds: 0.5 }]);
    });
    let mut p = Patrol::default();
    p.tick(&mut b); // records the anchor signature
    p.plan = vec![PlanSegment {
        anchor: 1,
        legs: None,
        from: Some(0),
        sweep: None,
    }];
    p.seg = 0;
    let before = b.slept;
    p.tick(&mut b);
    assert!(b.moves.is_empty());
    assert!(b.slept - before >= 0.5);
    assert_eq!(arrivals(&b).last().map(String::as_str), Some("a1"));
}

#[test]
fn a_miss_reroutes_from_where_the_player_ended_up() {
    let mut b = weak_up_flash();
    let mut p = Patrol::default();
    p.plan = up_flash_plan();
    p.tick(&mut b); // the up flash falls short
    b.pos = (150.0, 100.0); // knocked along the floor meanwhile
    b.up = 20.0;
    p.tick(&mut b);
    let legs = p.plan[p.seg].legs.clone().unwrap();
    assert_eq!(legs[0].x0, 150.0);
    ticks(&mut p, &mut b, 3);
    assert_eq!(arrivals(&b).first().map(String::as_str), Some("a1"));
}

#[test]
fn a_lost_focus_mid_patrol_is_not_a_miss() {
    let mut b = weak_up_flash();
    let mut p = Patrol::default();
    p.plan = up_flash_plan();
    b.focus = false;
    ticks(&mut p, &mut b, 3);
    assert_eq!(p.fails, 0);
    assert!(!b.log_has("missed a landing"));
    assert!(b.state.bans.is_empty());
}

fn rope_legs(p: &Patrol) -> usize {
    p.plan
        .iter()
        .flat_map(|s| s.legs.iter().flatten())
        .filter(|l| l.kind == MoveKind::RopeLift)
        .count()
}

#[test]
fn an_unbound_rope_lift_is_never_planned() {
    let plats = [FLOOR, [20.0, 85.0, 60.0, 85.0], [140.0, 85.0, 180.0, 85.0]];
    let anchors = [(40.0, 85.0), (160.0, 85.0)];
    // The up flash only covers these 15px rises on unproven reach (12px
    // known, 1.3x exploration), so rope lift stays the way up.
    let unproven_up = || {
        let mut m = reach_with(&[(
            Move::UpFlash,
            Reach {
                dx: 6.0,
                rise: 12.0,
            },
        )]);
        m.explore = 1.3;
        m
    };
    let mut b = Sim::patrol(&plats, (100.0, 100.0), &anchors).with_reach(unproven_up());
    p_tick_count(&mut b, 1, |p| assert!(rope_legs(p) > 0)); // bound: preferred
    let mut b = Sim::patrol(&plats, (100.0, 100.0), &anchors).with_reach(unproven_up());
    b.rope = 0.0; // no key: never ready
    let mut p = Patrol::default();
    ticks(&mut p, &mut b, 16);
    assert_eq!(rope_legs(&p), 0);
    assert!(!b.moves.contains(&"rope_lift".to_owned()));
    assert!(arrivals(&b).len() >= 2);
    assert!(!b.log_has("missed"));
}

fn p_tick_count(b: &mut Sim, n: usize, check: impl Fn(&Patrol)) {
    let mut p = Patrol::default();
    ticks(&mut p, b, n);
    check(&p);
}

#[test]
fn the_pace_is_learned_from_segment_times_and_kept_across_a_reset() {
    let mut b = Sim::patrol(&[FLOOR], (10.0, 100.0), &[(20.0, 100.0), (180.0, 100.0)]);
    let mut p = Patrol::default();
    assert_eq!(p.pace(), 1.0);
    ticks(&mut p, &mut b, 8);
    assert!(arrivals(&b).len() >= 2);
    let pace = p.pace();
    assert_ne!(pace, 1.0);
    assert!((0.5..=4.0).contains(&pace));
    p.reset();
    assert_eq!(p.pace(), pace);
}

#[test]
fn a_new_patrol_starts_idle() {
    let b = Sim::patrol(&[FLOOR], (10.0, 100.0), &[(20.0, 100.0)]);
    assert!(b.state_ref().viz.patrol.is_none());
}

#[test]
fn sweep_mode_crosses_each_platform_end_to_end() {
    // Two anchors on the floor (one sweep) and one on MID.
    let mut b = Sim::patrol(
        &[FLOOR, MID],
        (100.0, 100.0),
        &[(30.0, 100.0), (170.0, 100.0), (80.0, 84.0)],
    );
    b.cfg.patrol_mode = picobot_core::config::PatrolMode::Sweep;
    b.cfg.sweep_reach_px = 12.0;
    let mut p = Patrol::default();
    let mut seen: Vec<(f64, f64)> = Vec::new();
    for _ in 0..30 {
        p.tick(&mut b);
        seen.push(b.pos);
    }
    let near = |x: f64, y: f64| seen.iter().any(|p| (p.0 - x).abs() <= 1.0 && p.1 == y);
    // The floor (0..200): from 8px inside one end to 12px short of the other.
    assert!(near(8.0, 100.0) || near(192.0, 100.0), "{seen:?}");
    assert!(near(188.0, 100.0) || near(12.0, 100.0), "{seen:?}");
    // MID (40..120): likewise — starting where an up flash lands, within the
    // walk tolerance (4px) of the entry.
    let within = |x: f64, y: f64| seen.iter().any(|p| (p.0 - x).abs() <= 4.0 && p.1 == y);
    assert!(within(48.0, 84.0) || within(112.0, 84.0), "{seen:?}");
    assert!(near(108.0, 84.0) || near(52.0, 84.0), "{seen:?}");
    // The second floor anchor is covered by the floor's sweep: never a target.
    assert!(p.plan.iter().all(|s| s.anchor != 1));
    assert!(arrivals(&b).iter().any(|a| a == "a0"));
    assert!(arrivals(&b).iter().any(|a| a == "a2"));
    assert!(!arrivals(&b).iter().any(|a| a == "a1"));
}

/// Odium Road to the Castle's Gate 2 (198x84 px): three tiers 13-14px
/// apart, anchors on five of its seven platforms, and the class's learned
/// reach (up flash 13px up — not tier 2 to tier 3).
fn castle_gate() -> Sim {
    let plats = [
        [40.0, 42.0, 98.0, 42.0],
        [106.0, 42.0, 134.0, 42.0],
        [138.0, 42.0, 173.0, 42.0],
        [36.0, 56.0, 69.0, 56.0],
        [75.0, 56.0, 122.0, 56.0],
        [129.0, 56.0, 164.0, 56.0],
        [30.0, 69.0, 171.0, 69.0],
    ];
    let anchors = [
        (53.0, 52.0),
        (146.0, 52.0),
        (79.0, 38.0),
        (105.0, 65.0),
        (168.0, 38.0),
    ];
    let r = |dx, rise| Reach { dx, rise };
    let mut b = Sim::patrol(&plats, (100.0, 69.0), &anchors).with_reach(reach_with(&[
        (Move::Flash, r(37.0, 4.0)),
        (Move::DoubleFlash, r(48.0, 4.0)),
        (Move::UpFlash, r(6.0, 13.0)),
        (Move::UpSideFlash, r(17.0, 12.6)),
        (Move::RopeLift, r(3.0, 90.0)),
    ]));
    b.cfg.patrol_mode = picobot_core::config::PatrolMode::Sweep;
    b.cfg.sweep_reach_px = 12.0;
    b
}

#[test]
fn castle_gate_sweeps_keep_off_the_edges_and_never_climb_to_come_down() {
    for seed in 0..20u64 {
        let mut b = castle_gate();
        b.state = picobot_core::bot::BotState::new(&b.cfg, b.state.reach.clone(), Some(seed));
        let mut p = Patrol::default();
        p.tick(&mut b);
        assert_eq!(p.plan.len(), 5, "every anchored platform is swept");
        let g = b.graph();
        for s in &p.plan {
            let (entry, exit) = s.sweep.expect("a sweep");
            let plat = g.platforms[g.locate(entry.0, entry.1).unwrap()];
            for x in [entry.0, exit.0] {
                assert!(
                    x - plat.x0 >= 8.0 - 1e-9 && plat.x1 - x >= 8.0 - 1e-9,
                    "seed {seed}: {x} within 8px of [{}, {}]",
                    plat.x0,
                    plat.x1
                );
            }
            // No rope lift up only to come back down before the sweep.
            let legs = s.legs.as_ref().unwrap();
            for (i, l) in legs.iter().enumerate() {
                if l.kind != MoveKind::RopeLift {
                    continue;
                }
                let lower_after = legs[i + 1..].iter().any(|m| m.y1 > l.y1 + 1.0);
                assert!(
                    !lower_after,
                    "seed {seed}: lifted to {} then dropped: {legs:?}",
                    l.y1
                );
            }
        }
    }
}
