//! Farming routines around the patrol: map sync, arrival skills and
//! summons, buffs, the single-anchor weave, the no-platform checkpoint
//! patrol, and recorded legs.

use super::body::{Body, Dir, Travel};
use super::navigator::Navigator;
use crate::rotation::{ClimbDir, Rotation, Step, TravelStyle};
use crate::skills::{Skill, SkillBook, SkillKind};
use crate::vision::platform_span_at;

/// Apply a map change (the host bumps `map_version`): summons stay on the
/// old map; the skill book becomes the config's plus the map's own, with
/// cooldowns carried over. True when the map changed.
pub fn sync_map<B: Body + ?Sized>(body: &mut B) -> bool {
    let v = body.map_version();
    if v == body.state_ref().map_version {
        return false;
    }
    let map = body.map();
    let mut book = SkillBook::new(body.config().skills.clone());
    if let Some(e) = &map {
        book.overlay(&e.skills);
        body.log(&format!("Map: {}", e.name));
    }
    let st = body.state();
    st.map_version = v;
    st.summons.reset();
    st.skills = book.carry_from(&st.skills);
    st.anchor_idx = 0;
    st.travel_target = None;
    st.weave_dir = None;
    st.weave_bounds = None;
    st.route.clear();
    st.checkpoint = None;
    true
}

/// Swap in skill edits from the dashboard (on the bot thread).
pub fn apply_pending_skills<B: Body + ?Sized>(body: &mut B) {
    let st = body.state();
    if let Some(skills) = st.pending_skills.take() {
        st.skills = SkillBook::new(skills).carry_from(&st.skills);
    }
}

/// No player dot: wait briefly — attacks only happen inside flash moves,
/// and moving blind could leave the platform.
pub fn blind_wait<B: Body + ?Sized>(body: &mut B) {
    body.log_every("blind", 5.0, "Player dot not visible — waiting");
    body.sleep(0.15);
}

/// Skills allowed at an anchor: its `on_arrive` list, or every summon.
fn anchor_skills<B: Body + ?Sized>(body: &mut B, rot: &Rotation, idx: usize) -> Vec<Skill> {
    let st = body.state();
    let anchor = &rot.anchors[idx];
    let names: Vec<String> = if anchor.on_arrive.is_empty() {
        st.skills
            .skills()
            .iter()
            .filter(|s| s.kind == SkillKind::Summon)
            .map(|s| s.name.clone())
            .collect()
    } else {
        anchor.on_arrive.clone()
    };
    names
        .iter()
        .filter_map(|n| st.skills.get(n).cloned())
        .collect()
}

/// Summons need the character standing on a platform: two reads a moment
/// apart, both within 1px and on a drawn line — not mid-air, not a rope.
pub fn standing_on_platform<B: Body + ?Sized>(body: &mut B) -> bool {
    let Some(a) = body.pos() else { return false };
    body.sleep_between(0.08, 0.06, 0.12);
    let Some(b) = body.pos() else { return false };
    if (a.0 - b.0).abs() > 1.0 || (a.1 - b.1).abs() > 1.0 {
        return false;
    }
    body.graph().is_none_or(|g| g.locate(b.0, b.1).is_some())
}

/// At a checkpoint: listed non-summon skills fire when ready; then one
/// summon — only if the anchor has none live, one allowed here has a
/// charge, and the character stands on a platform. The fullest skill
/// (most charges banked) goes first.
pub fn cast_at_anchor<B: Body + ?Sized>(body: &mut B, rot: &Rotation, idx: usize) {
    let allowed = anchor_skills(body, rot, idx);
    let name = rot.anchors[idx].name.clone();
    for s in allowed.iter().filter(|s| s.kind != SkillKind::Summon) {
        let now = body.now();
        if body.state().skills.ready(&s.name, now) {
            body.use_skill(s);
        }
    }
    let now = body.now();
    let summons: Vec<Skill> = allowed
        .iter()
        .filter(|s| s.kind == SkillKind::Summon && body.state().skills.charges(&s.name, now) > 0)
        .cloned()
        .collect();
    if summons.is_empty() || !body.state().summons.anchor_free(&name, now) {
        return;
    }
    if !standing_on_platform(body) {
        body.log(&format!(
            "Summon skipped at {name}: not standing on a platform"
        ));
        return;
    }
    let st = body.state();
    let fullness = |s: &Skill, st: &mut super::body::BotState| {
        let c = st.skills.charges(&s.name, now) as f64;
        (c / s.charges as f64, c)
    };
    let mut best = summons[0].clone();
    let mut best_key = fullness(&best, st);
    for s in &summons[1..] {
        let k = fullness(s, st);
        if k > best_key {
            best = s.clone();
            best_key = k;
        }
    }
    if !body.use_skill(&best) {
        return;
    }
    let now = body.now();
    let gone = body.state().summons.place(&best, &name, now);
    let left = body.state().skills.charges(&best.name, now);
    let note = gone.map_or(String::new(), |g| {
        format!("; the one at {} expired early", g.anchor)
    });
    body.log(&format!(
        "Summon {} at {name} ({left}/{} charges left){note}",
        best.name, best.charges
    ));
    publish_summons(body);
}

/// Summon placements and charges, for the dashboard.
pub fn publish_summons<B: Body + ?Sized>(body: &mut B) {
    let now = body.now();
    let st = body.state();
    let placed: Vec<(String, String, f64)> = st
        .summons
        .active(now)
        .iter()
        .map(|p| (p.skill.clone(), p.anchor.clone(), (p.expires - now).round()))
        .collect();
    let summon_skills: Vec<Skill> = st
        .skills
        .skills()
        .iter()
        .filter(|s| s.kind == SkillKind::Summon)
        .cloned()
        .collect();
    let charges = summon_skills
        .iter()
        .map(|s| (s.name.clone(), st.skills.charges(&s.name, now), s.charges))
        .collect();
    st.viz.summons = placed;
    st.viz.summon_charges = charges;
}

/// Fire the buffs that are due.
pub fn cast_buffs<B: Body + ?Sized>(body: &mut B) {
    let now = body.now();
    let buffs = body.state().skills.due_buffs(now);
    for buff in buffs {
        if !body.should_continue() {
            break;
        }
        if body.use_skill(&buff) {
            body.sleep_between(0.3, 0.2, 0.45);
        }
    }
}

/// Both points are on drawn platforms, but different ones.
fn other_platform<B: Body + ?Sized>(body: &mut B, pos: (f64, f64), goal: (f64, f64)) -> bool {
    let Some(g) = body.graph() else { return false };
    matches!((g.locate(pos.0, pos.1), g.locate(goal.0, goal.1)), (Some(a), Some(b)) if a != b)
}

/// `pos` stands where a weave can't reach `goal`: another drawn platform,
/// or — with none drawn — another level.
fn off_home<B: Body + ?Sized>(body: &mut B, pos: (f64, f64), goal: (f64, f64)) -> bool {
    if body.graph().is_some() {
        return other_platform(body, pos, goal);
    }
    let band = (body.config().nav_threshold_px as f64 * 2.0).max(8.0);
    (goal.1 - pos.1).abs() > band
}

/// One flash weave bouncing across the platform under `(ax, ay)`. With
/// `home` set, a player away from that platform queues a TRAVEL back to
/// it — or, while that anchor is banned, halts.
pub fn weave_around<B: Body + ?Sized>(body: &mut B, ax: f64, ay: f64, home: Option<usize>) {
    let pos = body.pos();
    if let (Some(h), Some(p)) = (home, pos) {
        if off_home(body, p, (ax, ay)) {
            let now = body.now();
            if body.state().bans.get(&h).is_some_and(|t| *t > now) {
                body.log_every("no_path_home", 5.0, "No planned path home — taking a break");
                body.sleep(2.0);
            } else {
                body.state().travel_target = Some(h);
            }
            return;
        }
    }
    if body.state().weave_bounds.is_none() {
        let segs = body.segments_px();
        body.state().weave_bounds =
            platform_span_at(&segs, ax, ay, 6.0).map(|(a, b)| (a as f64, b as f64));
    }
    let cfg = body.config().clone();
    let m = cfg.weave_edge_margin_px as f64;
    let (mut lo, mut hi) = (
        ax - cfg.weave_range_px as f64,
        ax + cfg.weave_range_px as f64,
    );
    if let Some((b0, b1)) = body.state().weave_bounds {
        if b0 + m < b1 - m {
            (lo, hi) = (b0 + m, b1 - m);
        }
    }
    let hop = body.state().hop_px;
    let mut dir = match body.state().weave_dir {
        Some(d) => d,
        None => {
            if rand::Rng::random::<bool>(&mut body.state().rng) {
                Dir::Left
            } else {
                Dir::Right
            }
        }
    };
    if let Some(p) = pos {
        // The drawn platform's ends are the boundaries: bounce before a hop
        // could leave it.
        if p.0 <= lo {
            dir = Dir::Right;
        } else if p.0 >= hi {
            dir = Dir::Left;
        } else if rand::Rng::random::<f64>(&mut body.state().rng) < 0.06 {
            dir = dir.flip();
        }
        let (room_l, room_r) = (p.0 - lo, hi - p.0);
        if dir == Dir::Left && room_l < hop && hop <= room_r {
            dir = Dir::Right;
        } else if dir == Dir::Right && room_r < hop && hop <= room_l {
            dir = Dir::Left;
        } else if room_l < hop && room_r < hop {
            dir = if room_l > room_r {
                Dir::Left
            } else {
                Dir::Right
            };
        }
    }
    body.state().weave_dir = Some(dir);
    body.weave_move(dir);
}

/// A single anchor (or none reachable): keep moving on its platform.
pub fn weave_attack<B: Body + ?Sized>(body: &mut B) {
    let rot = body.rotation();
    let idx = body.state().anchor_idx;
    match rot.anchors.get(idx) {
        Some(a) => {
            let (x, y) = (body.rx(a.x), body.ry(a.y));
            weave_around(body, x, y, Some(idx));
        }
        None => grind_once(body),
    }
}

/// No rotation: due buffs, then weave-hop around where grinding started.
pub fn grind_once<B: Body + ?Sized>(body: &mut B) {
    apply_pending_skills(body);
    if !body.focused() || !body.should_continue() {
        return;
    }
    cast_buffs(body);
    if body.state().roam_origin.is_none() {
        body.state().roam_origin = body.pos();
    }
    match body.state().roam_origin {
        Some((x, y)) => weave_around(body, x, y, None),
        None => blind_wait(body),
    }
}

/// Order the anchors nearest-first from `pos` (no-platform patrol).
fn plan_route<B: Body + ?Sized>(body: &mut B, pos: (f64, f64)) {
    let cfg = body.config().clone();
    let band = (cfg.nav_threshold_px as f64 * 2.0).max(8.0);
    let tol = cfg.nav_threshold_px as f64;
    let px = body.anchors_px();
    let now = body.now();
    let st = body.state();
    st.bans.retain(|_, t| *t > now);
    let mut remaining: Vec<usize> = (0..px.len())
        .filter(|&i| (px[i].0 - pos.0).abs() > tol || (px[i].1 - pos.1).abs() > band)
        .filter(|i| !st.bans.contains_key(i))
        .collect();
    let mut route = Vec::new();
    let mut cur = pos;
    while !remaining.is_empty() {
        let d = |i: usize| (px[i].0 - cur.0).powi(2) + (px[i].1 - cur.1).powi(2);
        let (k, &next) = remaining
            .iter()
            .enumerate()
            .min_by(|a, b| d(*a.1).total_cmp(&d(*b.1)))
            .unwrap();
        route.push(next);
        remaining.remove(k);
        cur = px[next];
    }
    st.route = route;
    st.checkpoint = None;
}

/// Checkpoint patrol for maps without drawn platforms: weave toward the
/// route's head; other levels are handed to TRAVEL; a head that won't
/// arrive in 20s is skipped and briefly banned.
pub fn legacy_patrol_tick<B: Body + ?Sized>(body: &mut B) {
    let rot = body.rotation();
    if rot.anchors.len() < 2 {
        weave_attack(body);
        return;
    }
    let Some(pos) = body.pos() else {
        blind_wait(body);
        return;
    };
    let cfg = body.config().clone();
    let now = body.now();
    let band = (cfg.nav_threshold_px as f64 * 2.0).max(8.0);
    let tol = cfg.nav_threshold_px as f64;
    let mut head = None;
    for _ in 0..=rot.anchors.len() {
        if body.state().route.is_empty() {
            plan_route(body, pos);
            if body.state().route.is_empty() {
                weave_attack(body); // every anchor already within reach
                return;
            }
        }
        let idx = body.state().route[0];
        let t = (body.rx(rot.anchors[idx].x), body.ry(rot.anchors[idx].y));
        if (t.1 - pos.1).abs() > band || other_platform(body, pos, t) {
            // Another level: TRAVEL owns that transition.
            let st = body.state();
            st.route.remove(0);
            st.checkpoint = None;
            st.travel_target = Some(idx);
            return;
        }
        if (t.0 - pos.0).abs() > tol {
            head = Some((idx, t));
            break;
        }
        let st = body.state();
        st.route.remove(0);
        st.anchor_idx = idx;
        st.checkpoint = None;
        st.arrive_pending = vec![idx];
        if let Some(face) = rot.anchors[idx].face {
            let key = if face == crate::rotation::Face::Left {
                "left"
            } else {
                "right"
            };
            body.keys().press(key, None);
        }
        let name = rot.anchors[idx].name.clone();
        body.log(&format!("Checkpoint: {name}"));
        body.stat("visit", &name, "");
    }
    let Some((idx, (tx, _))) = head else {
        weave_attack(body);
        return;
    };
    // A head that won't arrive in time is skipped.
    match body.state().checkpoint {
        Some((c, deadline)) if c == idx => {
            if now > deadline {
                let name = rot.anchors[idx].name.clone();
                body.log(&format!("Checkpoint {name} unreachable — skipping"));
                body.stat("skip", &name, "unreachable after 20s");
                let st = body.state();
                st.route.remove(0);
                st.bans.insert(idx, now + 30.0);
                st.checkpoint = None;
                return;
            }
        }
        _ => body.state().checkpoint = Some((idx, now + 20.0)),
    }
    let dx = tx - pos.0;
    let mut dir = body
        .state()
        .weave_dir
        .unwrap_or(Dir::toward(if dx >= 0.0 { 1.0 } else { -1.0 }));
    if dx > tol {
        dir = Dir::Right;
    } else if dx < -tol {
        dir = Dir::Left;
    }
    body.state().weave_dir = Some(dir);
    body.weave_move(dir);
}

/// Run a hand-recorded leg; true when it completed with no hazard.
pub fn run_leg<B: Body + ?Sized>(body: &mut B, steps: &[Step]) -> bool {
    let rot = body.rotation();
    let jitter = rot.position_jitter_px;
    for step in steps {
        if !body.should_continue() || !body.focused() {
            return false;
        }
        match *step {
            Step::WalkTo { x, y, style } => {
                let (jx, jy) = if jitter > 0 {
                    let r = &mut body.state().rng;
                    (
                        rand::Rng::random_range(r, -jitter..=jitter),
                        rand::Rng::random_range(r, -jitter..=jitter),
                    )
                } else {
                    (0, 0)
                };
                let style = match style.unwrap_or(rot.travel_style) {
                    TravelStyle::Walk => Travel::Walk,
                    TravelStyle::Flash => Travel::Flash,
                    TravelStyle::Mixed => Travel::Mixed,
                };
                let (tx, ty) = (body.rx(x) + jx as f64, body.ry(y) + jy as f64);
                if !body.move_to_point(tx, ty, None, style, false) {
                    return false;
                }
            }
            Step::Climb { dir, until_y, x } => {
                let (ty, tx) = (body.ry(until_y), x.map(|x| body.rx(x)));
                if !body.climb(dir == ClimbDir::Up, ty, tx) {
                    return false;
                }
            }
            Step::UpJump => {
                body.up_jump();
            }
            Step::DownJump => body.down_jump(),
            Step::Wait { seconds } => {
                if body.sleep(seconds) {
                    return false;
                }
            }
        }
    }
    body.hazard().is_none()
}

/// Enter GRIND at the current anchor: face it and queue its arrival skills.
pub fn begin_grind<B: Body + ?Sized>(body: &mut B) {
    let rot = body.rotation();
    let st = body.state();
    st.weave_dir = None;
    st.weave_bounds = None;
    st.roam_origin = None;
    let idx = st.anchor_idx;
    match rot.anchors.get(idx) {
        Some(a) => {
            body.state().arrive_pending = vec![idx];
            if let Some(face) = a.face {
                let key = if face == crate::rotation::Face::Left {
                    "left"
                } else {
                    "right"
                };
                body.keys().press(key, None);
            }
        }
        None => body.state().arrive_pending.clear(),
    }
}

/// Arm the next TRAVEL leg: a preset target, else the route's head, else
/// the nearest anchor. False without anchors.
pub fn begin_travel<B: Body + ?Sized>(body: &mut B) -> bool {
    let rot = body.rotation();
    if rot.anchors.is_empty() {
        return false;
    }
    if body.state().anchor_idx >= rot.anchors.len() {
        body.state().anchor_idx = 0;
    }
    let target = match (
        body.state().travel_target,
        body.state().route.first().copied(),
    ) {
        (Some(t), _) => t,
        (None, Some(r)) => r,
        (None, None) => match body.pos() {
            Some(p) => {
                let px = body.anchors_px();
                let d = |i: usize| (px[i].0 - p.0).powi(2) + (px[i].1 - p.1).powi(2);
                (0..px.len())
                    .min_by(|a, b| d(*a).total_cmp(&d(*b)))
                    .unwrap_or(0)
            }
            None => body.state().anchor_idx,
        },
    };
    body.state().travel_target = Some(target);
    body.log(&format!("TRAVEL: → {}", rot.anchors[target].name));
    true
}

/// Run the armed leg: a recorded one if it exists, else the navigator.
/// A failed target is held out of routes for 45s.
pub fn run_travel<B: Body + ?Sized>(body: &mut B) -> bool {
    let rot = body.rotation();
    let Some(target) = body.state().travel_target else {
        return false;
    };
    let a = &rot.anchors[target];
    let goal = (body.rx(a.x), body.ry(a.y));
    let from = body.state().anchor_idx;
    let graph = body.graph();
    let ok = match (rot.legs.contains_key(&(from, target)), graph) {
        (false, Some(g)) if g.locate(goal.0, goal.1).is_some() => {
            let nav = Navigator::new(g, &mut body.state().rng);
            let ok = nav.go(body, goal, 3, 40) && body.hazard().is_none();
            body.state().viz.route = None;
            ok
        }
        _ => {
            let steps = rot.leg_steps(from, target);
            run_leg(body, &steps)
        }
    };
    let now = body.now();
    let st = body.state();
    st.travel_target = None;
    if ok {
        st.anchor_idx = target;
        st.bans.remove(&target);
    } else {
        st.bans.insert(target, now + 45.0);
        body.log("Leg incomplete — checkpoint held out of routes for a bit");
    }
    ok
}
