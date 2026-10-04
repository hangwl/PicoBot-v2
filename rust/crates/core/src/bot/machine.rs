//! The GRIND / TRAVEL / RUNE / PAUSE state machine.
//!
//! PAUSE is an interruption: entering it remembers the interrupted state,
//! and the all-clear resumes it. Each state checks the environmental safety
//! conditions (focus, hazards) before it runs a tick.
//!
//! A rune is a goal, not a hazard: RUNE detours to it and (`rune_action:
//! solve`) solves it standing on it, or (`approach`) stands beside it and
//! holds in PAUSE until it's gone; `pause` holds at once. A solve that
//! gives up holds too.

use serde_json::json;

use super::approach::{Approach, RuneApproach, Stand};
use super::body::Body;
use super::grind::{
    apply_pending_skills, begin_grind, begin_travel, cast_at_anchor, cast_buffs_standing,
    grind_once, publish_summons, run_travel, sync_map, weave_attack,
};
use super::patrol::Patrol;
use super::session::Session;
use super::solve::{RuneSolver, Solve};
use super::watchdog::Watchdog;
use crate::config::RuneAction;
use crate::rune::Rune;
use crate::skills::SkillKind;
use crate::timing::{human_between, human_reaction, jittered};

/// Detours to one rune that may fail on the way before holding for it.
const MAX_RUNE_TRIES: u32 = 3;
/// Farming between failed detours (s, mean).
const RUNE_RETRY_S: f64 = 15.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    Grind,
    Travel,
    Rune,
    Pause,
}

impl State {
    pub fn as_str(self) -> &'static str {
        match self {
            State::Grind => "GRIND",
            State::Travel => "TRAVEL",
            State::Rune => "RUNE",
            State::Pause => "PAUSE",
        }
    }
}

enum Next {
    Stay,
    Go(State),
    /// Pause for the rune, then carry on with the given state.
    Hold(State),
    Resume,
}

pub struct Machine {
    pub state: State,
    /// The state PAUSE interrupted.
    paused_from: Option<State>,
    travel_done: bool,
    pub patrol: Patrol,
    watchdog: Watchdog,
    approach: RuneApproach,
    solver: RuneSolver,
    /// Paused for the rune (beside it, or where it was seen) until it's gone.
    rune_hold: bool,
    /// Detours to the current rune that failed on the way.
    rune_tries: u32,
    /// Farming on after a failed detour: the next one not before this.
    rune_retry_at: f64,
    /// A detour failed: back to farming.
    rune_back: bool,
    /// The run ends at this time (`session_max_minutes`).
    session_end: Option<f64>,
    /// The next scheduled rest starts at this time.
    next_break: Option<f64>,
    /// A scheduled rest lasts until this time.
    rest_until: Option<f64>,
}

impl Default for Machine {
    fn default() -> Self {
        Machine {
            state: State::Grind,
            paused_from: None,
            travel_done: false,
            patrol: Patrol::default(),
            watchdog: Watchdog::default(),
            approach: RuneApproach::default(),
            solver: RuneSolver::default(),
            rune_hold: false,
            rune_tries: 0,
            rune_retry_at: f64::NEG_INFINITY,
            rune_back: false,
            session_end: None,
            next_break: None,
            rest_until: None,
        }
    }
}

/// The rune on the map, when runes matter: this frame's sighting folded
/// into the tracker (a rune under the player's dot is still there).
fn rune_now<B: Body + ?Sized>(body: &mut B) -> Option<Rune> {
    if !body.config().stop_when_rune_appears {
        body.state().rune.clear();
        return None;
    }
    let Some(img) = body.frame() else {
        return body.state_ref().rune.rune;
    };
    let seen = body.locate_rune(&img);
    let had = body.state_ref().rune.rune;
    // The player only matters while a remembered rune doesn't show.
    let player = match (seen, had) {
        (None, Some(_)) => body.locate_player(&img),
        _ => None,
    };
    let now = body.now();
    let present = body.state().rune.observe(seen, player, now);
    let rune = body.state_ref().rune.rune.filter(|_| present);
    match (had, rune) {
        (None, Some(r)) => {
            let (x, y) = r.center();
            body.log(&format!("Rune spotted at ({x:.0}, {y:.0})"));
            body.rune_event("seen", json!({ "rune": r.bbox, "player": player }));
        }
        (Some(_), None) => body.log("Rune gone"),
        _ => {}
    }
    rune
}

/// Hazards a person at the keyboard would see; the rest (loading, map
/// identity) are the bot's own business and stop it at once.
fn human_noticeable(reason: &str) -> bool {
    matches!(reason, "other players" | "lie detector")
}

/// How long a person takes to notice and react to `reason`: another
/// player's dot is a glance, a lie-detector window takes a moment more.
fn noticing_delay(reason: &str) -> f64 {
    if reason == "lie detector" {
        human_between(0.7, 0.3, 2.0, 0.5)
    } else {
        human_reaction()
    }
}

/// Why the bot should halt, if anything: None when clear. `notify` sends a
/// one-shot alert (Pause re-checks quietly) and, for hazards a person
/// would see, keeps playing for a human reaction time before stopping.
fn safety<B: Body + ?Sized>(body: &mut B, notify: bool) -> Option<State> {
    if !body.focused() {
        return Some(State::Pause);
    }
    if !body.should_continue() {
        return None;
    }
    let reason = body.hazard()?;
    if notify {
        let mut r = reason.clone();
        if let Some(c) = r.get_mut(0..1) {
            c.make_ascii_uppercase();
        }
        let note = body.hazard_note().unwrap_or_default();
        body.notify(&format!("{r} detected — pausing{note}"));
        if human_noticeable(&reason) {
            body.sleep(noticing_delay(&reason));
        }
    }
    Some(State::Pause)
}

impl Machine {
    /// Run until the body stops. Keys are released on the way out.
    pub fn run<B: Body + ?Sized>(&mut self, body: &mut B) {
        self.state = State::Grind;
        body.state().session = Session::new(body.now());
        self.start_schedule(body);
        self.enter(body);
        while body.should_continue() && !self.session_over(body) {
            self.watch(body);
            if !self.switch(body) && body.should_continue() {
                self.execute(body);
            }
        }
        self.exit(body);
        body.state().viz.state = "STOPPED".into();
    }

    /// Draw this run's length limit and first rest.
    fn start_schedule<B: Body + ?Sized>(&mut self, body: &mut B) {
        let now = body.now();
        let limit = body.config().session_max_minutes * 60.0;
        self.session_end =
            (limit > 0.0).then(|| now + jittered(limit, limit * 0.85, limit * 1.15, 0.1));
        self.rest_until = None;
        self.schedule_break(body);
    }

    /// The next rest: about `break_every_minutes` of farming from now.
    fn schedule_break<B: Body + ?Sized>(&mut self, body: &mut B) {
        let cfg = body.config();
        let every = cfg.break_every_minutes * 60.0;
        self.next_break = (every > 0.0 && cfg.break_minutes > 0.0)
            .then(|| body.now() + jittered(every, every * 0.7, every * 1.3, 0.2));
    }

    /// Whether the run's time is up (announced once).
    fn session_over<B: Body + ?Sized>(&mut self, body: &mut B) -> bool {
        if !self.session_end.is_some_and(|t| body.now() >= t) {
            return false;
        }
        body.notify("Session limit reached — stopping");
        true
    }

    /// A scheduled rest is due: start it (the caller pauses).
    fn break_due<B: Body + ?Sized>(&mut self, body: &mut B) -> bool {
        let now = body.now();
        if !self.next_break.is_some_and(|t| now >= t) {
            return false;
        }
        let mean = body.config().break_minutes * 60.0;
        let len = jittered(mean, mean * 0.7, mean * 1.4, 0.25);
        self.next_break = None;
        self.rest_until = Some(now + len);
        body.log(&format!("Break: resting for {:.1} min", len / 60.0));
        true
    }

    /// Health checks: logged only — alerts are for hazards.
    fn watch<B: Body + ?Sized>(&mut self, body: &mut B) {
        let (now, player) = (body.now(), body.state_ref().viz.player);
        if self.rest_until.is_none() {
            if let Some(msg) = self.watchdog.tick(now, self.state, player) {
                body.log(&msg);
            }
        }
        let every = body.config().heartbeat_minutes * 60.0;
        if body.state().session.beat_due(now, every) {
            let name = body
                .map()
                .map_or("unknown map".to_owned(), |e| e.name.clone());
            let s = body.state_ref().session.summary(now);
            body.log(&format!(
                "Heartbeat: {name} — {} — {s}",
                self.state.as_str()
            ));
        }
    }

    fn check<B: Body + ?Sized>(&mut self, body: &mut B) -> Next {
        match self.state {
            State::Grind => {
                if let Some(s) = safety(body, true) {
                    return Next::Go(s);
                }
                if self.rune_due(body) {
                    return self.on_rune(body);
                }
                if self.break_due(body) {
                    return Next::Go(State::Pause);
                }
                let active = !body.rotation().anchors.is_empty();
                if active && body.state_ref().travel_target.is_some() {
                    return Next::Go(State::Travel);
                }
                Next::Stay
            }
            State::Travel => {
                if let Some(s) = safety(body, true) {
                    return Next::Go(s);
                }
                if self.rune_due(body) {
                    return self.on_rune(body);
                }
                if self.break_due(body) {
                    return Next::Go(State::Pause);
                }
                if self.travel_done {
                    Next::Go(State::Grind)
                } else {
                    Next::Stay
                }
            }
            State::Rune => {
                if let Some(s) = safety(body, true) {
                    return Next::Go(s);
                }
                match rune_now(body) {
                    None => {
                        if self.solver.active() {
                            let n = self.solver.attempts();
                            body.log(&format!("Rune solved (attempt {n})"));
                        }
                        Next::Go(State::Grind)
                    }
                    Some(_) if self.rune_hold => Next::Hold(State::Grind),
                    Some(_) if self.rune_back => {
                        self.rune_back = false;
                        Next::Go(State::Grind)
                    }
                    Some(_) => Next::Stay,
                }
            }
            State::Pause => {
                if let Some(t) = self.rest_until {
                    if body.now() < t {
                        body.state().viz.hazard = Some("scheduled break".into());
                        return Next::Stay;
                    }
                    self.rest_until = None;
                    self.schedule_break(body);
                    body.log("Break over — resuming");
                }
                if safety(body, false).is_some() || !body.focused() {
                    return Next::Stay;
                }
                if self.rune_hold {
                    if rune_now(body).is_some() {
                        body.state().viz.hazard = Some("rune".into());
                        return Next::Stay;
                    }
                    self.rune_hold = false;
                }
                Next::Resume
            }
        }
    }

    /// A rune to go for: one is up and no failed detour is cooling off.
    fn rune_due<B: Body + ?Sized>(&mut self, body: &mut B) -> bool {
        if rune_now(body).is_none() {
            self.rune_tries = 0;
            self.rune_retry_at = f64::NEG_INFINITY;
            return false;
        }
        body.now() >= self.rune_retry_at
    }

    /// A detour failed on the way: farm on and try again shortly, or —
    /// after `MAX_RUNE_TRIES` — hold for the rune.
    fn rune_failed<B: Body + ?Sized>(&mut self, body: &mut B, r: Rune, why: &str) {
        self.rune_tries += 1;
        let (x, y) = r.center();
        body.rune_event(
            "failed",
            json!({ "rune": r.bbox, "why": why, "try": self.rune_tries }),
        );
        if self.rune_tries >= MAX_RUNE_TRIES {
            self.rune_hold = true;
            body.notify(&format!(
                "Rune at ({x:.0}, {y:.0}): {why} ({} tries) — pausing",
                self.rune_tries
            ));
            return;
        }
        let wait = human_between(RUNE_RETRY_S, RUNE_RETRY_S * 0.6, RUNE_RETRY_S * 2.0, 0.3);
        self.rune_retry_at = body.now() + wait;
        self.rune_back = true;
        body.log(&format!(
            "Rune at ({x:.0}, {y:.0}): {why} — farming on, trying again in {wait:.0}s"
        ));
    }

    /// A rune showed up while farming: detour to it, or hold here.
    fn on_rune<B: Body + ?Sized>(&mut self, body: &mut B) -> Next {
        match body.config().rune_action {
            RuneAction::Solve | RuneAction::Approach => Next::Go(State::Rune),
            RuneAction::Pause => {
                self.rune_hold = true;
                body.notify("Rune detected — pausing");
                Next::Hold(self.state)
            }
        }
    }

    /// One transition check; true when the state changed.
    pub fn switch<B: Body + ?Sized>(&mut self, body: &mut B) -> bool {
        if !body.should_continue() {
            return false;
        }
        let next = match self.check(body) {
            Next::Stay => return false,
            Next::Resume => match self.paused_from.take() {
                Some(s) => s,
                None => return false,
            },
            Next::Go(s) => {
                if s == State::Pause {
                    self.paused_from = Some(self.state);
                }
                s
            }
            Next::Hold(after) => {
                self.paused_from = Some(after);
                body.state().viz.hazard = Some("rune".into());
                State::Pause
            }
        };
        self.exit(body);
        self.state = next;
        self.enter(body);
        true
    }

    fn enter<B: Body + ?Sized>(&mut self, body: &mut B) {
        body.state().viz.state = self.state.as_str().into();
        match self.state {
            State::Grind => {
                begin_grind(body);
                body.log("GRIND: farming");
            }
            State::Travel => {
                self.travel_done = !begin_travel(body);
            }
            State::Rune => {
                self.approach.stand = match body.config().rune_action {
                    RuneAction::Solve => Stand::On,
                    _ => Stand::Beside,
                };
                self.approach.reset();
                self.solver.reset();
                self.patrol.detour(); // the patrol resumes from wherever this ends
                body.log("RUNE: heading to the rune");
            }
            State::Pause => {
                let now = body.now();
                body.state().session.pause_begin(now);
                body.keys().release_all();
                body.log("PAUSE: holding until safe");
            }
        }
    }

    fn exit<B: Body + ?Sized>(&mut self, body: &mut B) {
        body.keys().release_all();
        if self.state == State::Pause {
            let now = body.now();
            body.state().session.pause_end(now);
            // A person notices the all-clear before carrying on.
            body.sleep(human_reaction());
        }
    }

    pub fn execute<B: Body + ?Sized>(&mut self, body: &mut B) {
        if sync_map(body) {
            self.patrol.reset();
        }
        match self.state {
            State::Grind => {
                if body.rotation().anchors.is_empty() {
                    grind_once(body);
                } else {
                    self.grind_tick(body);
                }
            }
            State::Travel => {
                run_travel(body);
                self.travel_done = true;
            }
            State::Rune => self.rune_tick(body),
            State::Pause => {
                body.sleep(1.0);
            }
        }
    }

    /// One step of the detour; arriving or failing holds for the rune.
    fn rune_tick<B: Body + ?Sized>(&mut self, body: &mut B) {
        let Some(r) = body.state_ref().rune.rune else {
            return;
        };
        if self.solver.active() {
            match self.solver.tick(body, r, &mut self.approach) {
                Solve::GaveUp(why) => {
                    self.rune_hold = true;
                    body.notify(&format!("Rune: {why} — pausing"));
                }
                Solve::Lost(why) => {
                    self.solver.reset();
                    self.rune_failed(body, r, &why);
                }
                Solve::Working => {}
            }
            return;
        }
        match self.approach.tick(body, r) {
            Approach::Moving => {}
            Approach::Arrived if self.approach.stand == Stand::On => {
                let now = body.now();
                self.solver.begin(now);
            }
            Approach::Arrived => {
                self.rune_hold = true;
                body.notify("Rune reached — standing beside it, pausing to solve");
            }
            Approach::Failed(why) => self.rune_failed(body, r, &why),
            Approach::Impossible(why) => {
                self.rune_hold = true;
                let (x, y) = r.center();
                body.rune_event("failed", json!({ "rune": r.bbox, "why": why }));
                body.notify(&format!("Rune at ({x:.0}, {y:.0}): {why} — pausing"));
            }
        }
    }

    /// One farming tick: arrival skills, buffs, then patrol (or the single
    /// anchor's weave).
    pub fn grind_tick<B: Body + ?Sized>(&mut self, body: &mut B) {
        apply_pending_skills(body);
        let pending = std::mem::take(&mut body.state().arrive_pending);
        let rot = body.rotation();
        for idx in pending {
            cast_at_anchor(body, &rot, idx);
        }
        if body
            .state_ref()
            .skills
            .skills()
            .iter()
            .any(|s| s.kind == SkillKind::Summon)
        {
            publish_summons(body);
        }
        if rot.anchors.len() >= 2 {
            self.patrol.tick(body); // buffs go out at its checkpoints
        } else {
            cast_buffs_standing(body); // no checkpoints to wait for
            weave_attack(body);
        }
    }
}
