//! The GRIND / TRAVEL / RUNE / PAUSE state machine.
//!
//! PAUSE is an interruption: entering it remembers the interrupted state,
//! and the all-clear resumes it. Each state checks the environmental safety
//! conditions (focus, hazards) before it runs a tick.
//!
//! A rune is a goal, not a hazard: RUNE detours to stand beside it, then
//! holds in PAUSE until it's gone (`rune_action: pause` holds at once).

use serde_json::json;

use super::approach::{Approach, RuneApproach};
use super::body::Body;
use super::grind::{
    apply_pending_skills, begin_grind, begin_travel, cast_at_anchor, cast_buffs, grind_once,
    publish_summons, run_travel, sync_map, weave_attack,
};
use super::patrol::Patrol;
use super::session::Session;
use super::watchdog::Watchdog;
use crate::config::RuneAction;
use crate::rune::Rune;
use crate::skills::SkillKind;
use crate::timing::human_reaction;

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
    /// Paused for the rune (beside it, or where it was seen) until it's gone.
    rune_hold: bool,
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
            rune_hold: false,
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

/// Why the bot should halt, if anything: None when clear. `notify` sends a
/// one-shot alert (Pause re-checks quietly).
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
    }
    Some(State::Pause)
}

impl Machine {
    /// Run until the body stops. Keys are released on the way out.
    pub fn run<B: Body + ?Sized>(&mut self, body: &mut B) {
        self.state = State::Grind;
        body.state().session = Session::new(body.now());
        self.enter(body);
        while body.should_continue() {
            self.watch(body);
            if !self.switch(body) && body.should_continue() {
                self.execute(body);
            }
        }
        self.exit(body);
        body.state().viz.state = "STOPPED".into();
    }

    fn watch<B: Body + ?Sized>(&mut self, body: &mut B) {
        let (now, player) = (body.now(), body.state_ref().viz.player);
        if let Some(msg) = self.watchdog.tick(now, self.state, player) {
            body.notify(&msg);
        }
        let every = body.config().heartbeat_minutes * 60.0;
        if body.state().session.beat_due(now, every) {
            let name = body
                .map()
                .map_or("unknown map".to_owned(), |e| e.name.clone());
            let s = body.state_ref().session.summary(now);
            body.notify(&format!(
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
                if rune_now(body).is_some() {
                    return self.on_rune(body);
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
                if rune_now(body).is_some() {
                    return self.on_rune(body);
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
                    None => Next::Go(State::Grind),
                    Some(_) if self.rune_hold => Next::Hold(State::Grind),
                    Some(_) => Next::Stay,
                }
            }
            State::Pause => {
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

    /// A rune showed up while farming: detour to it, or hold here.
    fn on_rune<B: Body + ?Sized>(&mut self, body: &mut B) -> Next {
        match body.config().rune_action {
            RuneAction::Approach => Next::Go(State::Rune),
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
                self.approach.reset();
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
        match self.approach.tick(body, r) {
            Approach::Moving => {}
            Approach::Arrived => {
                self.rune_hold = true;
                body.notify("Rune reached — standing beside it, pausing to solve");
            }
            Approach::Failed(why) => {
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
        cast_buffs(body);
        if rot.anchors.len() >= 2 {
            self.patrol.tick(body);
        } else {
            weave_attack(body);
        }
    }
}
