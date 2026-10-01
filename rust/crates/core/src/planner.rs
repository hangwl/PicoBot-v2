//! Patrol loop planning: order a map's anchors into one loop of legs.
//!
//! Every reachable anchor is visited once per loop; the policy only picks
//! the order. `Weighted` is a roulette with P(next) ∝ 1/cost^temp, so far
//! anchors still get drawn; `Greedy` takes the cheapest next leg (costs
//! jittered ±20% for variety). Anchors with no route are returned as
//! skipped, with the reason, so the caller can ban them for a while.

use std::collections::HashSet;

use rand::Rng;

use crate::config::PatrolPolicy;
use crate::navgraph::{Leg, MoveKind, NavGraph};

/// One leg of the loop: to `anchor`, along planned `legs` — or, when
/// `legs` is None, the hand-recorded leg from anchor `from`.
#[derive(Debug, Clone, PartialEq)]
pub struct PlanSegment {
    pub anchor: usize,
    pub legs: Option<Vec<Leg>>,
    pub from: Option<usize>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkipReason {
    /// The anchor isn't on any drawn platform.
    OffPlatform,
    /// No route from the loop's previous anchor.
    NoRoute,
}

impl SkipReason {
    pub fn as_str(self) -> &'static str {
        match self {
            SkipReason::OffPlatform => "not on a drawn platform — re-place it",
            SkipReason::NoRoute => "no route from here",
        }
    }
}

pub struct LoopRequest<'a> {
    pub graph: &'a NavGraph,
    /// Anchor positions (minimap px), in anchor order.
    pub anchors: &'a [(f64, f64)],
    /// Anchors currently banned (not planned).
    pub banned: &'a HashSet<usize>,
    /// Arrival tolerance (px): an anchor this close on the same platform
    /// is already reached.
    pub tol: f64,
    /// Whether a hand-recorded leg exists from one anchor to another.
    pub recorded: &'a dyn Fn(usize, usize) -> bool,
    pub policy: PatrolPolicy,
    pub temp: f64,
    /// Moves with a cooldown, as of the loop's start.
    pub cooldowns: &'a [Cooldown],
    /// Real seconds per second of route cost.
    pub pace: f64,
}

/// A move with a cooldown.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Cooldown {
    pub kind: MoveKind,
    /// Seconds until it's usable (infinite: never, e.g. no key bound).
    pub ready_in: f64,
    /// Seconds from one use to the next.
    pub every: f64,
}

/// When each cooled move is usable along a planned loop, on a clock that
/// runs `pace` real seconds per second of route cost.
#[derive(Debug, Clone)]
struct Clock {
    t: f64,
    pace: f64,
    /// (kind, usable at, every).
    ready: Vec<(MoveKind, f64, f64)>,
}

impl Clock {
    fn new(cds: &[Cooldown], pace: f64) -> Self {
        Clock {
            t: 0.0,
            pace: pace.max(0.1),
            ready: cds.iter().map(|c| (c.kind, c.ready_in, c.every)).collect(),
        }
    }

    fn never(&self) -> Vec<MoveKind> {
        self.ready
            .iter()
            .filter(|r| r.1.is_infinite())
            .map(|r| r.0)
            .collect()
    }

    /// Advance over `legs`; the first move they'd fire while still cooling.
    fn run(&mut self, legs: &[Leg]) -> Option<MoveKind> {
        let mut early = None;
        for l in legs {
            if let Some(r) = self.ready.iter_mut().find(|r| r.0 == l.kind) {
                if r.1 > self.t + 1e-9 && early.is_none() {
                    early = Some(l.kind);
                }
                if r.1.is_finite() {
                    r.1 = self.t + r.2;
                }
            }
            self.t += l.cost * self.pace;
        }
        early
    }

    fn idle(&mut self, cost: f64) {
        self.t += cost * self.pace;
    }

    /// The cheapest route that only fires moves once they're ready; failing
    /// that, the cheapest at all (the run re-routes if one is still
    /// cooling) — a cooldown never makes an anchor unreachable.
    fn route(&self, g: &NavGraph, from: (f64, f64), to: (f64, f64)) -> Option<Vec<Leg>> {
        let never = self.never();
        let mut exclude = never.clone();
        while let Some(legs) = g.route(from, to, &exclude) {
            match self.clone().run(&legs) {
                None => return Some(legs),
                Some(k) => exclude.push(k),
            }
        }
        g.route(from, to, &never)
    }
}

/// The cooldowns after running `legs` at `pace` (for planning past them).
pub fn after_legs(cds: &[Cooldown], legs: &[Leg], pace: f64) -> Vec<Cooldown> {
    let mut c = Clock::new(cds, pace);
    c.run(legs);
    c.ready
        .iter()
        .map(|&(kind, at, every)| Cooldown {
            kind,
            ready_in: (at - c.t).max(0.0),
            every,
        })
        .collect()
}

/// Plan one loop from `cur` (whose anchor is `cur_i`, if at one).
pub fn plan_loop<R: Rng + ?Sized>(
    req: &LoopRequest,
    mut cur: (f64, f64),
    mut cur_i: Option<usize>,
    rng: &mut R,
) -> (Vec<PlanSegment>, Vec<(usize, SkipReason)>) {
    let g = req.graph;
    let here = g.locate(cur.0, cur.1);
    let mut remaining: Vec<usize> = (0..req.anchors.len())
        .filter(|i| !req.banned.contains(i))
        .filter(|&i| {
            let (ax, ay) = req.anchors[i];
            !((ax - cur.0).abs() <= req.tol && g.locate(ax, ay) == here)
        })
        .collect();
    let mut clock = Clock::new(req.cooldowns, req.pace);
    let (mut segments, mut skipped) = (Vec::new(), Vec::new());
    while !remaining.is_empty() {
        let mut routes: Vec<(usize, Vec<Leg>)> = Vec::new();
        for &i in &remaining {
            match clock.route(g, cur, req.anchors[i]) {
                Some(legs) => routes.push((i, legs)),
                None => {
                    let (ax, ay) = req.anchors[i];
                    let why = if g.locate(ax, ay).is_none() {
                        SkipReason::OffPlatform
                    } else {
                        SkipReason::NoRoute
                    };
                    skipped.push((i, why));
                }
            }
        }
        // Unreachable anchors can't be picked by either policy: skip them
        // up front so the loop always makes progress.
        remaining.retain(|i| routes.iter().any(|r| r.0 == *i));
        if routes.is_empty() {
            break;
        }
        let costs: Vec<(usize, f64)> = routes
            .iter()
            .map(|(i, legs)| (*i, legs.iter().map(|l| l.cost).sum()))
            .collect();
        let next = pick_next(&costs, req.policy, req.temp, rng);
        let (route, cost) = routes
            .into_iter()
            .zip(&costs)
            .find(|((i, _), _)| *i == next)
            .map(|((_, legs), (_, c))| (legs, *c))
            .expect("picked from the candidates");
        let legs = match cur_i {
            Some(from) if (req.recorded)(from, next) => {
                clock.idle(cost);
                None
            }
            _ => {
                clock.run(&route);
                Some(route)
            }
        };
        let from = if legs.is_none() { cur_i } else { None };
        segments.push(PlanSegment {
            anchor: next,
            legs,
            from,
        });
        remaining.retain(|&r| r != next);
        cur = req.anchors[next];
        cur_i = Some(next);
    }
    (segments, skipped)
}

/// The loop-order policy over `(anchor, route cost)` candidates.
pub fn pick_next<R: Rng + ?Sized>(
    costs: &[(usize, f64)],
    policy: PatrolPolicy,
    temp: f64,
    rng: &mut R,
) -> usize {
    match policy {
        PatrolPolicy::Greedy => {
            let mut best = costs[0].0;
            let mut best_cost = f64::INFINITY;
            for &(i, c) in costs {
                let jittered = c * (1.0 + rng.random_range(-0.2..=0.2));
                if jittered < best_cost {
                    best = i;
                    best_cost = jittered;
                }
            }
            best
        }
        PatrolPolicy::Weighted => roulette(costs, temp, rng.random::<f64>()),
    }
}

/// Weighted pick with the draw `r` in [0, 1): P ∝ 1/cost^temp.
pub fn roulette(costs: &[(usize, f64)], temp: f64, r: f64) -> usize {
    let temp = temp.max(0.05);
    let weights: Vec<f64> = costs
        .iter()
        .map(|(_, c)| 1.0 / c.max(0.5).powf(temp))
        .collect();
    let target = r * weights.iter().sum::<f64>();
    let mut acc = 0.0;
    for (w, (i, _)) in weights.iter().zip(costs) {
        acc += w;
        if target <= acc {
            return *i;
        }
    }
    costs[0].0
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::BotConfig;
    use crate::navgraph::GraphOptions;
    use crate::reach::{base_reach, ReachModel};
    use rand::rngs::StdRng;
    use rand::SeedableRng;

    #[test]
    fn roulette_gives_far_anchors_a_real_draw() {
        let costs = [(0, 1.0), (1, 10.0)];
        assert_eq!(roulette(&costs, 1.0, 0.95), 1);
        assert_eq!(roulette(&costs, 1.0, 0.1), 0);
    }

    #[test]
    fn greedy_picks_the_cheapest() {
        let mut rng = StdRng::seed_from_u64(0);
        assert_eq!(
            pick_next(&[(0, 1.0), (1, 10.0)], PatrolPolicy::Greedy, 1.0, &mut rng),
            0
        );
    }

    fn graph(segs: &[[f64; 4]]) -> NavGraph {
        NavGraph::new(
            segs,
            &[],
            &ReachModel::new(base_reach(&BotConfig::default()), None),
            GraphOptions::default(),
        )
    }

    #[test]
    fn plans_every_reachable_anchor_once_and_skips_the_rest() {
        // A platform 100px up: beyond rope lift's 90px grab range.
        let g = graph(&[[0.0, 100.0, 200.0, 100.0], [60.0, 0.0, 100.0, 0.0]]);
        let anchors = [(20.0, 100.0), (180.0, 100.0), (80.0, 0.0), (80.0, 60.0)];
        let banned = HashSet::new();
        let req = LoopRequest {
            graph: &g,
            anchors: &anchors,
            banned: &banned,
            tol: 5.0,
            recorded: &|_, _| false,
            policy: PatrolPolicy::Greedy,
            temp: 1.0,
            cooldowns: &[],
            pace: 1.0,
        };
        let (plan, skipped) = plan_loop(&req, (100.0, 100.0), None, &mut StdRng::seed_from_u64(1));
        let mut visited: Vec<usize> = plan.iter().map(|s| s.anchor).collect();
        visited.sort();
        assert_eq!(visited, [0, 1]);
        assert!(skipped.contains(&(2, SkipReason::NoRoute)));
        assert!(skipped.contains(&(3, SkipReason::OffPlatform)));
    }

    #[test]
    fn recorded_legs_are_used_between_anchors_and_bans_are_respected() {
        let g = graph(&[[0.0, 100.0, 200.0, 100.0]]);
        let anchors = [(20.0, 100.0), (100.0, 100.0), (180.0, 100.0)];
        let banned = HashSet::from([2]);
        let req = LoopRequest {
            graph: &g,
            anchors: &anchors,
            banned: &banned,
            tol: 5.0,
            recorded: &|a, b| (a, b) == (0, 1),
            policy: PatrolPolicy::Greedy,
            temp: 1.0,
            cooldowns: &[],
            pace: 1.0,
        };
        let (plan, _) = plan_loop(&req, (20.0, 100.0), Some(0), &mut StdRng::seed_from_u64(2));
        assert_eq!(
            plan,
            [PlanSegment {
                anchor: 1,
                legs: None,
                from: Some(0)
            }]
        );
    }

    /// Two ledges 15px up, too far apart to cross: each is a rise from the
    /// floor, by rope lift (cheapest) or up flash.
    fn two_rises(cooldowns: &[Cooldown]) -> (Vec<PlanSegment>, Vec<(usize, SkipReason)>) {
        let g = graph(&[
            [0.0, 100.0, 200.0, 100.0],
            [20.0, 85.0, 60.0, 85.0],
            [140.0, 85.0, 180.0, 85.0],
        ]);
        let anchors = [(40.0, 85.0), (160.0, 85.0)];
        let banned = HashSet::new();
        let req = LoopRequest {
            graph: &g,
            anchors: &anchors,
            banned: &banned,
            tol: 5.0,
            recorded: &|_, _| false,
            policy: PatrolPolicy::Greedy,
            temp: 1.0,
            cooldowns,
            pace: 1.0,
        };
        plan_loop(&req, (100.0, 100.0), None, &mut StdRng::seed_from_u64(3))
    }

    fn count(plan: &[PlanSegment], kind: MoveKind) -> usize {
        plan.iter()
            .flat_map(|s| s.legs.iter().flatten())
            .filter(|l| l.kind == kind)
            .count()
    }

    fn rope(ready_in: f64, every: f64) -> [Cooldown; 1] {
        [Cooldown {
            kind: MoveKind::RopeLift,
            ready_in,
            every,
        }]
    }

    #[test]
    fn a_cooling_move_is_planned_only_once_it_is_ready_again() {
        let (plan, _) = two_rises(&[]);
        assert_eq!(count(&plan, MoveKind::RopeLift), 2); // unconstrained
        let (plan, _) = two_rises(&rope(0.0, 1000.0));
        assert_eq!(plan.len(), 2);
        assert_eq!(count(&plan, MoveKind::RopeLift), 1);
        assert_eq!(count(&plan, MoveKind::UpFlash), 1);
        let (plan, _) = two_rises(&rope(f64::INFINITY, 3.0)); // unbound
        assert_eq!(plan.len(), 2);
        assert_eq!(count(&plan, MoveKind::RopeLift), 0);
    }

    #[test]
    fn a_cooldown_never_makes_an_anchor_unreachable() {
        // 60px up: only a rope lift gets there.
        let g = graph(&[[0.0, 100.0, 200.0, 100.0], [80.0, 40.0, 120.0, 40.0]]);
        let anchors = [(100.0, 40.0)];
        let banned = HashSet::new();
        let plan = |cds: &[Cooldown]| {
            let req = LoopRequest {
                graph: &g,
                anchors: &anchors,
                banned: &banned,
                tol: 5.0,
                recorded: &|_, _| false,
                policy: PatrolPolicy::Greedy,
                temp: 1.0,
                cooldowns: cds,
                pace: 1.0,
            };
            plan_loop(&req, (20.0, 100.0), None, &mut StdRng::seed_from_u64(4))
        };
        let (segs, skipped) = plan(&rope(1000.0, 3.0));
        assert_eq!(count(&segs, MoveKind::RopeLift), 1);
        assert!(skipped.is_empty());
        let (segs, skipped) = plan(&rope(f64::INFINITY, 3.0));
        assert!(segs.is_empty());
        assert_eq!(skipped, [(0, SkipReason::NoRoute)]);
    }

    #[test]
    fn cooldowns_run_down_over_the_legs_ahead() {
        let leg = |kind, cost| Leg {
            kind,
            x0: 0.0,
            y0: 0.0,
            x1: 0.0,
            y1: 0.0,
            cost,
        };
        let legs = [leg(MoveKind::RopeLift, 0.5), leg(MoveKind::Walk, 2.0)];
        let after = after_legs(&rope(0.0, 3.0), &legs, 1.0);
        assert!((after[0].ready_in - 0.5).abs() < 1e-9);
        let after = after_legs(&rope(0.0, 3.0), &legs, 2.0); // a slower pace
        assert_eq!(after[0].ready_in, 0.0);
        let after = after_legs(&rope(f64::INFINITY, 3.0), &legs, 1.0);
        assert!(after[0].ready_in.is_infinite());
    }
}
