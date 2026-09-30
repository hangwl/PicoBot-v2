//! Measure real move reach by performing each move a few times.
//!
//! Run with the bot stopped: the measurer executes each move its class can
//! use in the safest available direction and calibrates the reach model to
//! the best result per move — a measurement is ground truth, so it may also
//! lower a too-generous guess. The up flash is measured by its recorded
//! peak (a [`Flight`]), not by the ledge it happened to land on. A separate
//! timing sweep records how the peak depends on the re-press delay.

use serde::Serialize;
use serde_json::{json, Map, Value};

use super::body::{Body, Dir};
use super::flight::{Flight, FlightRecorder};
use crate::config::{BotConfig, ClassTravel};
use crate::effects::{effect_of, Glide};
use crate::navgraph::Platform;
use crate::reach::Move;
use crate::skills::{Skill, SkillKind, Stance};
use crate::vision::platform_span_at;

const FLASH_PLAN: &[&str] = &["flash", "double_flash", "jump", "rope_lift", "up_flash"];
const SINGLE_FLASH_PLAN: &[&str] = &["flash", "jump", "rope_lift", "up_flash"];
const TELEPORT_PLAN: &[&str] = &["jump", "teleport", "rope_lift", "teleport_up"];
const WALK_PLAN: &[&str] = &["jump", "rope_lift"];
const VERTICAL: &[&str] = &["up_flash", "rope_lift", "teleport_up"];
/// Up-flash timing sweep: re-press delays (s) after the first key-down.
pub const PROFILE_DELAYS: [f64; 8] = [0.08, 0.12, 0.16, 0.20, 0.25, 0.30, 0.36, 0.44];
pub const PROFILE_REPS: usize = 3;
pub const PROFILE_CLEAR_PX: f64 = 45.0;
/// Walk-tap sweep: key-hold lengths (ms) and the measured taps for each.
pub const TAP_MS: [u32; 6] = [30, 50, 80, 120, 180, 260];
pub const TAP_REPS: usize = 4;
/// Skill-effect flashes: each is measured this many times, and needs this
/// much platform ahead.
const EFFECT_REPS: usize = 3;
const EFFECT_ROOM_PX: f64 = 70.0;
/// A tap sweep stays this far from a platform end (turning round before it).
const TAP_ROOM_PX: f64 = 30.0;

fn room(m: &str) -> f64 {
    match m {
        "flash" => 34.0,
        "double_flash" => 64.0,
        "jump" => 16.0,
        _ => 40.0, // teleport
    }
}

/// The moves this class's planner can use — only those are worth measuring.
pub fn plan_for(cfg: &BotConfig) -> &'static [&'static str] {
    match cfg.class_travel {
        ClassTravel::Teleport if cfg.teleport_key.is_some() => TELEPORT_PLAN,
        ClassTravel::Flash if cfg.flash_jump_enabled && cfg.double_flash => FLASH_PLAN,
        ClassTravel::Flash if cfg.flash_jump_enabled => SINGLE_FLASH_PLAN,
        _ => WALK_PLAN,
    }
}

/// Samples the player dot through one move.
pub trait Recorder {
    fn start(&mut self);
    fn mark(&mut self, name: &str);
    fn stop(&mut self) -> Flight;
}

impl Recorder for FlightRecorder {
    fn start(&mut self) {
        FlightRecorder::start(self)
    }
    fn mark(&mut self, name: &str) {
        FlightRecorder::mark(self, name)
    }
    fn stop(&mut self) -> Flight {
        FlightRecorder::stop(self)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Moves,
    UpFlashProfile,
    WalkTaps,
    SkillEffects,
}

#[derive(Debug, Clone, Serialize)]
pub struct MeasureStatus {
    pub running: bool,
    #[serde(rename = "move")]
    pub current: Option<String>,
    pub plan: Vec<String>,
    pub results: Map<String, Value>,
    pub mode: Mode,
    pub only: Option<String>,
    pub profile: Vec<Value>,
    pub profiles: Map<String, Value>,
}

pub type Emit = Box<dyn FnMut(&str, &str) + Send>;
pub type OnStatus = Box<dyn FnMut(MeasureStatus) + Send>;

pub struct MoveMeasurer {
    recorder: Option<Box<dyn Recorder + Send>>,
    emit: Emit,
    on_status: OnStatus,
    pub reps: usize,
    /// move -> {"dx"|"rise": px} | {"skipped": reason}
    pub results: Map<String, Value>,
    pub current: Option<String>,
    running: bool,
    mode: Mode,
    only: Option<String>,
    pub profile_rows: Vec<Value>,
}

enum Attempt<T> {
    Got(T),
    Skip(String),
}
use Attempt::{Got, Skip};

impl MoveMeasurer {
    /// `recorder` samples flights (the up flash is then measured by its
    /// peak, and the timing sweep can run); `emit` gets (kind, message)
    /// progress lines and `on_status` every status change.
    pub fn new(
        recorder: Option<Box<dyn Recorder + Send>>,
        emit: Emit,
        on_status: OnStatus,
    ) -> Self {
        MoveMeasurer {
            recorder,
            emit,
            on_status,
            reps: 2,
            results: Map::new(),
            current: None,
            running: false,
            mode: Mode::Moves,
            only: None,
            profile_rows: Vec::new(),
        }
    }

    pub fn status<B: Body + ?Sized>(&self, body: &B) -> MeasureStatus {
        MeasureStatus {
            running: self.running,
            current: self.current.clone(),
            plan: plan_for(body.config())
                .iter()
                .map(|s| s.to_string())
                .collect(),
            results: self.results.clone(),
            mode: self.mode,
            only: self.only.clone(),
            profile: self.profile_rows.clone(),
            profiles: body.state_ref().reach.profiles.clone(),
        }
    }

    fn publish<B: Body + ?Sized>(&mut self, body: &B) {
        let st = self.status(body);
        (self.on_status)(st);
    }

    fn peak_measured(&self, m: &str) -> bool {
        m == "up_flash" && self.recorder.is_some()
    }

    /// Run a whole measurement (blocking); the body's stop ends it early.
    pub fn run<B: Body + ?Sized>(&mut self, body: &mut B, mode: Mode, only: Option<&str>) {
        self.results.clear();
        self.profile_rows.clear();
        self.current = None;
        self.mode = mode;
        self.only = only.map(str::to_owned);
        self.running = true;
        self.publish(body);
        self.run_inner(body);
        // Finished moves are kept even when the run was stopped.
        if let Err(e) = body.state().reach.save(true) {
            (self.emit)("error", &format!("saving reach failed: {e}"));
        }
        self.current = None;
        self.running = false;
        self.publish(body);
    }

    fn run_inner<B: Body + ?Sized>(&mut self, body: &mut B) {
        if body.graph().is_none() {
            (self.emit)(
                "measure",
                "draw the platforms first — nothing to measure against",
            );
            return;
        }
        match self.mode {
            Mode::UpFlashProfile => return self.profile(body),
            Mode::WalkTaps => return self.walk_taps(body),
            Mode::SkillEffects => return self.skill_effects(body),
            Mode::Moves => {}
        }
        let mut plan: Vec<&str> = plan_for(body.config()).to_vec();
        if let Some(only) = self.only.clone() {
            let Some(m) = plan.iter().find(|m| **m == only).copied() else {
                (self.emit)(
                    "measure",
                    &format!("{only} isn't one of this class's moves"),
                );
                return;
            };
            plan = vec![m];
        }
        let what = self.only.clone().unwrap_or_else(|| "moves".into());
        (self.emit)(
            "measure",
            &format!("measuring {what} — keep the game focused"),
        );
        for m in plan {
            self.current = Some(m.to_owned());
            self.publish(body);
            let mut best: Option<f64> = None;
            let mut reason = String::new();
            let peak = self.peak_measured(m);
            let n = if !VERTICAL.contains(&m) || peak {
                self.reps
            } else {
                1
            };
            for _ in 0..n {
                if !body.should_continue() || !body.focused() {
                    (self.emit)("measure", "measurement stopped");
                    return;
                }
                match self.measure(body, m) {
                    Skip(why) => reason = why,
                    // The re-press timing varies: plan on the lowest peak.
                    Got(v) => {
                        if best.is_none_or(|b| if peak { v < b } else { v > b }) {
                            best = Some(v);
                        }
                    }
                }
            }
            self.record(body, m, best, &reason);
        }
        let measured: Vec<&str> = self
            .results
            .iter()
            .filter(|(_, r)| r.get("skipped").is_none())
            .map(|(k, _)| k.as_str())
            .collect();
        let skipped: Vec<&str> = self
            .results
            .iter()
            .filter(|(_, r)| r.get("skipped").is_some())
            .map(|(k, _)| k.as_str())
            .collect();
        let mut summary = format!(
            "done — reach saved. Measured: {}",
            if measured.is_empty() {
                "nothing".into()
            } else {
                measured.join(", ")
            }
        );
        if !skipped.is_empty() {
            summary += &format!("; skipped: {}", skipped.join(", "));
        }
        (self.emit)("measure", &summary);
    }

    /// Calibrate `m` to its best attempt this run (rise for upward moves,
    /// dx for the rest).
    fn record<B: Body + ?Sized>(&mut self, body: &mut B, m: &str, best: Option<f64>, reason: &str) {
        let Some(best) = best else {
            let why = if reason.is_empty() {
                "no usable attempt"
            } else {
                reason
            };
            self.results.insert(m.into(), json!({"skipped": why}));
            self.publish(body);
            return;
        };
        let kind =
            Move::parse(if m == "teleport_up" { "teleport" } else { m }).expect("known move");
        let r1 = (best * 10.0).round() / 10.0;
        let reach = &mut body.state().reach;
        if VERTICAL.contains(&m) {
            reach.calibrate(kind, None, Some(best), Some(m));
            self.results.insert(m.into(), json!({"rise": r1}));
        } else {
            reach.calibrate(kind, Some(best), None, Some(m));
            self.results.insert(m.into(), json!({"dx": r1}));
        }
        self.publish(body);
    }

    fn skip(&mut self, m: &str, why: &str) -> Attempt<f64> {
        (self.emit)("measure", &format!("{m}: skipped — {why}"));
        Skip(why.to_owned())
    }

    /// One attempt: the measured reach, or the reason it was skipped.
    fn measure<B: Body + ?Sized>(&mut self, body: &mut B, m: &str) -> Attempt<f64> {
        if self.peak_measured(m) {
            return self.measure_peak(body, m);
        }
        let graph = body.graph().expect("checked before the run");
        let vertical = VERTICAL.contains(&m);
        let teleport = m == "teleport" || m == "teleport_up";
        if teleport {
            let wait = body.teleport_remaining();
            if wait.is_infinite() {
                return self.skip(m, "teleport key not bound");
            }
            if wait > 0.0 && body.sleep(wait + 0.05) {
                return self.skip(m, "stopped");
            }
        }
        let Some(start) = settle(body, 0.9) else {
            return self.skip(m, "player dot not visible");
        };
        let mut dir = None;
        if vertical {
            if graph.above(start.0, start.1, None).is_none() {
                return self.skip(m, "no platform above the player");
            }
        } else {
            let segs = body.segments_px();
            let Some((x0, x1)) = platform_span_at(&segs, start.0, start.1, 6.0) else {
                return self.skip(m, "player not on a drawn platform");
            };
            let (right, left) = (x1 as f64 - start.0, start.0 - x0 as f64);
            if right.max(left) < room(m) {
                let why = format!(
                    "only {:.0}px of platform room (needs {:.0})",
                    right.max(left),
                    room(m)
                );
                return self.skip(m, &why);
            }
            dir = Some(if right >= left { Dir::Right } else { Dir::Left });
        }
        if m == "rope_lift" && !body.rope_lift() {
            return self.skip(m, "rope lift key not bound or cooling down");
        }
        // teleport() holds its own direction around the key press.
        let held = match (m, dir) {
            ("teleport_up", _) => Some("up"),
            (_, Some(d)) if !teleport => Some(d.key()),
            _ => None,
        };
        if let Some(k) = held {
            body.keys().key_down(k);
        }
        let fired = match m {
            "teleport" => body.teleport(dir),
            "teleport_up" => body.teleport(None),
            "flash" => {
                body.flash_hop();
                true
            }
            "double_flash" => {
                body.double_flash();
                true
            }
            "jump" => {
                let jk = body.config().jump_key.clone();
                body.keys().press(&jk, None);
                body.sleep(0.5);
                true
            }
            "up_flash" => {
                body.up_flash(None);
                true
            }
            _ => true,
        };
        if let Some(k) = held {
            body.keys().key_up(k);
        }
        if !fired {
            return self.skip(m, "teleport not ready");
        }
        let land = settle(body, 1.2);
        let Some(land) = land.filter(|l| *l != start) else {
            return self.skip(m, "the character didn't move");
        };
        let dx = (land.0 - start.0).abs();
        let rise = start.1 - land.1;
        // Rise is positive upward: a sideways move must land on its own level.
        if !vertical && rise < -8.0 {
            return self.skip(
                m,
                &format!("landed {:.0}px lower — fell off a ledge", -rise),
            );
        }
        if !vertical && rise > 8.0 {
            return self.skip(
                m,
                &format!("landed {rise:.0}px higher — caught a ledge above"),
            );
        }
        if vertical && rise <= 0.0 {
            return self.skip(m, "didn't reach the platform above");
        }
        let side = dir.map(|d| format!(" ({})", d.key())).unwrap_or_default();
        (self.emit)(
            "measure",
            &format!("{m}: dx {dx:.0}px rise {rise:.0}px{side}"),
        );
        Got(if vertical { rise } else { dx })
    }

    /// Up flash as it runs on patrol; the recorded peak is its rise.
    fn measure_peak<B: Body + ?Sized>(&mut self, body: &mut B, m: &str) -> Attempt<f64> {
        let flight = match self.fly(body, None, true, false) {
            Got(f) => f,
            Skip(why) => return self.skip(m, &why),
        };
        match flight.peak() {
            Some((t, rise)) if rise >= 2.0 => {
                (self.emit)("measure", &format!("{m}: peak {rise:.0}px at {t:.2}s"));
                Got(rise)
            }
            _ => self.skip(m, "no flight recorded — is the dot visible?"),
        }
    }

    /// Record one flight from standing: a plain jump, or an up flash
    /// (re-press at `delay` s, or the patrol's own timing when None). With
    /// `open_sky`, landing on a platform above is a reason to discard it.
    fn fly<B: Body + ?Sized>(
        &mut self,
        body: &mut B,
        delay: Option<f64>,
        up_flash: bool,
        open_sky: bool,
    ) -> Attempt<Flight> {
        let Some(start) = settle(body, 0.9) else {
            return Skip("player dot not visible".into());
        };
        let rec = self
            .recorder
            .as_deref_mut()
            .expect("flights need a recorder");
        rec.start();
        body.sleep(0.12); // a few standing samples first
        if !up_flash {
            rec.mark("jump");
            let jk = body.config().jump_key.clone();
            body.keys().press(&jk, None);
            body.sleep(0.7);
        } else if delay.is_none() {
            rec.mark("jump");
            body.up_flash(None);
        } else {
            body.up_flash_timed(None, delay, &mut |m| rec.mark(m));
        }
        let land = settle(body, 1.5);
        let flight = rec.stop();
        if let Some(land) = land.filter(|l| open_sky && start.1 - l.1 > 3.0) {
            return Skip(format!(
                "landed {:.0}px higher on a platform — measure where nothing is overhead",
                start.1 - land.1
            ));
        }
        Got(flight)
    }

    // -- Skill effects -------------------------------------------------------------------
    /// Measure the kit's attack and movement skills — or just `only` — in
    /// the air after a flash (flash-jump classes, with flight recording)
    /// and standing on the ground. Each skill's rows are saved as soon as
    /// it is done, so a stopped run keeps what it finished.
    fn skill_effects<B: Body + ?Sized>(&mut self, body: &mut B) {
        let only = self.only.clone();
        let skills: Vec<Skill> = body
            .state_ref()
            .skills
            .skills()
            .iter()
            .filter(|s| matches!(s.kind, SkillKind::Attack | SkillKind::Movement))
            .filter(|s| only.as_ref().is_none_or(|o| &s.name == o))
            .cloned()
            .collect();
        if skills.is_empty() {
            let msg = match &only {
                Some(n) => format!("{n} isn't an attack or movement skill in this kit"),
                None => "no attack or movement skills to measure".to_owned(),
            };
            (self.emit)("measure", &msg);
            return;
        }
        let Some(plat) = self.effect_platform(body) else {
            return;
        };
        let cfg = body.config();
        let air_ok = self.recorder.is_some()
            && cfg.class_travel == ClassTravel::Flash
            && cfg.flash_jump_enabled;
        if !air_ok {
            (self.emit)(
                "measure",
                "air effects need a flash-jump class with flight recording — measuring on the ground only",
            );
        }
        (self.emit)(
            "measure",
            &format!(
                "measuring {} skill(s) — keep the game focused",
                skills.len()
            ),
        );
        let mut base: Vec<Glide> = Vec::new();
        for s in &skills {
            if !body.should_continue() || !body.focused() {
                (self.emit)("measure", "measurement stopped");
                return;
            }
            self.current = Some(s.name.clone());
            self.publish(body);
            let mut rows: Vec<Value> = Vec::new();
            if air_ok && s.stance != Stance::Ground {
                rows.extend(self.air_one(body, s, plat, &mut base));
            }
            if body.should_continue() && s.stance != Stance::Air {
                rows.extend(self.ground_one(body, s, plat));
            }
            if rows.is_empty() {
                (self.emit)("measure", &format!("{}: nothing recorded", s.name));
                continue;
            }
            for r in &rows {
                (self.emit)(
                    "measure",
                    &format!(
                        "{}: {} — {} px{}",
                        s.name,
                        r["where"].as_str().unwrap_or("air"),
                        r["dx"],
                        match r["hang"].as_f64() {
                            Some(h) if h >= 0.1 => format!(", holds {h}s"),
                            _ => String::new(),
                        }
                    ),
                );
            }
            self.store_effects(body, &s.name, rows);
        }
        (self.emit)("measure", "skill effects saved");
    }

    /// Replace `skill`'s saved effect rows with `rows`, keeping the others.
    fn store_effects<B: Body + ?Sized>(&mut self, body: &mut B, skill: &str, rows: Vec<Value>) {
        let mut all: Vec<Value> = body
            .state_ref()
            .reach
            .profiles
            .get("skill_effects")
            .and_then(|p| p.get("rows"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        all.retain(|r| r["skill"].as_str() != Some(skill));
        all.extend(rows);
        self.profile_rows = all.clone();
        body.state().reach.set_profile("skill_effects", all);
        self.publish(body);
    }

    /// The drawn platform the measuring character stands on, if it is long
    /// enough to measure on.
    fn effect_platform<B: Body + ?Sized>(&mut self, body: &mut B) -> Option<Platform> {
        let Some(start) = settle(body, 0.9) else {
            (self.emit)("measure", "player dot not visible");
            return None;
        };
        let graph = body.graph().expect("checked before the run");
        let Some(pi) = graph.locate(start.0, start.1) else {
            (self.emit)("measure", "stand on a drawn platform first");
            return None;
        };
        let plat = graph.platforms[pi];
        if plat.x1 - plat.x0 < 2.0 * EFFECT_ROOM_PX {
            (self.emit)(
                "measure",
                &format!(
                    "this platform is too short — stand on one at least {:.0}px long",
                    2.0 * EFFECT_ROOM_PX
                ),
            );
            return None;
        }
        Some(plat)
    }

    /// The way with more platform ahead of the character, or None (said)
    /// when there isn't room either way.
    fn effect_dir<B: Body + ?Sized>(&mut self, body: &mut B, plat: Platform) -> Option<Dir> {
        let Some(pos) = settle(body, 0.9) else {
            (self.emit)("measure", "player dot lost");
            return None;
        };
        let room = |d: Dir| match d {
            Dir::Right => plat.x1 - pos.0,
            Dir::Left => pos.0 - plat.x0,
        };
        let dir = [Dir::Right, Dir::Left]
            .into_iter()
            .max_by(|a, b| room(*a).total_cmp(&room(*b)))
            .expect("two directions");
        if room(dir) < EFFECT_ROOM_PX {
            (self.emit)(
                "measure",
                "ran out of platform — stand near the middle of a longer one",
            );
            return None;
        }
        Some(dir)
    }

    /// Sleep until `skill` is ready; false (said) when that is over 40 s.
    fn wait_ready<B: Body + ?Sized>(&mut self, body: &mut B, skill: &Skill) -> bool {
        let now = body.now();
        let wait = body.state().skills.remaining(&skill.name, now);
        if wait > 40.0 {
            (self.emit)(
                "measure",
                &format!("{}: on cooldown for {wait:.0}s — skipped", skill.name),
            );
            return false;
        }
        body.sleep(wait + 0.3);
        true
    }

    fn reps_for(skill: &Skill) -> usize {
        if skill.cooldown <= 12.0 {
            EFFECT_REPS
        } else {
            1
        }
    }

    /// One skill cast just after a flash's re-press, against plain flashes
    /// (measured once per run and shared): the air row, if any.
    fn air_one<B: Body + ?Sized>(
        &mut self,
        body: &mut B,
        skill: &Skill,
        plat: Platform,
        base: &mut Vec<Glide>,
    ) -> Option<Value> {
        let mut with: Vec<Glide> = Vec::new();
        let steps = EFFECT_REPS.saturating_sub(base.len()) + Self::reps_for(skill);
        for _ in 0..steps {
            if !body.should_continue() || !body.focused() {
                break;
            }
            let plain = base.len() < EFFECT_REPS;
            if !plain && !self.wait_ready(body, skill) {
                break;
            }
            let dir = self.effect_dir(body, plat)?;
            match self.glide(body, dir, (!plain).then_some(skill)) {
                Skip(why) => (self.emit)("measure", &format!("a flash was discarded — {why}")),
                Got(g) if plain => base.push(g),
                Got(g) => with.push(g),
            }
            body.sleep_between(0.4, 0.28, 0.7);
        }
        let (e, sd) = effect_of(&skill.name, base, &with)?;
        Some(json!({
            "skill": e.skill,
            "where": "air",
            "n": e.n,
            "dx": round_to(e.dx, 1),
            "hang": round_to(e.hang, 2),
            "rise": round_to(e.rise, 1),
            "sd": round_to(sd, 1),
        }))
    }

    /// One skill cast standing still facing a direction with nothing held —
    /// how a landing window casts it: the ground row, if any.
    fn ground_one<B: Body + ?Sized>(
        &mut self,
        body: &mut B,
        skill: &Skill,
        plat: Platform,
    ) -> Option<Value> {
        let mut dxs: Vec<f64> = Vec::new();
        for _ in 0..Self::reps_for(skill) {
            if !body.should_continue() || !body.focused() || !self.wait_ready(body, skill) {
                break;
            }
            let dir = self.effect_dir(body, plat)?;
            match self.ground_cast(body, dir, skill) {
                Skip(why) => (self.emit)("measure", &format!("a cast was discarded — {why}")),
                Got(dx) => dxs.push(dx),
            }
            body.sleep_between(0.4, 0.28, 0.7);
        }
        if dxs.is_empty() {
            return None;
        }
        let m = mean(&dxs);
        let sd = (dxs.iter().map(|d| (d - m).powi(2)).sum::<f64>() / dxs.len() as f64).sqrt();
        Some(json!({
            "skill": skill.name,
            "where": "ground",
            "n": dxs.len(),
            "dx": round_to(m, 1),
            "hang": 0.0,
            "rise": 0.0,
            "sd": round_to(sd, 1),
        }))
    }

    /// Face `dir` (two short taps), cast `skill` with nothing held and see
    /// how far it moved the character that way.
    fn ground_cast<B: Body + ?Sized>(
        &mut self,
        body: &mut B,
        dir: Dir,
        skill: &Skill,
    ) -> Attempt<f64> {
        body.tap(dir, 0.04);
        body.sleep(0.25);
        body.tap(dir, 0.04);
        let Some(start) = settle(body, 0.9) else {
            return Skip("player dot not visible".into());
        };
        body.keys().press(&skill.key, skill.hold);
        let now = body.now();
        body.state().skills.mark_used(&skill.name, now);
        body.sleep(0.9);
        let Some(end) = settle(body, 1.5) else {
            return Skip("player dot lost".into());
        };
        if (end.1 - start.1).abs() > 3.0 {
            return Skip("it left the platform row".into());
        }
        let sign = if dir == Dir::Right { 1.0 } else { -1.0 };
        Got((end.0 - start.0) * sign)
    }

    /// One flash jump toward `dir`, with `skill` cast after the re-press.
    fn glide<B: Body + ?Sized>(
        &mut self,
        body: &mut B,
        dir: Dir,
        skill: Option<&Skill>,
    ) -> Attempt<Glide> {
        let Some(start) = settle(body, 0.9) else {
            return Skip("player dot not visible".into());
        };
        let rec = self
            .recorder
            .as_deref_mut()
            .expect("flights need a recorder");
        rec.start();
        body.sleep(0.12);
        rec.mark("jump");
        let jk = body.flash_key();
        body.keys().key_down(dir.key());
        body.keys().press(&jk, None);
        let gap = body.repress(body.config().flash_repress_seconds);
        body.sleep(gap);
        body.keys().press(&jk, None);
        if let Some(s) = skill {
            body.sleep_between(0.12, 0.09, 0.16);
            body.keys().press(&s.key, s.hold);
            let now = body.now();
            body.state().skills.mark_used(&s.name, now);
        }
        body.sleep(0.9);
        body.keys().key_up(dir.key());
        let land = settle(body, 1.5);
        let flight = rec.stop();
        let Some(land) = land else {
            return Skip("player dot lost".into());
        };
        if (land.1 - start.1).abs() > 3.0 {
            return Skip("it landed on another row".into());
        }
        let (Some((air, _)), Some((_, peak))) = (flight.landing(), flight.peak()) else {
            return Skip("no flight recorded — is the dot visible?".into());
        };
        let sign = if dir == Dir::Right { 1.0 } else { -1.0 };
        Got(Glide {
            dx: (land.0 - start.0) * sign,
            air,
            peak,
        })
    }

    // -- Walk taps and pace ---------------------------------------------------------------
    /// How far taps of several lengths carry, then the walking pace and
    /// slide. Turning round before a platform end; each direction change
    /// spends one discarded tap (the first only turns the character).
    fn walk_taps<B: Body + ?Sized>(&mut self, body: &mut B) {
        let Some(start) = settle(body, 0.9) else {
            (self.emit)("measure", "player dot not visible");
            return;
        };
        let graph = body.graph().expect("checked before the run");
        let Some(pi) = graph.locate(start.0, start.1) else {
            (self.emit)("measure", "stand on a drawn platform first");
            return;
        };
        let plat = graph.platforms[pi];
        if plat.x1 - plat.x0 < 4.0 * TAP_ROOM_PX {
            (self.emit)(
                "measure",
                &format!(
                    "this platform is too short — stand on one at least {:.0}px long",
                    4.0 * TAP_ROOM_PX
                ),
            );
            return;
        }
        (self.emit)(
            "measure",
            &format!(
                "walk-tap sweep: {} taps — keep the game focused",
                TAP_MS.len() * (TAP_REPS + 1)
            ),
        );
        let room = |dir: Dir, x: f64| match dir {
            Dir::Right => plat.x1 - x,
            Dir::Left => x - plat.x0,
        };
        let mut dir = if room(Dir::Right, start.0) >= room(Dir::Left, start.0) {
            Dir::Right
        } else {
            Dir::Left
        };
        let mut facing: Option<Dir> = None;
        let mut rows: Vec<Value> = Vec::new();
        for ms in TAP_MS {
            let mut dxs: Vec<f64> = Vec::new();
            while dxs.len() < TAP_REPS {
                if !body.should_continue() || !body.focused() {
                    (self.emit)("measure", "sweep stopped");
                    self.save_taps(body, rows);
                    return;
                }
                self.current = Some(format!("tap {ms}ms"));
                self.publish(body);
                let Some(pos) = settle(body, 0.9) else {
                    (self.emit)("measure", "sweep stopped — player dot lost");
                    self.save_taps(body, rows);
                    return;
                };
                if room(dir, pos.0) < TAP_ROOM_PX {
                    dir = dir.flip();
                }
                if facing != Some(dir) {
                    body.tap(dir, f64::from(ms) / 1000.0);
                    facing = Some(dir);
                    continue;
                }
                body.tap(dir, f64::from(ms) / 1000.0);
                let Some(after) = settle(body, 0.9) else {
                    (self.emit)("measure", "sweep stopped — player dot lost");
                    self.save_taps(body, rows);
                    return;
                };
                let sign = if dir == Dir::Right { 1.0 } else { -1.0 };
                dxs.push((after.0 - pos.0) * sign);
                body.sleep_between(0.2, 0.12, 0.4);
            }
            let m = mean(&dxs);
            let sd = (dxs.iter().map(|d| (d - m).powi(2)).sum::<f64>() / dxs.len() as f64).sqrt();
            rows.push(json!({
                "ms": ms,
                "n": dxs.len(),
                "dx": round_to(m, 1),
                "sd": round_to(sd, 1),
            }));
            self.profile_rows = rows.clone();
            self.publish(body);
        }
        self.save_taps(body, rows);
        self.walk_pace(body, plat.x0, plat.x1);
    }

    fn save_taps<B: Body + ?Sized>(&mut self, body: &mut B, rows: Vec<Value>) {
        if rows.is_empty() {
            (self.emit)("measure", "sweep recorded nothing");
            return;
        }
        body.state().reach.set_profile("walk_taps", rows);
        (self.emit)("measure", "walk taps saved");
    }

    /// Hold a direction ~0.6 s for the pace, release for the slide; two
    /// runs, one each way.
    fn walk_pace<B: Body + ?Sized>(&mut self, body: &mut B, x0: f64, x1: f64) {
        let (mut speeds, mut slides) = (Vec::new(), Vec::new());
        for dir in [Dir::Right, Dir::Left, Dir::Right, Dir::Left] {
            if !body.should_continue() || !body.focused() {
                break;
            }
            let Some(a) = settle(body, 0.9) else { break };
            let room = if dir == Dir::Right {
                x1 - a.0
            } else {
                a.0 - x0
            };
            if room < 2.0 * TAP_ROOM_PX {
                continue;
            }
            self.current = Some("walking pace".into());
            self.publish(body);
            body.keys().key_down(dir.key());
            let (t0, mut last) = (body.now(), a);
            let mut t1 = t0;
            while body.now() - t0 < 0.6 {
                body.sleep(0.1);
                if let Some(p) = body.pos() {
                    last = p;
                    t1 = body.now();
                }
            }
            body.keys().key_up(dir.key());
            let Some(rest) = settle(body, 0.9) else { break };
            if t1 - t0 > 0.2 {
                speeds.push((last.0 - a.0).abs() / (t1 - t0));
                slides.push((rest.0 - last.0).abs());
            }
        }
        if speeds.is_empty() {
            (self.emit)("measure", "walking pace not measured — not enough room");
            return;
        }
        let row = json!({
            "speed": round_to(mean(&speeds), 1),
            "slide": round_to(mean(&slides), 1),
            "n": speeds.len(),
        });
        body.state()
            .reach
            .set_profile("walk_speed", vec![row.clone()]);
        (self.emit)(
            "measure",
            &format!(
                "walking pace saved — {} px/s, {} px slide",
                row["speed"], row["slide"]
            ),
        );
    }

    // -- Up-flash timing sweep ---------------------------------------------------------
    fn profile<B: Body + ?Sized>(&mut self, body: &mut B) {
        if self.recorder.is_none() {
            (self.emit)("measure", "timing sweep needs flight recording");
            return;
        }
        let cfg = body.config();
        if cfg.class_travel != ClassTravel::Flash || !cfg.flash_jump_enabled {
            (self.emit)("measure", "the up-flash sweep is for flash-jump classes");
            return;
        }
        let Some(start) = settle(body, 0.9) else {
            (self.emit)("measure", "player dot not visible");
            return;
        };
        let graph = body.graph().expect("checked before the run");
        if graph.locate(start.0, start.1).is_none() {
            (self.emit)("measure", "stand on a drawn platform first");
            return;
        }
        if let Some(above) = graph.above(start.0, start.1, None) {
            let gap = start.1 - graph.platforms[above].y_at(start.0);
            if gap < PROFILE_CLEAR_PX {
                (self.emit)(
                    "measure",
                    &format!(
                        "a platform is {gap:.0}px overhead — stand where there's at least {PROFILE_CLEAR_PX:.0}px of open space above"
                    ),
                );
                return;
            }
        }
        let plan: Vec<Option<f64>> = std::iter::once(None)
            .chain(PROFILE_DELAYS.map(Some))
            .collect();
        let mut flights: Vec<(Option<f64>, Vec<Flight>)> =
            plan.iter().map(|d| (*d, Vec::new())).collect();
        (self.emit)(
            "measure",
            &format!(
                "up-flash timing sweep: {} jumps — keep the game focused",
                plan.len() * PROFILE_REPS
            ),
        );
        let mut stopped = false;
        'reps: for _ in 0..PROFILE_REPS {
            for (i, d) in plan.iter().enumerate() {
                if !body.should_continue() || !body.focused() {
                    (self.emit)("measure", "sweep stopped");
                    stopped = true;
                    break 'reps;
                }
                self.current = Some(match d {
                    None => "jump".into(),
                    Some(d) => format!("up_flash @{d:.2}s"),
                });
                self.publish(body);
                match self.fly(body, *d, d.is_some(), true) {
                    Skip(why) => {
                        (self.emit)("measure", &format!("sweep stopped — {why}"));
                        stopped = true;
                        break 'reps;
                    }
                    Got(f) => flights[i].1.push(f),
                }
                self.profile_rows = profile_rows(&flights);
                self.publish(body);
                body.sleep_between(0.35, 0.25, 0.6);
            }
        }
        let rows = profile_rows(&flights);
        if !rows.iter().any(|r| r["n"].as_u64() > Some(0)) {
            (self.emit)("measure", "sweep recorded nothing");
            return;
        }
        let best = rows
            .iter()
            .filter(|r| !r["delay"].is_null() && r["n"].as_u64() > Some(0))
            .max_by(|a, b| a["rise"].as_f64().total_cmp(&b["rise"].as_f64()))
            .map(|r| {
                (
                    r["rise"].as_f64().unwrap_or(0.0),
                    r["delay"].as_f64().unwrap_or(0.0),
                )
            });
        body.state().reach.set_profile("up_flash", rows);
        let mut msg = format!("sweep saved{}", if stopped { " (partial)" } else { "" });
        if let Some((rise, delay)) = best {
            msg += &format!(" — highest peak {rise}px at {delay:.2}s");
        }
        (self.emit)("measure", &msg);
    }
}

trait TotalCmpOpt {
    fn total_cmp(&self, other: &Self) -> std::cmp::Ordering;
}

impl TotalCmpOpt for Option<f64> {
    fn total_cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.unwrap_or(f64::NEG_INFINITY)
            .total_cmp(&other.unwrap_or(f64::NEG_INFINITY))
    }
}

fn round_to(v: f64, places: i32) -> f64 {
    let f = 10f64.powi(places);
    (v * f).round() / f
}

fn mean(v: &[f64]) -> f64 {
    v.iter().sum::<f64>() / v.len() as f64
}

/// One row per delay (null delay = the plain-jump baseline).
pub fn profile_rows(flights: &[(Option<f64>, Vec<Flight>)]) -> Vec<Value> {
    let avg = |v: &[f64]| {
        if v.is_empty() {
            Value::Null
        } else {
            json!(round_to(mean(v), 3))
        }
    };
    flights
        .iter()
        .map(|(delay, fs)| {
            let peaks: Vec<(f64, f64)> = fs.iter().filter_map(Flight::peak).collect();
            let rises: Vec<f64> = peaks.iter().map(|p| p.1).collect();
            let lands: Vec<f64> = fs.iter().filter_map(Flight::landing).map(|l| l.0).collect();
            let gaps: Vec<f64> = fs.iter().filter_map(|f| f.gap("jump", "rejump")).collect();
            let sd = if rises.len() > 1 {
                let m = mean(&rises);
                round_to((rises.iter().map(|r| (r - m).powi(2)).sum::<f64>() / rises.len() as f64).sqrt(), 1)
            } else {
                0.0
            };
            let peak_t: Vec<f64> = peaks.iter().map(|p| p.0).collect();
            json!({
                "delay": delay,
                "n": rises.len(),
                "gap": avg(&gaps),
                "rise": if rises.is_empty() { Value::Null } else { json!(round_to(mean(&rises), 1)) },
                "sd": sd,
                "min": rises.iter().copied().reduce(f64::min),
                "max": rises.iter().copied().reduce(f64::max),
                "peak_t": avg(&peak_t),
                "air": avg(&lands),
            })
        })
        .collect()
}

/// The player's position once it holds still for two reads (or the last
/// read at the timeout).
pub fn settle<B: Body + ?Sized>(body: &mut B, timeout: f64) -> Option<(f64, f64)> {
    let mut last = body.pos()?;
    let end = body.now() + timeout;
    let mut stable = 0;
    while body.now() < end {
        if body.sleep(0.1) || !body.should_continue() {
            return Some(last);
        }
        let Some(pos) = body.pos() else { continue };
        if (pos.0 - last.0).abs() <= 1.0 && (pos.1 - last.1).abs() <= 1.0 {
            stable += 1;
            if stable >= 2 {
                return Some(pos);
            }
        } else {
            stable = 0;
        }
        last = pos;
    }
    Some(last)
}
