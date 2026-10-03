//! Continuous patrol: strictly execute a pre-planned anchor loop.
//!
//! The loop is a sequence of anchor-to-anchor segments over the movement
//! graph (see [`crate::planner`]). The bot follows the planned legs in
//! order, one per tick; only a failed leg splices a re-route from the
//! player's actual position, and two misses ban the anchor. The next loop
//! is planned before the current one finishes; with no plan, the bot halts
//! and takes a break. Anchors are pure pass-through waypoints.

use std::collections::HashSet;
use std::sync::Arc;

use super::body::{Body, Dir, PatrolStatus};
use super::grind::{blind_wait, face_anchor, legacy_patrol_tick, run_leg};
use super::navigator::{interrupted, legs_viz, LegStatus, Navigator};
use crate::config::PatrolMode;
use crate::navgraph::{Leg, MoveKind, NavGraph, ROPE_BOTTOM_GAP, ROPE_TOP_OVERSHOOT};
use crate::planner::{after_legs, plan_loop, Cooldown, LoopRequest, PlanSegment, Sweep};
use crate::timing::human_reaction;

#[derive(Default)]
pub struct Patrol {
    pub plan: Vec<PlanSegment>,
    pub seg: usize,
    pub leg_i: usize,
    pub fails: u32,
    pub arrived: u32,
    halted: bool,
    anchors_sig: Option<Vec<(String, u64, u64)>>,
    nav: Option<Navigator>,
    replan_at: f64,
    /// Since when, and where, the player has held still off the graph.
    pub stuck: Option<(f64, (f64, f64))>,
    /// The current segment must be re-routed from the player's position.
    resplice: bool,
    /// Learned real seconds per second of route cost (0: not yet).
    pace: f64,
    /// When the current segment started, and its planned cost.
    seg_started: f64,
    seg_cost: f64,
    /// The last planned leg run, and how it ended.
    last_leg: Option<(Leg, LegStatus)>,
    /// This spell off the platforms has been reported.
    off_noted: bool,
}

enum Outcome {
    Failed,
    Cooldown,
    Aborted,
}

impl Patrol {
    /// Start over (the learned pace is the character's, so it stays).
    pub fn reset(&mut self) {
        let pace = self.pace;
        *self = Patrol::default();
        self.pace = pace;
    }

    /// The player is being moved off the plan (a rune detour): re-route
    /// the current segment from wherever it ends.
    pub fn detour(&mut self) {
        self.resplice = true;
    }

    /// Real seconds per second of planned route cost (1 until learned).
    pub fn pace(&self) -> f64 {
        if self.pace > 0.0 {
            self.pace
        } else {
            1.0
        }
    }

    /// The current segment starts now.
    fn start_seg(&mut self, now: f64) {
        self.seg_started = now;
        self.seg_cost = self
            .plan
            .get(self.seg)
            .and_then(|s| s.legs.as_ref())
            .map_or(0.0, |l| l.iter().map(|l| l.cost).sum());
    }

    /// Fold the finished segment's real duration into the pace.
    fn learn_pace(&mut self, now: f64) {
        if self.seg_cost < 1.0 {
            return; // too short to say much
        }
        let r = ((now - self.seg_started) / self.seg_cost).clamp(0.5, 4.0);
        self.pace = if self.pace > 0.0 {
            0.8 * self.pace + 0.2 * r
        } else {
            r
        };
    }

    /// Arrived at the current segment's anchor: on to the next segment,
    /// with the next loop planned before this one runs out.
    fn advance<B: Body + ?Sized>(&mut self, body: &mut B, graph: &NavGraph, idx: usize) {
        self.arrive(body, idx);
        let now = body.now();
        self.learn_pace(now);
        self.seg += 1;
        self.leg_i = 0;
        self.start_seg(now);
        if self.seg + 1 == self.plan.len() {
            self.plan_next(body, graph);
        }
    }

    pub fn tick<B: Body + ?Sized>(&mut self, body: &mut B) {
        self.halted = false;
        self.tick_inner(body);
        self.publish_status(body);
    }

    fn status(&self, names: &[String]) -> PatrolStatus {
        let name = |i: usize| names.get(i).cloned().unwrap_or_else(|| "?".into());
        let cur = self.plan.get(self.seg);
        let legs = cur.and_then(|c| c.legs.as_ref());
        let on_leg = legs.is_some_and(|l| self.leg_i < l.len());
        PatrolStatus {
            target: cur.map(|c| name(c.anchor)),
            leg: on_leg.then_some(self.leg_i + 1),
            legs: legs.filter(|l| !l.is_empty()).map(Vec::len),
            move_kind: if on_leg {
                legs.map(|l| l[self.leg_i].kind.as_str().to_owned())
            } else {
                None
            },
            misses: self.fails,
            next: self
                .plan
                .iter()
                .skip(self.seg + 1)
                .take(3)
                .map(|s| name(s.anchor))
                .collect(),
            arrived: self.arrived,
            halted: self.halted,
        }
    }

    fn publish_status<B: Body + ?Sized>(&self, body: &mut B) {
        let names: Vec<String> = body
            .rotation()
            .anchors
            .iter()
            .map(|a| a.name.clone())
            .collect();
        body.state().viz.patrol = Some(self.status(&names));
    }

    /// The anchors were added, removed or moved since the plan was made (a
    /// dashboard edit mid-run): its indices are stale.
    fn anchors_changed<B: Body + ?Sized>(&mut self, body: &mut B) -> bool {
        let sig: Vec<(String, u64, u64)> = body
            .rotation()
            .anchors
            .iter()
            .map(|a| (a.name.clone(), a.x.to_bits(), a.y.to_bits()))
            .collect();
        if self.anchors_sig.as_ref() == Some(&sig) {
            return false;
        }
        let first = self.anchors_sig.is_none();
        self.anchors_sig = Some(sig);
        !first
    }

    fn tick_inner<B: Body + ?Sized>(&mut self, body: &mut B) {
        if self.anchors_changed(body) {
            let (arrived, sig) = (self.arrived, self.anchors_sig.take());
            self.reset();
            self.arrived = arrived;
            self.anchors_sig = sig;
            let st = body.state();
            st.bans.clear();
            st.anchor_idx = 0;
            st.arrive_pending.clear();
            st.travel_target = None;
            body.log("Patrol: anchors changed — replanning");
        }
        let Some(pos) = body.pos() else {
            // Blind: never learn or leap on a guess.
            blind_wait(body);
            return;
        };
        let Some(graph) = body.graph() else {
            legacy_patrol_tick(body); // no drawn platforms
            return;
        };
        if graph.locate(pos.0, pos.1).is_none() {
            self.off_graph(body, &graph, pos);
            return;
        }
        self.stuck = None;
        self.off_noted = false;
        if self
            .nav
            .as_ref()
            .is_none_or(|n| !Arc::ptr_eq(&n.graph, &graph))
        {
            self.nav = Some(Navigator::new(graph.clone(), &mut body.state().rng));
        }
        if self.resplice {
            self.reroute(body, &graph, pos);
        }
        if self.seg >= self.plan.len() {
            let now = body.now();
            if now >= self.replan_at {
                self.replan_at = now + 3.0;
                self.plan_first(body, &graph, pos);
            }
            if self.seg >= self.plan.len() {
                self.halt(body);
                return;
            }
        }
        let seg = self.plan[self.seg].clone();
        let idx = seg.anchor;
        let outcome = match &seg.legs {
            Some(legs) if self.leg_i >= legs.len() => {
                self.advance(body, &graph, idx);
                return;
            }
            None => {
                let rot = body.rotation();
                let steps = seg
                    .from
                    .and_then(|f| rot.legs.get(&(f, idx)).cloned())
                    .unwrap_or_default();
                if run_leg(body, &steps) {
                    self.advance(body, &graph, idx);
                    return;
                }
                if interrupted(body) {
                    Outcome::Aborted
                } else {
                    Outcome::Failed
                }
            }
            Some(legs) => {
                let leg = legs[self.leg_i];
                let nav = self.nav.as_ref().expect("navigator set above");
                let status = nav.execute_leg(body, &leg);
                self.last_leg = Some((leg, status));
                match status {
                    LegStatus::Ok => {
                        self.leg_i += 1;
                        self.publish(body);
                        return;
                    }
                    LegStatus::Failed => Outcome::Failed,
                    LegStatus::Cooldown => Outcome::Cooldown,
                    LegStatus::Aborted => Outcome::Aborted,
                }
            }
        };
        // The leg moved the player: re-route from wherever it ended, on the
        // next tick (which first waits out any time off the platforms).
        match outcome {
            Outcome::Aborted => self.resplice = true,
            Outcome::Cooldown => {
                // Rope lift or teleport started cooling: re-route without
                // it — never wait, never count a failure.
                self.resplice = true;
            }
            Outcome::Failed => {
                self.fails += 1;
                let name = body
                    .rotation()
                    .anchors
                    .get(idx)
                    .map(|a| a.name.clone())
                    .unwrap_or_default();
                body.stat("miss", &name, "");
                body.log(&format!(
                    "Patrol: missed a landing toward {name} ({})",
                    self.fails
                ));
                body.sleep(human_reaction()); // noticing takes a moment
                if self.fails >= 2 {
                    self.ban(body, idx, "unreachable after retries");
                }
                self.resplice = true;
            }
        }
    }

    /// Platforms exist but the player isn't on one: mid-move, or hanging on
    /// an undrawn game rope. Stable for 2s: probe with Down and learn the
    /// rope (or report undrawn ground), then leap off.
    fn off_graph<B: Body + ?Sized>(&mut self, body: &mut B, graph: &NavGraph, pos: (f64, f64)) {
        body.log_every(
            "off_graph",
            5.0,
            &format!(
                "Player is not on any drawn platform at ({:.0}, {:.0}) — waiting (mid-move, or check the platform drawing)",
                pos.0, pos.1
            ),
        );
        if !self.off_noted {
            self.off_noted = true;
            let info = self.off_info(body, graph, pos);
            body.off_platform(pos, info);
        }
        let now = body.now();
        match self.stuck {
            Some((since, p)) if (pos.0 - p.0).abs() <= 3.0 && (pos.1 - p.1).abs() <= 3.0 => {
                if now - since > 2.0 {
                    self.stuck = Some((now, p));
                    let exit: Dir = graph.exit_direction(pos.0, pos.1).into();
                    match body.probe_rope() {
                        Some(true) => {
                            self.learn_rope(body, graph, pos);
                            body.rope_exit(exit);
                        }
                        Some(false) => {
                            body.log(&format!(
                                "Standing off the drawn platforms at ({:.0}, {:.0}) — not a rope; draw the platform there. Hopping back.",
                                pos.0, pos.1
                            ));
                            body.rope_exit(exit);
                        }
                        None => {}
                    }
                }
            }
            _ => self.stuck = Some((now, pos)),
        }
        blind_wait(body);
    }

    /// What the patrol knows about a spell off the platforms: the leg that
    /// led there, the platforms just above and below, and learned ropes
    /// in that column.
    fn off_info<B: Body + ?Sized>(
        &self,
        body: &mut B,
        graph: &NavGraph,
        pos: (f64, f64),
    ) -> serde_json::Value {
        let (x, y) = pos;
        let near = |i: Option<usize>| {
            i.map(|i| {
                let p = graph.platforms[i];
                serde_json::json!({
                    "platform": [p.x0, p.y0, p.x1, p.y1],
                    "dy": ((p.y_at(x) - y) * 10.0).round() / 10.0,
                })
            })
        };
        let target = self.plan.get(self.seg).and_then(|s| {
            body.rotation()
                .anchors
                .get(s.anchor)
                .map(|a| a.name.clone())
        });
        let ropes: Vec<_> = graph
            .ropes
            .iter()
            .filter(|r| ((r[0] + r[2]) / 2.0 - x).abs() <= 8.0)
            .collect();
        serde_json::json!({
            "pos": [x, y],
            "target": target,
            "last_leg": self.last_leg.map(|(l, s)| serde_json::json!({
                "kind": l.kind.as_str(),
                "from": [l.x0, l.y0],
                "to": [l.x1, l.y1],
                "status": format!("{s:?}"),
            })),
            "above": near(graph.above(x, y, None)),
            "below": near(graph.below(x, y, None)),
            "ropes_near": ropes,
        })
    }

    /// Record a confirmed rope hang, connected to the platform above; a
    /// hang on the same column extends that rope instead of adding one.
    fn learn_rope<B: Body + ?Sized>(&mut self, body: &mut B, graph: &NavGraph, pos: (f64, f64)) {
        let (Some(entry), Some((w, h))) = (body.map(), body.region_wh()) else {
            return;
        };
        let Some(above) = graph.above(pos.0, pos.1, None) else {
            return;
        };
        let p = graph.platforms[above];
        let row = p.y_at(pos.0.clamp(p.x0, p.x1));
        let top = (row - ROPE_TOP_OVERSHOOT).max(0.0);
        // Keep the bottom end off the platform below (stacked tiers).
        let limit = graph
            .below(pos.0, row, Some(above))
            .map_or(f64::INFINITY, |u| {
                graph.platforms[u].y_at(pos.0) - ROPE_BOTTOM_GAP
            });
        let bottom = pos.1.min(limit);
        if bottom <= top + 1.0 {
            return;
        }
        let r4 = |v: f64| (v * 1e4).round() / 1e4;
        let mut entry = (*entry).clone();
        let mut ropes = entry.ropes.clone().unwrap_or_default();
        if let Some(r) = ropes
            .iter_mut()
            .find(|r| ((r[0] + r[2]) / 2.0 * w - pos.0).abs() < 5.0)
        {
            let (lo, hi) = (r[1].max(r[3]) * h, r[1].min(r[3]) * h);
            let (nb, nt) = (lo.max(bottom).min(limit), hi.min(top));
            if (nb - lo).abs() <= 0.5 && nt >= hi - 0.5 {
                return; // nothing new
            }
            *r = [r[0], r4(nb / h), r[2], r4(nt / h)];
            entry.ropes = Some(ropes);
            body.save_map(entry);
            body.log(&format!(
                "Extended the rope at x {:.0} (y {nt:.0}-{nb:.0})",
                pos.0
            ));
            return;
        }
        ropes.push([r4(pos.0 / w), r4(bottom / h), r4(pos.0 / w), r4(top / h)]);
        entry.ropes = Some(ropes);
        body.save_map(entry);
        body.log(&format!(
            "Learned a rope at ({:.0}, {:.0}) — climbs there are a last resort",
            pos.0, pos.1
        ));
    }

    /// Rope lift and teleport as the planner sees them now.
    fn cooldowns<B: Body + ?Sized>(body: &B) -> Vec<Cooldown> {
        let cfg = body.config();
        vec![
            Cooldown {
                kind: MoveKind::RopeLift,
                ready_in: body.rope_lift_remaining(),
                every: cfg.up_jump_skill_cooldown,
            },
            Cooldown {
                kind: MoveKind::Teleport,
                ready_in: body.teleport_remaining(),
                every: cfg.teleport_cooldown,
            },
        ]
    }

    /// Plan one loop from `cur` (at anchor `cur_i`, if any), to start once
    /// the `ahead` legs have run; records the anchors it had to skip.
    fn plan<B: Body + ?Sized>(
        &mut self,
        body: &mut B,
        graph: &NavGraph,
        cur: (f64, f64),
        cur_i: Option<usize>,
        ahead: &[Leg],
    ) -> Vec<PlanSegment> {
        let rot = body.rotation();
        let anchors = body.anchors_px();
        let now = body.now();
        body.state().bans.retain(|_, t| *t > now);
        let mut banned: HashSet<usize> = body.state().bans.keys().copied().collect();
        let recorded = |a: usize, b: usize| rot.legs.contains_key(&(a, b));
        let cfg = body.config().clone();
        let cooldowns = after_legs(&Patrol::cooldowns(body), ahead, self.pace());
        let sweeps = match cfg.patrol_mode {
            PatrolMode::Sweep => {
                let (sweeps, covered) = platform_sweeps(graph, &anchors, cfg.sweep_reach_px);
                banned.extend(covered); // their platform is swept for another anchor
                sweeps
            }
            PatrolMode::Anchors => Vec::new(),
        };
        let req = LoopRequest {
            graph,
            anchors: &anchors,
            banned: &banned,
            tol: cfg.nav_threshold_px as f64,
            recorded: &recorded,
            policy: cfg.patrol_policy,
            temp: cfg.patrol_weight_temp,
            cooldowns: &cooldowns,
            pace: self.pace(),
            sweeps: &sweeps,
        };
        let (segments, skipped) = plan_loop(&req, cur, cur_i, &mut body.state().rng);
        for (i, why) in skipped {
            let name = rot.anchors[i].name.clone();
            body.log(&format!(
                "Patrol: skipping {name} for a while ({})",
                why.as_str()
            ));
            body.stat("skip", &name, why.as_str());
            body.state().bans.insert(i, now + 30.0);
        }
        segments
    }

    fn plan_first<B: Body + ?Sized>(&mut self, body: &mut B, graph: &NavGraph, pos: (f64, f64)) {
        self.plan = self.plan(body, graph, pos, None, &[]);
        self.seg = 0;
        self.leg_i = 0;
        self.fails = 0;
        self.start_seg(body.now());
        self.publish(body);
        if !self.plan.is_empty() {
            let names = self.names(body, &self.plan);
            body.log(&format!("Patrol plan: {names}"));
        }
    }

    /// Plan the next loop from this loop's final anchor, while the current
    /// one still has a segment to go — the plan never runs dry.
    fn plan_next<B: Body + ?Sized>(&mut self, body: &mut B, graph: &NavGraph) {
        let idx = self.plan[self.seg].anchor;
        let cur = body.anchors_px()[idx];
        let ahead = self.plan[self.seg].legs.clone().unwrap_or_default();
        let segments = self.plan(
            body,
            graph,
            cur,
            Some(idx),
            &ahead[self.leg_i.min(ahead.len())..],
        );
        if !segments.is_empty() {
            let names = self.names(body, &segments);
            self.plan.extend(segments);
            self.publish(body);
            body.log(&format!("Next loop planned: {names}"));
        }
    }

    fn names<B: Body + ?Sized>(&self, body: &mut B, segs: &[PlanSegment]) -> String {
        let rot = body.rotation();
        segs.iter()
            .map(|s| rot.anchors[s.anchor].name.as_str())
            .collect::<Vec<_>>()
            .join(" → ")
    }

    /// Re-route the current segment from the player's actual position.
    fn splice<B: Body + ?Sized>(
        &mut self,
        body: &mut B,
        graph: &NavGraph,
        pos: (f64, f64),
        idx: usize,
    ) -> bool {
        let mut exclude = Vec::new();
        if body.rope_lift_remaining() > 0.0 {
            exclude.push(MoveKind::RopeLift);
        }
        if body.teleport_remaining() > 0.0 {
            exclude.push(MoveKind::Teleport);
        }
        let sweep = self.plan.get(self.seg).and_then(|s| s.sweep);
        let legs = match sweep {
            // On the swept platform already: on to the exit.
            Some((_, exit)) if graph.locate(pos.0, pos.1) == graph.locate(exit.0, exit.1) => {
                graph.route(pos, exit, &exclude)
            }
            Some((entry, exit)) => graph.route(pos, entry, &exclude).and_then(|mut l| {
                l.extend(graph.route(entry, exit, &exclude)?);
                Some(l)
            }),
            None => graph.route(pos, body.anchors_px()[idx], &exclude),
        };
        let Some(legs) = legs else {
            return false;
        };
        self.plan[self.seg] = PlanSegment {
            anchor: idx,
            legs: Some(legs),
            from: None,
            sweep,
        };
        self.leg_i = 0;
        self.publish(body);
        true
    }

    fn arrive<B: Body + ?Sized>(&mut self, body: &mut B, idx: usize) {
        let rot = body.rotation();
        let Some(a) = rot.anchors.get(idx) else {
            return;
        };
        body.stat("visit", &a.name, "");
        self.fails = 0;
        self.arrived += 1;
        let st = body.state();
        st.anchor_idx = idx;
        st.weave_dir = None;
        st.weave_bounds = None;
        st.arrive_pending = vec![idx];
        st.viz.route = None;
        face_anchor(body, a);
        body.log(&format!("Checkpoint: {}", a.name));
    }

    /// Re-route the current segment from `pos`, skipping past anchors with
    /// no route from here; the next loop is planned if this left the last
    /// segment.
    fn reroute<B: Body + ?Sized>(&mut self, body: &mut B, graph: &NavGraph, pos: (f64, f64)) {
        self.resplice = false;
        while let Some(idx) = self.plan.get(self.seg).map(|s| s.anchor) {
            if self.splice(body, graph, pos, idx) {
                self.start_seg(body.now());
                if self.seg + 1 == self.plan.len() {
                    self.plan_next(body, graph);
                }
                return;
            }
            self.ban(body, idx, "no route");
        }
    }

    /// Hold `idx` out of planning for 30s and drop it from the plan if it's
    /// the current target.
    fn ban<B: Body + ?Sized>(&mut self, body: &mut B, idx: usize, why: &str) {
        let name = body
            .rotation()
            .anchors
            .get(idx)
            .map(|a| a.name.clone())
            .unwrap_or_default();
        body.log(&format!("Patrol: skipping {name} for a while ({why})"));
        body.stat("skip", &name, why);
        let now = body.now();
        body.state().bans.insert(idx, now + 30.0);
        if self.plan.get(self.seg).is_some_and(|s| s.anchor == idx) {
            self.plan.remove(self.seg);
            self.leg_i = 0;
            self.fails = 0;
        }
    }

    /// No planned path: halt all actions until planning succeeds.
    fn halt<B: Body + ?Sized>(&mut self, body: &mut B) {
        self.halted = true;
        body.log_every("break", 5.0, "No planned path — taking a break");
        body.sleep(2.0);
    }

    /// The leg being run and the rest of the plan, for the dashboard.
    fn publish<B: Body + ?Sized>(&self, body: &mut B) {
        let st = body.state();
        if let Some(Some(legs)) = self.plan.get(self.seg).map(|s| s.legs.as_ref()) {
            st.viz.route = Some(legs_viz(&legs[self.leg_i.min(legs.len())..]));
        }
        let plan: Vec<_> = self.plan[self.seg.min(self.plan.len())..]
            .iter()
            .filter_map(|s| s.legs.as_ref())
            .flat_map(|l| legs_viz(l))
            .collect();
        st.viz.plan = Some(plan);
    }
}

/// A sweep stays this far (px) inside its platform's ends: a walk slides
/// a few px past where it lets go, and a target nearer an edge than that
/// ends up over it.
const SWEEP_EDGE_PX: f64 = 8.0;

/// Per anchor, the sweep of its platform — the first anchor on each
/// platform gets it; the others are returned as `covered`. A sweep crosses
/// from just inside one end to `reach` short of the other (the last
/// attack, facing that way, covers the rest); a platform too short for
/// that is one point, its middle. Anchors off every platform get none.
fn platform_sweeps(
    graph: &NavGraph,
    anchors: &[(f64, f64)],
    reach: f64,
) -> (Vec<Option<Sweep>>, Vec<usize>) {
    let mut seen = HashSet::new();
    let mut covered = Vec::new();
    let sweeps = anchors
        .iter()
        .enumerate()
        .map(|(i, &(x, y))| {
            let plat = graph.locate(x, y)?;
            if !seen.insert(plat) {
                covered.push(i);
                return None;
            }
            let p = graph.platforms[plat];
            let pt = |x: f64| (x, p.y_at(x));
            let (left, right) = (p.x0 + SWEEP_EDGE_PX, p.x1 - SWEEP_EDGE_PX);
            let stop = reach.max(SWEEP_EDGE_PX);
            let (stop_l, stop_r) = (p.x0 + stop, p.x1 - stop);
            if stop_r <= left {
                let mid = pt((p.x0 + p.x1) / 2.0);
                return Some([(mid, mid), (mid, mid)]);
            }
            Some([(pt(left), pt(stop_r)), (pt(right), pt(stop_l))])
        })
        .collect();
    (sweeps, covered)
}
