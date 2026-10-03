//! The GRIND / TRAVEL / PAUSE state machine on the simulator.

mod sim;

use picobot_core::bot::{Machine, State};
use picobot_core::skills::{Skill, SkillBook, SkillKind};
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
fn a_heartbeat_reports_the_session_and_can_be_turned_off() {
    let mut b = Sim::new(&[FLOOR], (30.0, 100.0));
    b.stop_at = 2000.0;
    Machine::default().run(&mut b);
    assert!(b.log_has("Heartbeat:") && b.log_has("visits"));

    let mut b = Sim::new(&[FLOOR], (30.0, 100.0));
    b.cfg.heartbeat_minutes = 0.0;
    b.stop_at = 2000.0;
    Machine::default().run(&mut b);
    assert!(!b.log_has("Heartbeat:"));
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

#[test]
fn indices_of_removed_anchors_are_dropped_not_followed() {
    let mut b = anchored();
    b.state.arrive_pending = vec![5];
    let mut m = Machine::default();
    m.grind_tick(&mut b);
    b.state.travel_target = Some(9);
    assert!(m.switch(&mut b));
    assert_eq!(m.state, State::Travel);
    assert!(b.log_has("TRAVEL: →"));
    b.state.travel_target = Some(9);
    assert!(!picobot_core::bot::grind::run_travel(&mut b));
    assert!(b.state.travel_target.is_none());
}

#[test]
fn an_interrupted_travel_keeps_its_target_unbanned() {
    let mut b = anchored();
    b.state.travel_target = Some(1);
    b.hazard = Some("rune".into());
    assert!(!picobot_core::bot::grind::run_travel(&mut b));
    assert_eq!(b.state.travel_target, Some(1));
    assert!(b.state.bans.is_empty());
}

#[test]
fn a_map_change_forgets_the_old_maps_bans_and_origin() {
    let mut b = anchored();
    b.state.bans.insert(1, 9e9);
    b.state.arrive_pending = vec![1];
    b.state.roam_origin = Some((5.0, 5.0));
    b.state.map_version = 7;
    assert!(picobot_core::bot::grind::sync_map(&mut b));
    assert!(b.state.bans.is_empty());
    assert!(b.state.arrive_pending.is_empty());
    assert!(b.state.roam_origin.is_none());
}

#[test]
fn buffs_go_out_first_at_checkpoints_standing() {
    let mut b = anchored();
    b.state.skills = SkillBook::new(vec![
        Skill {
            kind: SkillKind::Buff,
            cooldown: 1000.0,
            ..Skill::new("haste", "h")
        },
        Skill {
            kind: SkillKind::Summon,
            cooldown: 1.0,
            duration: 60.0,
            ..Skill::new("orb", "o")
        },
    ]);
    let mut m = Machine::default();
    for _ in 0..8 {
        m.execute(&mut b);
    }
    let at = |l: &str| b.logs.iter().position(|x| x == l);
    let checkpoint = b
        .logs
        .iter()
        .position(|l| l.starts_with("Checkpoint:"))
        .expect("a checkpoint");
    let haste = at("Skill: haste").expect("the buff was cast");
    assert!(checkpoint < haste, "cast before any checkpoint");
    if let Some(orb) = at("Skill: orb") {
        assert!(haste < orb, "the buff goes before the summon");
    }
    let casts = b.logs.iter().filter(|l| *l == "Skill: haste").count();
    assert_eq!(casts, 1, "cast once per cooldown");
}
