//! The GRIND / TRAVEL / PAUSE state machine.
//!
//! PAUSE is an interruption: entering it remembers the interrupted state,
//! and the all-clear resumes it. Each state checks the environmental safety
//! conditions (focus, hazards) before it runs a tick.

use super::body::Body;
use super::grind::{
    apply_pending_skills, begin_grind, begin_travel, cast_at_anchor, cast_buffs, grind_once,
    publish_summons, run_travel, sync_map, weave_attack,
};
use super::patrol::Patrol;
use super::session::Session;
use super::watchdog::Watchdog;
use crate::skills::SkillKind;
use crate::timing::human_reaction;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    Grind,
    Travel,
    Pause,
}

impl State {
    pub fn as_str(self) -> &'static str {
        match self {
            State::Grind => "GRIND",
            State::Travel => "TRAVEL",
            State::Pause => "PAUSE",
        }
    }
}

enum Next {
    Stay,
    Go(State),
    Resume,
}

pub struct Machine {
    pub state: State,
    /// The state PAUSE interrupted.
    stack: Vec<State>,
    travel_done: bool,
    pub patrol: Patrol,
    watchdog: Watchdog,
}

impl Default for Machine {
    fn default() -> Self {
        Machine {
            state: State::Grind,
            stack: Vec::new(),
            travel_done: false,
            patrol: Patrol::default(),
            watchdog: Watchdog::default(),
        }
    }
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
        body.notify(&format!("{r} detected — pausing"));
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
                if self.travel_done {
                    Next::Go(State::Grind)
                } else {
                    Next::Stay
                }
            }
            State::Pause => {
                if safety(body, false).is_some() || !body.focused() {
                    Next::Stay
                } else {
                    Next::Resume
                }
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
            Next::Resume => match self.stack.pop() {
                Some(s) => s,
                None => return false,
            },
            Next::Go(s) => {
                if s == State::Pause {
                    self.stack.push(self.state);
                }
                s
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
            State::Pause => {
                body.sleep(1.0);
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
