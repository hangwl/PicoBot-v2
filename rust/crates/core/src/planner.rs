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
use crate::navgraph::{Leg, NavGraph};

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
    let (mut segments, mut skipped) = (Vec::new(), Vec::new());
    while !remaining.is_empty() {
        let costs: Vec<(usize, f64)> = remaining
            .iter()
            .map(|&i| (i, g.route_cost(cur, req.anchors[i], &[])))
            .collect();
        // Unreachable anchors can't be picked by either policy: skip them
        // up front so the loop always makes progress.
        for &(i, c) in &costs {
            if c.is_infinite() {
                let (ax, ay) = req.anchors[i];
                let why = if g.locate(ax, ay).is_none() {
                    SkipReason::OffPlatform
                } else {
                    SkipReason::NoRoute
                };
                skipped.push((i, why));
                remaining.retain(|&r| r != i);
            }
        }
        let costs: Vec<(usize, f64)> = costs.into_iter().filter(|(_, c)| c.is_finite()).collect();
        if costs.is_empty() {
            break;
        }
        let next = pick_next(&costs, req.policy, req.temp, rng);
        let legs = match cur_i {
            Some(from) if (req.recorded)(from, next) => None,
            _ => Some(g.route(cur, req.anchors[next], &[]).unwrap_or_default()),
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
}
