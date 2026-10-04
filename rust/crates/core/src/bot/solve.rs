//! Solving a rune, standing on it: activate, read the arrow puzzle, answer
//! it, then step off to see whether the rune is gone.
//!
//! The player's dot hides the rune while standing on it, so the verdict
//! comes from stepping aside: a rune that shows again failed (and stays
//! locked `LOCK_S`, waited out with small wiggles before the next try); one
//! that stays gone was solved — the machine's rune tracker sees that and
//! goes back to farming. Every delay is human (log-normal, tempo-scaled):
//! a moment to take in the puzzle, uneven gaps between the arrows. Keys go
//! only to a focused game window.

use rand::Rng;
use serde_json::json;

use super::approach::{Approach, RuneApproach};
use super::body::{Body, Dir};
use crate::rune::{gap, platform_under, Rune};
use crate::rune_arrows::{ArrowWatch, Watch};
use crate::timing::human_between;

/// Activations before giving up.
const MAX_ATTEMPTS: u32 = 3;
/// Puzzles that never read before giving up.
const MAX_UNREAD: u32 = 2;
/// How long the puzzle may take to show (and read) after activating (s).
const READ_TIMEOUT_S: f64 = 2.5;
/// Frames this far apart (s): quick enough to see a spinning arrow linger.
const POLL_S: f64 = 0.05;
/// A failed rune can't be activated again for this long (s).
const LOCK_S: f64 = 3.0;
/// Stepped off, the rune shows within this long (s) if it's still there.
const CHECK_S: f64 = 3.0;
/// Taps to get the player's dot off the rune.
const STEP_TAPS: u32 = 5;

#[derive(Debug, Clone, PartialEq)]
pub enum Solve {
    Working,
    GaveUp(String),
    /// Couldn't get back onto the rune: the detour's failure, not the puzzle's.
    Lost(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Default)]
enum Phase {
    #[default]
    Idle,
    /// Press the rune key, not before this time.
    Activate(f64),
    /// Waiting for the puzzle, since this time.
    Read(f64),
    /// Answered (or gave up reading) at this time: step off the rune.
    StepOff(f64),
    /// Off the rune since `.1`, after answering at `.0`: does it show?
    Check(f64, f64),
    /// Failed: wiggle until this time, then go back on.
    Cool(f64),
    /// Back onto the rune.
    Return,
}

#[derive(Default)]
pub struct RuneSolver {
    phase: Phase,
    attempts: u32,
    unread: u32,
    watch: ArrowWatch,
}

impl RuneSolver {
    pub fn reset(&mut self) {
        *self = RuneSolver::default();
    }

    pub fn active(&self) -> bool {
        self.phase != Phase::Idle
    }

    pub fn attempts(&self) -> u32 {
        self.attempts
    }

    /// Standing on the rune: start solving it.
    pub fn begin(&mut self, now: f64) {
        self.phase = Phase::Activate(now + human_between(0.35, 0.2, 0.8, 0.35));
    }

    /// One step of the solve.
    pub fn tick<B: Body + ?Sized>(
        &mut self,
        body: &mut B,
        rune: Rune,
        approach: &mut RuneApproach,
    ) -> Solve {
        let now = body.now();
        match self.phase {
            Phase::Idle => {}
            Phase::Activate(at) => {
                if self.attempts >= MAX_ATTEMPTS {
                    return Solve::GaveUp(format!("not solved after {MAX_ATTEMPTS} attempts"));
                }
                if now < at {
                    body.sleep((at - now).min(0.2));
                    return Solve::Working;
                }
                if !body.focused() {
                    return Solve::Working; // the machine pauses
                }
                let key = body.config().rune_key.clone();
                body.keys().press(&key, None);
                self.attempts += 1;
                body.log(&format!("Rune: activating (attempt {})", self.attempts));
                self.phase = Phase::Read(body.now());
                self.watch = ArrowWatch::default();
                body.sleep(human_between(0.25, 0.15, 0.4, 0.3));
            }
            Phase::Read(since) => {
                if let Some(img) = body.window_frame() {
                    self.watch.add(&img, now);
                }
                let last = now - since > READ_TIMEOUT_S;
                match self.watch.verdict(now, last) {
                    Watch::Read(r, spinning) => {
                        if let Some(i) = spinning.iter().position(|&x| x) {
                            body.log(&format!(
                                "Rune: arrow {} was spinning — answered where it lingered",
                                i + 1
                            ));
                        }
                        self.answer(body, rune, &r.keys());
                    }
                    Watch::Fail(why) => {
                        self.unread += 1;
                        body.log(&format!("Rune: couldn't read the arrows ({why})"));
                        body.rune_event(
                            "unread",
                            json!({ "rune": rune.bbox, "why": why, "attempt": self.attempts }),
                        );
                        if self.unread >= MAX_UNREAD {
                            return Solve::GaveUp("couldn't read its arrows".into());
                        }
                        self.phase = Phase::StepOff(now);
                    }
                    Watch::Wait => {
                        body.sleep(POLL_S);
                    }
                }
            }
            Phase::StepOff(answered) => {
                step_off(body, rune);
                self.phase = Phase::Check(answered, body.now());
            }
            Phase::Check(answered, since) => {
                let shown = body
                    .state_ref()
                    .rune
                    .rune
                    .is_some_and(|r| r.seen_at >= since);
                if shown || now - since > CHECK_S {
                    body.log(&format!(
                        "Rune: attempt {} failed — the rune is still there; retrying after its lock",
                        self.attempts
                    ));
                    let until = (answered + LOCK_S).max(now) + human_between(0.6, 0.3, 1.5, 0.4);
                    self.phase = Phase::Cool(until);
                } else {
                    // Still unseen: the machine calls it solved once it stays gone.
                    body.sleep(human_between(0.15, 0.1, 0.25, 0.3));
                }
            }
            Phase::Cool(until) => {
                if now >= until {
                    approach.reset();
                    self.phase = Phase::Return;
                    return Solve::Working;
                }
                wiggle(body);
            }
            Phase::Return => match approach.tick(body, rune) {
                Approach::Arrived => self.begin(body.now()),
                Approach::Failed(why) => return Solve::Lost(why),
                Approach::Impossible(why) => return Solve::GaveUp(why),
                Approach::Moving => {}
            },
        }
        Solve::Working
    }

    /// Press the arrows read, like a person: a moment to take in the
    /// puzzle, then uneven gaps between keys.
    fn answer<B: Body + ?Sized>(&mut self, body: &mut B, rune: Rune, keys: &[&'static str]) {
        body.log(&format!("Rune: read {} — answering", keys.join(" ")));
        body.rune_event(
            "attempt",
            json!({ "rune": rune.bbox, "read": keys, "attempt": self.attempts }),
        );
        body.sleep(human_between(0.7, 0.4, 1.4, 0.3));
        for (i, k) in keys.iter().enumerate() {
            if !body.should_continue() || !body.focused() {
                body.log("Rune: answer interrupted");
                break;
            }
            if i > 0 {
                body.sleep(human_between(0.3, 0.17, 0.7, 0.35));
            }
            body.keys().press(k, None);
        }
        let answered = body.now();
        body.sleep(human_between(0.5, 0.3, 1.0, 0.3));
        self.phase = Phase::StepOff(answered);
    }
}

/// Tap aside until the player's dot no longer covers the rune: toward the
/// side of its platform with more room.
fn step_off<B: Body + ?Sized>(body: &mut B, rune: Rune) {
    let cx = rune.center().0;
    let dir = body
        .graph()
        .and_then(|g| platform_under(&g, rune.bbox).map(|i| g.platforms[i]))
        .map_or(Dir::Right, |p| {
            if p.x1 - cx >= cx - p.x0 {
                Dir::Right
            } else {
                Dir::Left
            }
        });
    for _ in 0..STEP_TAPS {
        match body.pos() {
            Some(p) if gap(p.0.round() as i32, rune.bbox).is_some() => return,
            None => return,
            _ => {}
        }
        body.tap(dir, human_between(0.12, 0.08, 0.2, 0.3));
        body.sleep(human_between(0.12, 0.08, 0.22, 0.3));
    }
}

/// Small shuffles while the rune is locked: a short tap either way, then a
/// human pause.
fn wiggle<B: Body + ?Sized>(body: &mut B) {
    let dir = if body.state().rng.random::<bool>() {
        Dir::Left
    } else {
        Dir::Right
    };
    body.tap(dir, human_between(0.09, 0.05, 0.16, 0.3));
    body.sleep(human_between(0.55, 0.3, 1.1, 0.35));
}
