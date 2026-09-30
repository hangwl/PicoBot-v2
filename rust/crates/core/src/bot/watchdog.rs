//! Alerts for a bot that is alive but going nowhere: paused too long,
//! blind (no player dot), or standing still while it should be moving.

use super::machine::State;

const PAUSE_S: f64 = 60.0;
const BLIND_S: f64 = 30.0;
const STALL_S: f64 = 45.0;

#[derive(Default)]
pub struct Watchdog {
    paused_at: Option<f64>,
    blind_at: Option<f64>,
    moved_at: f64,
    last_pos: Option<(f64, f64)>,
    /// Which episodes were already announced: pause, blind, stall.
    alerted: [bool; 3],
}

impl Watchdog {
    /// One look at the bot; a message when an episode has just run too
    /// long (each is announced once, and re-arms when it ends).
    pub fn tick(&mut self, now: f64, state: State, player: Option<(f64, f64)>) -> Option<String> {
        if state == State::Pause {
            self.blind_at = None;
            self.moved_at = now;
            self.alerted[1] = false;
            self.alerted[2] = false;
            let since = *self.paused_at.get_or_insert(now);
            return Self::once(&mut self.alerted[0], now - since >= PAUSE_S, || {
                format!("Paused for {:.0}s — needs attention", now - since)
            });
        }
        self.paused_at = None;
        self.alerted[0] = false;
        let Some(p) = player else {
            let since = *self.blind_at.get_or_insert(now);
            return Self::once(&mut self.alerted[1], now - since >= BLIND_S, || {
                format!("Player dot lost for {:.0}s", now - since)
            });
        };
        self.blind_at = None;
        self.alerted[1] = false;
        if self.last_pos != Some(p) {
            self.last_pos = Some(p);
            self.moved_at = now;
            self.alerted[2] = false;
        }
        let idle = now - self.moved_at;
        Self::once(&mut self.alerted[2], idle >= STALL_S, || {
            format!("Bot hasn't moved for {idle:.0}s")
        })
    }

    fn once(flag: &mut bool, due: bool, msg: impl FnOnce() -> String) -> Option<String> {
        if due && !*flag {
            *flag = true;
            return Some(msg());
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_long_pause_alerts_once_and_rearms() {
        let mut w = Watchdog::default();
        assert!(w.tick(0.0, State::Pause, None).is_none());
        assert!(w.tick(59.0, State::Pause, None).is_none());
        assert!(w
            .tick(61.0, State::Pause, None)
            .unwrap()
            .starts_with("Paused"));
        assert!(w.tick(90.0, State::Pause, None).is_none());
        w.tick(91.0, State::Grind, Some((1.0, 1.0)));
        w.tick(92.0, State::Pause, None);
        assert!(w.tick(153.0, State::Pause, None).is_some());
    }

    #[test]
    fn a_missing_dot_alerts_after_a_while() {
        let mut w = Watchdog::default();
        assert!(w.tick(0.0, State::Grind, None).is_none());
        assert!(w.tick(29.0, State::Grind, None).is_none());
        assert!(w
            .tick(31.0, State::Grind, None)
            .unwrap()
            .contains("dot lost"));
        assert!(w.tick(40.0, State::Grind, None).is_none());
    }

    #[test]
    fn standing_still_alerts_but_moving_does_not() {
        let mut w = Watchdog::default();
        for t in 0..100 {
            let x = t as f64;
            assert!(w.tick(x, State::Grind, Some((x, 5.0))).is_none());
        }
        let mut w = Watchdog::default();
        w.tick(0.0, State::Travel, Some((5.0, 5.0)));
        assert!(w.tick(44.0, State::Travel, Some((5.0, 5.0))).is_none());
        assert!(w
            .tick(46.0, State::Travel, Some((5.0, 5.0)))
            .unwrap()
            .contains("moved"));
        assert!(w.tick(60.0, State::Travel, Some((5.0, 5.0))).is_none());
        assert!(w.tick(61.0, State::Travel, Some((6.0, 5.0))).is_none()); // re-armed
    }

    #[test]
    fn a_pause_does_not_count_as_standing_still() {
        let mut w = Watchdog::default();
        w.tick(0.0, State::Grind, Some((5.0, 5.0)));
        w.tick(40.0, State::Pause, Some((5.0, 5.0)));
        assert!(w.tick(41.0, State::Grind, Some((5.0, 5.0))).is_none());
        assert!(w.tick(80.0, State::Grind, Some((5.0, 5.0))).is_none());
    }
}
