//! Execute navigation-graph routes with the body's movement primitives.
//!
//! [`Navigator::step`] plans from the player's *actual* position and runs
//! one leg, so a missed landing is corrected on the next step instead of
//! blindly continuing a stale route. Every jump-type move reports its
//! takeoff and landing to the reach model — that's how reach is learned.

use std::hash::{Hash, Hasher};
use std::sync::Arc;

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};

use super::body::{Body, Dir, LegViz, Travel};
use crate::navgraph::{Leg, MoveKind, NavGraph, DRIFT_REACH};

/// How one leg ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LegStatus {
    Ok,
    Failed,
    /// Rope lift or teleport started cooling during the approach: re-route,
    /// don't count a failure.
    Cooldown,
}

/// How one navigation step ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StepStatus {
    Arrived,
    Moved,
    Failed,
    NoRoute,
    /// No player position.
    Lost,
    Cooldown,
}

pub struct Navigator {
    pub graph: Arc<NavGraph>,
    pub jitter: f64,
    pub align_px: f64,
    seed: u64,
}

/// How long a move may take to leave the ground: rope lift winds up while
/// its rope grapples (longer for higher platforms).
fn takeoff_s(kind: MoveKind) -> f64 {
    match kind {
        MoveKind::RopeLift => 1.2,
        MoveKind::Teleport => 0.6,
        MoveKind::ClimbUp => 0.8,
        _ => 0.4,
    }
}

const POLL_S: f64 = 0.05;

pub fn legs_viz(legs: &[Leg]) -> Vec<LegViz> {
    legs.iter()
        .map(|l| (l.kind.as_str().to_owned(), l.x0, l.y0, l.x1, l.y1))
        .collect()
}

impl Navigator {
    pub fn new<R: Rng + ?Sized>(graph: Arc<NavGraph>, rng: &mut R) -> Self {
        Navigator {
            graph,
            jitter: 0.15,
            align_px: 3.0,
            seed: rng.random(),
        }
    }

    /// Perform one planned leg (the patrol's fixed plan calls this).
    pub fn execute_leg<B: Body + ?Sized>(&self, body: &mut B, leg: &Leg) -> LegStatus {
        self.leg(body, leg)
    }

    /// Walk to `x` on the current platform (a planned walk leg).
    pub fn execute_walk<B: Body + ?Sized>(&self, body: &mut B, x: f64) -> bool {
        self.walk_to(body, x, false, None)
    }

    fn arrived<B: Body + ?Sized>(
        &self,
        body: &B,
        pos: (f64, f64),
        goal: (f64, f64),
        goal_plat: Option<usize>,
    ) -> bool {
        let tol = body.config().nav_threshold_px as f64;
        (pos.0 - goal.0).abs() <= tol && self.graph.locate(pos.0, pos.1) == goal_plat
    }

    /// One leg toward `goal`, planned from where the player actually is.
    pub fn step<B: Body + ?Sized>(&self, body: &mut B, goal: (f64, f64)) -> StepStatus {
        let Some(pos) = body.pos() else {
            return StepStatus::Lost;
        };
        let goal_plat = self.graph.locate(goal.0, goal.1);
        if self.arrived(body, pos, goal, goal_plat) {
            return StepStatus::Arrived;
        }
        // The same jitter draws for the whole segment: re-planning each
        // step must not flip between near-equal alternatives.
        let mut h = std::collections::hash_map::DefaultHasher::new();
        (goal.0.round() as i64, goal.1.round() as i64, self.seed).hash(&mut h);
        let mut rng = StdRng::seed_from_u64(h.finish());
        let mut exclude = Vec::new();
        if body.rope_lift_remaining() > 0.0 {
            exclude.push(MoveKind::RopeLift);
        }
        if body.teleport_remaining() > 0.0 {
            exclude.push(MoveKind::Teleport);
        }
        let Some(legs) = self
            .graph
            .route_jittered(pos, goal, self.jitter, &mut rng, &exclude)
        else {
            return StepStatus::NoRoute;
        };
        body.state().viz.route = Some(legs_viz(&legs));
        // The first leg that needs doing: a walk already within tolerance
        // would be a no-op, and the next step would re-plan it forever.
        let tol = body.config().nav_threshold_px as f64;
        let Some(leg) = legs
            .iter()
            .find(|l| l.kind != MoveKind::Walk || (pos.0 - l.x1).abs() > tol)
        else {
            return StepStatus::Moved;
        };
        match self.leg(body, leg) {
            LegStatus::Failed => return StepStatus::Failed,
            LegStatus::Cooldown => return StepStatus::Cooldown,
            LegStatus::Ok => {}
        }
        match body.pos() {
            Some(p) if self.arrived(body, p, goal, goal_plat) => StepStatus::Arrived,
            _ => StepStatus::Moved,
        }
    }

    /// Step until `goal` is reached; false on no route, repeated misses,
    /// a hazard or a stop.
    pub fn go<B: Body + ?Sized>(
        &self,
        body: &mut B,
        goal: (f64, f64),
        max_failures: u32,
        max_steps: u32,
    ) -> bool {
        let mut failures = 0;
        for _ in 0..max_steps {
            if !body.should_continue() || body.hazard().is_some() {
                return false;
            }
            match self.step(body, goal) {
                StepStatus::Arrived => return true,
                s @ (StepStatus::NoRoute | StepStatus::Lost) => {
                    let what = if s == StepStatus::NoRoute {
                        "noroute"
                    } else {
                        "lost"
                    };
                    body.log(&format!(
                        "Nav: {what} toward ({:.0}, {:.0})",
                        goal.0, goal.1
                    ));
                    return false;
                }
                StepStatus::Failed => {
                    failures += 1;
                    body.log(&format!("Nav: missed a landing — replanning ({failures})"));
                    if failures > max_failures {
                        body.log("Nav: giving up after replans");
                        return false;
                    }
                }
                StepStatus::Moved | StepStatus::Cooldown => {}
            }
        }
        false
    }

    fn leg<B: Body + ?Sized>(&self, body: &mut B, leg: &Leg) -> LegStatus {
        if !body.should_continue() || !body.focused() {
            return LegStatus::Failed;
        }
        if leg.kind == MoveKind::Walk {
            return if self.walk_to(body, leg.x1, false, None) {
                LegStatus::Ok
            } else {
                LegStatus::Failed
            };
        }
        // Rope lift fires from about wherever the character stands (mid-air
        // works too): no precise stop, which is what caused flash
        // ping-pong over the takeoff.
        let rope = leg.kind == MoveKind::RopeLift;
        let band = body.config().walk_band_px;
        if !self.walk_to(body, leg.x0, !rope, rope.then_some(band)) {
            return LegStatus::Failed;
        }
        if rope && body.rope_lift_remaining() > 0.0 {
            return LegStatus::Cooldown;
        }
        let want = self.graph.locate(leg.x1, leg.y1);
        let start = body.pos();
        let dir = Dir::toward(leg.x1 - leg.x0);
        match leg.kind {
            MoveKind::RopeLift => {
                if !body.rope_lift() {
                    return LegStatus::Failed;
                }
            }
            MoveKind::UpFlash => body.up_flash(((leg.x1 - leg.x0).abs() > 2.0).then_some(dir)),
            MoveKind::UpSideFlash => body.up_side_flash(dir),
            MoveKind::DownJump => body.down_jump(),
            MoveKind::ClimbUp => {
                // A moving grab: a hop (or a flash, from a platform end
                // further out) toward the rope with Up held.
                let gap = (leg.x1 - leg.x0).abs();
                let d = (gap > 2.0).then_some(dir);
                if !body.rope_up(leg.y1, d, gap > DRIFT_REACH) {
                    self.rope_fallback(body, leg);
                    return LegStatus::Failed;
                }
            }
            MoveKind::ClimbDown => {
                if !body.climb(false, leg.y1, Some(leg.x0)) {
                    self.rope_fallback(body, leg);
                    return LegStatus::Failed;
                }
            }
            MoveKind::Teleport => {
                if body.teleport_remaining() > 0.0 {
                    return LegStatus::Cooldown;
                }
                body.teleport(Some(dir));
            }
            MoveKind::Drop => {
                let (y0, snap) = (leg.y0, self.graph.snap_px);
                self.hold_until(body, dir, &mut |p| p.1 > y0 + snap, 1.5, None, 4.0);
            }
            MoveKind::Jump | MoveKind::Flash | MoveKind::DoubleFlash => {
                self.gap_jump(body, leg, dir)
            }
            MoveKind::Walk => unreachable!(),
        }
        let pos = self.land(body, start, takeoff_s(leg.kind), 2.5);
        let ok = pos.is_some_and(|p| self.on_platform(p, want));
        if let (Some(m), Some(s), Some(p)) = (leg.kind.reach_move(), start, pos) {
            body.state().reach.observe(
                m,
                ((leg.x1 - leg.x0).abs(), leg.y0 - leg.y1),
                ((p.0 - s.0).abs(), s.1 - p.1),
                ok,
            );
        }
        if ok {
            LegStatus::Ok
        } else {
            LegStatus::Failed
        }
    }

    /// Off a rope after a failed climb: a direction plus jump, toward a
    /// platform it can land on.
    fn rope_fallback<B: Body + ?Sized>(&self, body: &mut B, leg: &Leg) {
        let pos = body.pos().unwrap_or((leg.x1, (leg.y0 + leg.y1) / 2.0));
        let d = match self.graph.exit_direction(pos.0, pos.1) {
            crate::navgraph::Direction::Left => Dir::Left,
            crate::navgraph::Direction::Right => Dir::Right,
        };
        body.rope_exit(d);
    }

    /// Tolerant landing check: the drawn row may sit a few px off the real
    /// platform, so accept its span with row slack — a successful move must
    /// not shrink the reach model.
    pub fn on_platform(&self, pos: (f64, f64), want: Option<usize>) -> bool {
        let Some(want) = want else { return false };
        if self.graph.locate(pos.0, pos.1) == Some(want) {
            return true;
        }
        let p = self.graph.platforms[want];
        p.spans(pos.0, 2.0) && (p.y_at(pos.0) - pos.1).abs() <= self.graph.snap_px + 2.0
    }

    fn walk_to<B: Body + ?Sized>(
        &self,
        body: &mut B,
        x: f64,
        exact: bool,
        tol: Option<f64>,
    ) -> bool {
        let Some(pos) = body.pos() else { return false };
        let tol = tol.unwrap_or(if exact {
            self.align_px
        } else {
            body.config().nav_threshold_px as f64
        });
        if (pos.0 - x).abs() <= tol {
            return true;
        }
        body.move_to_point(x.round(), pos.1, Some(tol.trunc()), Travel::Mixed, true)
    }

    /// Hold `dir` until `done`. With `min_progress`, give up early when the
    /// character hasn't moved that far in 0.5s — a flash that never
    /// triggered must not walk the player off the takeoff edge.
    fn hold_until<B: Body + ?Sized>(
        &self,
        body: &mut B,
        dir: Dir,
        done: &mut dyn FnMut((f64, f64)) -> bool,
        timeout: f64,
        action: Option<&mut dyn FnMut(&mut B)>,
        min_progress: f64,
    ) {
        body.keys().key_down(dir.key());
        if let Some(a) = action {
            a(body);
        }
        let t0 = body.now();
        let mut start_x = None;
        while body.now() - t0 < timeout && body.should_continue() {
            if let Some(p) = body.pos() {
                if done(p) {
                    break;
                }
                match start_x {
                    None => start_x = Some(p.0),
                    Some(sx)
                        if min_progress > 0.0
                            && body.now() - t0 > 0.5
                            && (p.0 - sx).abs() < min_progress =>
                    {
                        break
                    }
                    _ => {}
                }
            }
            if body.sleep(POLL_S) {
                break;
            }
        }
        body.keys().key_up(dir.key());
    }

    fn gap_jump<B: Body + ?Sized>(&self, body: &mut B, leg: &Leg, dir: Dir) {
        let sign = if dir == Dir::Right { 1.0 } else { -1.0 };
        let (x1, y0, snap) = (leg.x1, leg.y0, self.graph.snap_px);
        let kind = leg.kind;
        let mut action = |b: &mut B| match kind {
            MoveKind::Jump => {
                let jk = b.config().jump_key.clone();
                b.keys().press(&jk, None);
            }
            MoveKind::Flash => b.flash_hop(),
            _ => b.double_flash(),
        };
        self.hold_until(
            body,
            dir,
            &mut |p| (p.0 - x1) * sign >= 0.0 || p.1 > y0 + snap, // or walked off the edge
            1.5,
            Some(&mut action),
            4.0,
        );
    }

    /// Where the move came down: wait until the character has left
    /// `start` (a wind-up stands still and must not read as a landing),
    /// then for two steady reads *on a drawn platform* — a pause at the top
    /// of a jump is steady too, but in the air. Polls are counted rather
    /// than timed, so a slow capture can't shorten the wait.
    pub fn land<B: Body + ?Sized>(
        &self,
        body: &mut B,
        start: Option<(f64, f64)>,
        takeoff: f64,
        timeout: f64,
    ) -> Option<(f64, f64)> {
        let polls = (timeout / POLL_S) as usize;
        let wait_takeoff = (takeoff / POLL_S) as usize;
        let away =
            |p: (f64, f64), s: (f64, f64)| (p.0 - s.0).abs() > 2.0 || (p.1 - s.1).abs() > 2.0;
        let mut last = body.pos();
        let mut moved = match (start, last) {
            (Some(s), Some(l)) => away(l, s),
            _ => true,
        };
        let mut stable = 0;
        for i in 0..polls {
            if body.sleep(POLL_S) {
                return last;
            }
            let Some(p) = body.pos() else { continue };
            if !moved {
                moved = start.is_some_and(|s| away(p, s)) || i >= wait_takeoff;
                last = Some(p);
                continue;
            }
            let steady = last.is_some_and(|l| (p.0 - l.0).abs() <= 1.0 && (p.1 - l.1).abs() <= 1.0);
            if steady && self.graph.locate(p.0, p.1).is_some() {
                stable += 1;
                if stable >= 2 {
                    return Some(p);
                }
            } else {
                stable = 0;
            }
            last = Some(p);
        }
        last
    }
}
