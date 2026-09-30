//! The attack scheduler: windows, stance, weights, odds and the rate target.

mod sim;

use picobot_core::bot::{Body, Window};
use picobot_core::skills::{Skill, SkillBook, Stance};
use sim::*;

fn sim_with(skills: Vec<Skill>) -> Sim {
    let mut b = Sim::new(&[FLOOR], (30.0, 100.0));
    b.state.skills = SkillBook::new(skills);
    b
}

fn skill(name: &str, stance: Stance, weight: f64) -> Skill {
    Skill {
        stance,
        weight,
        ..Skill::new(name, name)
    }
}

fn casts(b: &Sim, key: &str) -> usize {
    b.presses.iter().filter(|k| *k == key).count()
}

#[test]
fn the_defaults_attack_after_every_move() {
    let mut b = sim_with(vec![skill("a", Stance::Any, 1.0)]);
    for _ in 0..20 {
        b.after_flash(0.3);
    }
    assert!(casts(&b, "a") >= 20);
}

#[test]
fn a_move_can_carry_no_attack() {
    let mut b = sim_with(vec![skill("a", Stance::Any, 1.0)]);
    b.cfg.move_attack_chance = 0.0;
    for _ in 0..50 {
        b.after_flash(0.3);
    }
    assert_eq!(casts(&b, "a"), 0);
    b.cfg.move_attack_chance = 0.5;
    for _ in 0..200 {
        b.after_flash(0.3);
    }
    let n = casts(&b, "a");
    assert!((60..240).contains(&n), "{n}");
}

#[test]
fn stance_keeps_skills_to_their_window() {
    let mut b = sim_with(vec![skill("g", Stance::Ground, 1.0)]);
    for _ in 0..30 {
        b.after_flash(0.3);
    }
    assert_eq!(casts(&b, "g"), 0, "a ground skill isn't cast in the air");

    let mut b = sim_with(vec![skill("s", Stance::Air, 1.0)]);
    b.cfg.air_attacks = false; // the tail is a ground window
    for _ in 0..30 {
        b.after_flash(0.3);
    }
    assert_eq!(casts(&b, "s"), 0, "an air skill isn't cast on the ground");
    b.cfg.air_attacks = true;
    b.after_flash(0.3);
    assert!(casts(&b, "s") >= 1);
}

#[test]
fn a_ground_skill_is_cast_on_landings_even_with_no_ground_odds() {
    let mut b = sim_with(vec![skill("g", Stance::Ground, 1.0)]);
    assert_eq!(b.cfg.ground_attack_chance, 0.0);
    let fired = (0..200).filter(|_| b.ground_window() > 0).count();
    assert!((150..190).contains(&fired), "{fired}"); // ~85% of landings
}

#[test]
fn an_any_skill_is_not_cast_on_landings_by_default() {
    let mut b = sim_with(vec![skill("a", Stance::Any, 1.0)]);
    for _ in 0..100 {
        b.ground_window();
    }
    assert_eq!(casts(&b, "a"), 0);
}

#[test]
fn weight_sets_the_odds_and_zero_excludes() {
    let mut b = sim_with(vec![
        skill("a", Stance::Any, 9.0),
        skill("b", Stance::Any, 1.0),
        skill("z", Stance::Any, 0.0),
    ]);
    let mut count = std::collections::HashMap::new();
    for _ in 0..1000 {
        let s = b.pick_attack(Window::Air).unwrap();
        *count.entry(s.name).or_insert(0) += 1;
    }
    assert!((820..960).contains(&count["a"]), "{count:?}");
    assert!(!count.contains_key("z"));
}

#[test]
fn skills_on_cooldown_are_preferred_over_spam() {
    let mut b = sim_with(vec![
        skill("spam", Stance::Any, 1.0),
        Skill {
            cooldown: 30.0,
            ..skill("burst", Stance::Any, 1.0)
        },
    ]);
    for _ in 0..20 {
        assert_eq!(b.pick_attack(Window::Air).unwrap().name, "burst");
    }
}

#[test]
fn the_rate_target_pushes_the_odds_both_ways() {
    let mut b = sim_with(vec![skill("a", Stance::Any, 1.0)]);
    assert_eq!(b.window_odds(0.3), 0.3); // no target: untouched
    b.cfg.target_attacks_per_min = 60.0;
    assert_eq!(b.window_odds(0.0), 1.0); // nothing cast yet: a full deficit
    b.state.attack_log.extend(std::iter::repeat_n(0.0, 30));
    assert!((b.window_odds(0.0) - 0.5).abs() < 1e-9); // half the target
    b.state.attack_log.extend(std::iter::repeat_n(0.0, 90));
    assert_eq!(b.window_odds(1.0), 0.0); // twice the target: hold back
}

#[test]
fn casts_are_counted_and_age_out() {
    let mut b = sim_with(vec![skill("a", Stance::Any, 1.0)]);
    b.after_flash(0.3);
    assert!(b.attack_rate() >= 1.0);
    b.sleep(120.0);
    assert_eq!(b.attack_rate(), 0.0);
}

fn with_effect(skill: &str, dx: f64) -> Sim {
    let mut b = sim_with(vec![
        skill_named(skill, Stance::Any),
        skill_named("plain", Stance::Any),
    ]);
    b.state.reach.set_profile(
        "skill_effects",
        vec![serde_json::json!({"skill": skill, "n": 3, "dx": dx, "hang": 0.4, "rise": 0.0})],
    );
    b
}

fn skill_named(name: &str, stance: Stance) -> Skill {
    skill(name, stance, 1.0)
}

#[test]
fn a_skill_that_would_overshoot_the_landing_is_held_back_in_the_air() {
    let mut b = with_effect("pull", -12.0);
    let names = |b: &mut Sim| -> std::collections::HashSet<String> {
        (0..200)
            .filter_map(|_| b.pick_attack(Window::Air))
            .map(|s| s.name)
            .collect()
    };
    assert!(names(&mut b).contains("pull")); // no slack set: unconstrained
    b.state.air_slack = Some((5.0, 5.0));
    let n = names(&mut b);
    assert!(!n.contains("pull") && n.contains("plain"), "{n:?}");
    b.state.air_slack = Some((15.0, 5.0));
    assert!(names(&mut b).contains("pull")); // room to be pulled back
}

#[test]
fn slack_only_limits_the_air_window() {
    let mut b = with_effect("pull", -12.0);
    b.state.air_slack = Some((0.0, 0.0));
    let n: std::collections::HashSet<String> = (0..200)
        .filter_map(|_| b.pick_attack(Window::Ground))
        .map(|s| s.name)
        .collect();
    assert!(n.contains("pull"));
}

fn with_ground_effect(dx: f64) -> Sim {
    // A sim with a drawn map, so the bot can look up the platform room.
    let mut b = Sim::patrol(&[FLOOR], (100.0, 100.0), &[]);
    b.state.skills = SkillBook::new(vec![
        skill_named("rush", Stance::Any),
        skill_named("plain", Stance::Any),
    ]);
    b.state.reach.set_profile(
        "skill_effects",
        vec![
            serde_json::json!({"skill": "rush", "where": "ground", "n": 3, "dx": dx, "hang": 0.0}),
        ],
    );
    b
}

fn ground_names(b: &mut Sim) -> std::collections::HashSet<String> {
    (0..200)
        .filter_map(|_| b.pick_attack(Window::Ground))
        .map(|s| s.name)
        .collect()
}

#[test]
fn a_ground_skill_that_would_cross_the_platform_end_is_skipped_on_landings() {
    let mut b = with_ground_effect(30.0);
    b.pos = (100.0, 100.0);
    assert!(ground_names(&mut b).contains("rush")); // plenty of room
    b.pos = (190.0, 100.0);
    let n = ground_names(&mut b);
    assert!(!n.contains("rush") && n.contains("plain"), "{n:?}");
}

#[test]
fn ground_effects_do_not_limit_the_air_window_and_air_ones_not_the_ground() {
    let mut b = with_ground_effect(30.0);
    b.pos = (190.0, 100.0);
    b.state.air_slack = Some((0.0, 0.0));
    let air: std::collections::HashSet<String> = (0..200)
        .filter_map(|_| b.pick_attack(Window::Air))
        .map(|s| s.name)
        .collect();
    assert!(air.contains("rush"));
}

#[test]
fn a_leg_landing_slack_also_limits_ground_skills() {
    let mut b = with_ground_effect(30.0);
    b.pos = (100.0, 100.0);
    b.state.air_slack = Some((5.0, 5.0)); // a tight landing on this move
    let n = ground_names(&mut b);
    assert!(!n.contains("rush"), "{n:?}");
}
