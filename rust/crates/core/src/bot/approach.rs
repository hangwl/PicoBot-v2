//! The detour to a rune: route to a spot beside it and stand there
//! touching it (`rune::TARGET_GAP`; on it counts too), facing it.
//!
//! Routing is the navigator's, one step per tick from the player's actual
//! position; on the rune's platform the bot places itself precisely (tap
//! nudges when a tap table is measured) and turns toward the rune. The
//! state machine pauses on arrival or failure.

use std::sync::Arc;

use serde_json::json;

use super::body::{Body, Dir, Travel};
use super::grind::blind_wait;
use super::navigator::{Navigator, StepStatus};
use crate::rune::{at_rune, gap, platform_under, slot_x, Rune, TARGET_GAP};
use crate::timing::human_between;

/// The whole detour may take this long (s).
const TIMEOUT_S: f64 = 60.0;
/// Missed landings on the way before giving up.
const MAX_MISSES: u32 = 3;
/// Placement attempts on the rune's platform before giving up.
const MAX_PLACES: u32 = 4;

#[derive(Debug, Clone, PartialEq)]
pub enum Approach {
    Moving,
    Arrived,
    Failed(String),
}

#[derive(Default)]
pub struct RuneApproach {
    nav: Option<Navigator>,
    started: Option<f64>,
    misses: u32,
    places: u32,
}

impl RuneApproach {
    pub fn reset(&mut self) {
        *self = RuneApproach::default();
    }

    /// One step toward `rune`.
    pub fn tick<B: Body + ?Sized>(&mut self, body: &mut B, rune: Rune) -> Approach {
        let now = body.now();
        if now - *self.started.get_or_insert(now) > TIMEOUT_S {
            return Approach::Failed("timed out on the way".into());
        }
        let Some(graph) = body.graph() else {
            return Approach::Failed("no platforms drawn".into());
        };
        let Some(plat) = platform_under(&graph, rune.bbox) else {
            return Approach::Failed("it isn't over a drawn platform".into());
        };
        let p = graph.platforms[plat];
        let slots: Vec<f64> = [true, false]
            .into_iter()
            .map(|left| slot_x(rune.bbox, left, TARGET_GAP) as f64)
            .filter(|x| p.spans(*x, 0.0))
            .collect();
        if slots.is_empty() {
            return Approach::Failed("no room beside it".into());
        }
        let Some(pos) = body.pos() else {
            blind_wait(body);
            return Approach::Moving;
        };
        let Some(here) = graph.locate(pos.0, pos.1) else {
            blind_wait(body); // mid-move
            return Approach::Moving;
        };
        if self
            .nav
            .as_ref()
            .is_none_or(|n| !Arc::ptr_eq(&n.graph, &graph))
        {
            self.nav = Some(Navigator::new(graph.clone(), &mut body.state().rng));
        }
        if here == plat {
            let x = nearest(&slots, pos.0);
            return self.place(body, rune, x, p.y_at(x));
        }
        let goal = slots
            .iter()
            .map(|&x| ((x, p.y_at(x)), graph.route_cost(pos, (x, p.y_at(x)), &[])))
            .filter(|(_, c)| c.is_finite())
            .min_by(|a, b| a.1.total_cmp(&b.1))
            .map(|(g, _)| g);
        let Some(goal) = goal else {
            return Approach::Failed("no route to it".into());
        };
        let nav = self.nav.as_ref().expect("navigator set above");
        match nav.step(body, goal) {
            StepStatus::NoRoute => Approach::Failed("no route to it".into()),
            StepStatus::Failed => {
                self.misses += 1;
                if self.misses >= MAX_MISSES {
                    Approach::Failed("missed landings on the way".into())
                } else {
                    Approach::Moving
                }
            }
            _ => Approach::Moving,
        }
    }

    /// On the rune's platform: walk to `x`, turn toward the rune, and check
    /// the glyphs stand side by side.
    fn place<B: Body + ?Sized>(&mut self, body: &mut B, rune: Rune, x: f64, y: f64) -> Approach {
        self.places += 1;
        if self.places > MAX_PLACES {
            return Approach::Failed("couldn't stand beside it".into());
        }
        let Some(pos) = body.pos() else {
            return Approach::Moving;
        };
        if !at_rune(pos.0 as i32, rune.bbox) {
            let near = body.config().nav_threshold_px as f64;
            if (pos.0 - x).abs() > 2.0 * body.config().walk_band_px {
                // Far along the platform: hop most of the way, as anywhere.
                body.move_to_point(x, y, Some(near), Travel::Mixed, true);
            }
            let precise = body.state_ref().reach.tap_table().is_some();
            if precise {
                body.move_to_point(x, y, Some(6.0), Travel::Walk, true);
                body.nudge_to(x, 0.5);
            } else {
                body.move_to_point(x, y, Some(1.0), Travel::Walk, true);
            }
        }
        // A short tap toward the rune turns the character to face it.
        let toward = Dir::toward(rune.center().0 - x);
        let hold = human_between(0.05, 0.04, 0.07, 0.2);
        body.tap(toward, hold);
        body.sleep(human_between(0.15, 0.1, 0.25, 0.3));
        match body.pos() {
            Some(p) if at_rune(p.0 as i32, rune.bbox) => {
                let g = gap(p.0 as i32, rune.bbox);
                body.rune_event(
                    "arrived",
                    json!({ "rune": rune.bbox, "player": [p.0, p.1], "gap": g, "facing": toward.key() }),
                );
                Approach::Arrived
            }
            _ => Approach::Moving,
        }
    }
}

fn nearest(xs: &[f64], x: f64) -> f64 {
    *xs.iter()
        .min_by(|a, b| (*a - x).abs().total_cmp(&(*b - x).abs()))
        .expect("at least one slot")
}
