//! Continuous patrol: strictly execute a pre-planned anchor loop.
//!
//! The loop is a sequence of anchor-to-anchor segments over the movement
//! graph (see [`crate::planner`]). The bot follows the planned legs in
//! order, one per tick; only a failed leg splices a re-route from the
//! player's actual position, and three misses ban the anchor. The next loop
//! is planned before the current one finishes; with no plan, the bot halts
//! and takes a break. Anchors are pure pass-through waypoints.

use std::collections::HashSet;
use std::sync::Arc;

use super::body::{Body, Dir, PatrolStatus};
use super::grind::{blind_wait, legacy_patrol_tick, run_leg};
use super::navigator::{legs_viz, LegStatus, Navigator};
use crate::navgraph::{MoveKind, NavGraph, ROPE_BOTTOM_GAP};
use crate::planner::{plan_loop, LoopRequest, PlanSegment};
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
}

enum Outcome {
    Ok,
    Failed,
    Cooldown,
}

impl Patrol {
    pub fn reset(&mut self) {
        *self = Patrol::default();
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
        if self
            .nav
            .as_ref()
            .is_none_or(|n| !Arc::ptr_eq(&n.graph, &graph))
        {
            self.nav = Some(Navigator::new(graph.clone(), &mut body.state().rng));
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
                self.arrive(body, idx);
                self.seg += 1;
                self.leg_i = 0;
                if self.seg + 1 == self.plan.len() {
                    self.plan_next(body, &graph); // next loop ready before this one ends
                }
                return;
            }
            None => {
                let rot = body.rotation();
                let steps = seg
                    .from
                    .and_then(|f| rot.legs.get(&(f, idx)).cloned())
                    .unwrap_or_default();
                if run_leg(body, &steps) {
                    self.arrive(body, idx);
                    self.seg += 1;
                    if self.seg + 1 == self.plan.len() {
                        self.plan_next(body, &graph);
                    }
                    return;
                }
                Outcome::Failed
            }
            Some(legs) => {
                let leg = legs[self.leg_i];
                let nav = self.nav.as_ref().expect("navigator set above");
                let out = if leg.kind == MoveKind::Walk {
                    if nav.execute_walk(body, leg.x1) {
                        Outcome::Ok
                    } else {
                        Outcome::Failed
                    }
                } else {
                    match nav.execute_leg(body, &leg) {
                        LegStatus::Ok => Outcome::Ok,
                        LegStatus::Failed => Outcome::Failed,
                        LegStatus::Cooldown => Outcome::Cooldown,
                    }
                };
                if matches!(out, Outcome::Ok) {
                    self.leg_i += 1;
                    self.publish(body);
                    return;
                }
                out
            }
        };
        match outcome {
            Outcome::Ok => {}
            Outcome::Cooldown => {
                // Rope lift started cooling: re-route without it (up flash
                // instead) — never wait, never count a failure.
                if !self.splice(body, &graph, pos, idx) {
                    self.ban(body, &graph, pos, idx, "no route");
                }
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
                if self.fails >= 3 {
                    self.ban(body, &graph, pos, idx, "unreachable after retries");
                } else {
                    self.splice(body, &graph, pos, idx);
                }
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
            "Player is not on any drawn platform — waiting (mid-move, or check the platform drawing)",
        );
        let now = body.now();
        match self.stuck {
            Some((since, p)) if (pos.0 - p.0).abs() <= 3.0 && (pos.1 - p.1).abs() <= 3.0 => {
                if now - since > 2.0 {
                    self.stuck = Some((now, p));
                    let exit = exit_dir(graph, pos);
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
        let top = p.y_at(pos.0.clamp(p.x0, p.x1));
        // Keep the bottom end off the platform below (stacked tiers).
        let limit = graph
            .below(pos.0, top, Some(above))
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

    fn request<'a>(
        graph: &'a NavGraph,
        anchors: &'a [(f64, f64)],
        banned: &'a HashSet<usize>,
        recorded: &'a dyn Fn(usize, usize) -> bool,
        cfg: &crate::config::BotConfig,
    ) -> LoopRequest<'a> {
        LoopRequest {
            graph,
            anchors,
            banned,
            tol: cfg.nav_threshold_px as f64,
            recorded,
            policy: cfg.patrol_policy,
            temp: cfg.patrol_weight_temp,
        }
    }

    /// Plan one loop from `cur` (at anchor `cur_i`, if any), recording the
    /// anchors it had to skip.
    fn plan<B: Body + ?Sized>(
        &mut self,
        body: &mut B,
        graph: &NavGraph,
        cur: (f64, f64),
        cur_i: Option<usize>,
    ) -> Vec<PlanSegment> {
        let rot = body.rotation();
        let anchors = body.anchors_px();
        let now = body.now();
        body.state().bans.retain(|_, t| *t > now);
        let banned: HashSet<usize> = body.state().bans.keys().copied().collect();
        let recorded = |a: usize, b: usize| rot.legs.contains_key(&(a, b));
        let cfg = body.config().clone();
        let req = Patrol::request(graph, &anchors, &banned, &recorded, &cfg);
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
        self.plan = self.plan(body, graph, pos, None);
        self.seg = 0;
        self.leg_i = 0;
        self.fails = 0;
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
        let segments = self.plan(body, graph, cur, Some(idx));
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
        let exclude: Vec<MoveKind> = if body.rope_lift_remaining() > 0.0 {
            vec![MoveKind::RopeLift]
        } else {
            Vec::new()
        };
        let goal = body.anchors_px()[idx];
        let Some(legs) = graph.route(pos, goal, &exclude) else {
            return false;
        };
        self.plan[self.seg] = PlanSegment {
            anchor: idx,
            legs: Some(legs),
            from: None,
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
        if let Some(face) = a.face {
            let key = if face == crate::rotation::Face::Left {
                "left"
            } else {
                "right"
            };
            body.keys().press(key, None);
        }
        body.log(&format!("Checkpoint: {}", a.name));
    }

    /// Hold `idx` out of planning for 30s and move past it (and past any
    /// following anchor that can't be spliced either).
    fn ban<B: Body + ?Sized>(
        &mut self,
        body: &mut B,
        graph: &NavGraph,
        pos: (f64, f64),
        idx: usize,
        why: &str,
    ) {
        let rot = body.rotation();
        let name = rot
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
            while let Some(next) = self.plan.get(self.seg).map(|s| s.anchor) {
                if self.splice(body, graph, pos, next) {
                    break;
                }
                body.state().bans.insert(next, now + 30.0);
                self.plan.remove(self.seg);
                let n = rot
                    .anchors
                    .get(next)
                    .map(|a| a.name.clone())
                    .unwrap_or_default();
                body.log(&format!("Patrol: skipping {n} for a while (no route)"));
                body.stat("skip", &n, "no route");
            }
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

fn exit_dir(graph: &NavGraph, pos: (f64, f64)) -> Dir {
    match graph.exit_direction(pos.0, pos.1) {
        crate::navgraph::Direction::Left => Dir::Left,
        crate::navgraph::Direction::Right => Dir::Right,
    }
}
