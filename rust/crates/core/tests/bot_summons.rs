//! Arrival summons: cast only while standing, but a character still
//! settling after a landing gets a moment instead of a skipped visit.

mod sim;

use picobot_core::bot::grind::cast_at_anchor;
use picobot_core::bot::Body;
use picobot_core::skills::{Skill, SkillBook, SkillKind};
use sim::*;

fn summoner(at: (f64, f64)) -> Sim {
    let mut b = Sim::patrol(&[FLOOR], at, &[(30.0, 100.0), (150.0, 100.0)]);
    b.state.skills = SkillBook::new(vec![Skill {
        kind: SkillKind::Summon,
        ..Skill::new("orb", "s")
    }]);
    b
}

fn arrive(b: &mut Sim) {
    let rot = b.rotation();
    cast_at_anchor(b, &rot, 0);
}

fn cast(b: &Sim) -> bool {
    b.presses.iter().any(|k| k == "s")
}

#[test]
fn a_standing_character_summons_at_once() {
    let mut b = summoner((30.0, 100.0));
    arrive(&mut b);
    assert!(cast(&b));
    assert!(b.slept < 0.3);
}

#[test]
fn a_sliding_character_is_given_a_moment_to_settle() {
    let mut b = summoner((30.0, 100.0));
    b.script = [
        (30.0, 100.0),
        (38.0, 100.0),
        (46.0, 100.0),
        (52.0, 100.0),
        (52.0, 100.0),
    ]
    .into();
    arrive(&mut b);
    assert!(cast(&b));
    assert!(b.slept > 0.1);
}

#[test]
fn a_character_that_never_settles_skips_the_visit_and_keeps_the_charge() {
    let mut b = summoner((30.0, 100.0));
    b.script = (0..80)
        .map(|i| (30.0 + 5.0 * f64::from(i), 100.0))
        .collect();
    arrive(&mut b);
    assert!(!cast(&b));
    assert!(b.log_has("still moving"));
    assert!(b.slept < 1.6, "bounded wait, got {}", b.slept);
    // Nothing was placed, so the next visit may try again.
    let now = b.now();
    assert!(b.state.summons.anchor_free("a0", now));
}

#[test]
fn feet_a_little_under_a_high_line_still_count_as_standing() {
    let mut b = summoner((30.0, 104.0));
    arrive(&mut b);
    assert!(cast(&b));
}

#[test]
fn standing_well_off_every_line_is_reported_not_cast() {
    let mut b = summoner((30.0, 112.0));
    arrive(&mut b);
    assert!(!cast(&b));
    assert!(b.log_has("not on a drawn platform"));
}
