//! Keyboard and mouse commands to the Pico, with held-key tracking.
//!
//! The firmware relays raw down/up events, so key-hold duration and the
//! spacing between events are decided here (human-like, see
//! `picobot_core::timing`). A key counts as held from the moment a press
//! is *attempted* — a press whose ACK timed out may still land — and
//! dropping the controller releases everything it holds.

use std::collections::BTreeSet;
use std::sync::Arc;
use std::time::Duration;

use picobot_core::timing::{human_hold, key_gap, monotonic};

use crate::serial::SerialLink;

type SendFn = Box<dyn FnMut(&str) -> bool + Send>;
type SleepFn = Box<dyn FnMut(f64) + Send>;
type GapFn = Box<dyn FnMut() -> f64 + Send>;
type ClockFn = Box<dyn Fn() -> f64 + Send>;

pub struct HidController {
    send: SendFn,
    sleep: SleepFn,
    gap: Option<GapFn>,
    clock: ClockFn,
    held: BTreeSet<String>,
    last_event: Option<f64>,
}

impl HidController {
    /// Real timing: blocking sleeps, human key gaps, the monotonic clock.
    pub fn new(send: impl FnMut(&str) -> bool + Send + 'static) -> Self {
        HidController::with(
            send,
            |s| std::thread::sleep(Duration::from_secs_f64(s.max(0.0))),
            Some(Box::new(key_gap)),
            monotonic,
        )
    }

    /// Injectable sleep, key gap and clock (tests; or a stop-aware sleep).
    pub fn with(
        send: impl FnMut(&str) -> bool + Send + 'static,
        sleep: impl FnMut(f64) + Send + 'static,
        gap: Option<GapFn>,
        clock: impl Fn() -> f64 + Send + 'static,
    ) -> Self {
        HidController {
            send: Box::new(send),
            sleep: Box::new(sleep),
            gap,
            clock: Box::new(clock),
            held: BTreeSet::new(),
            last_event: None,
        }
    }

    /// Commands go over `link`, each waiting up to `timeout` for its ACK.
    pub fn for_link(link: Arc<SerialLink>, timeout: Duration) -> Self {
        HidController::new(move |payload| link.send_acked(payload, timeout).is_ok())
    }

    /// Fingers never land at once: consecutive key events are spaced by at
    /// least a drawn human gap. Time already spent (a deliberate sleep, the
    /// serial round trip) counts toward it, so timed sequences barely shift.
    fn space(&mut self) {
        if let (Some(gap), Some(last)) = (self.gap.as_mut(), self.last_event) {
            let wait = gap() - ((self.clock)() - last);
            if wait > 0.0 {
                (self.sleep)(wait);
            }
        }
        self.last_event = Some((self.clock)());
    }

    pub fn key_down(&mut self, key: &str) -> bool {
        self.space();
        self.held.insert(key.to_owned());
        (self.send)(&format!("hid|key|down|{key}"))
    }

    pub fn key_up(&mut self, key: &str) -> bool {
        self.space();
        let ok = (self.send)(&format!("hid|key|up|{key}"));
        self.held.remove(key);
        ok
    }

    /// Tap `key`; the hold defaults to a human-like duration. An
    /// unconfirmed press still sends its key-up.
    pub fn press(&mut self, key: &str, hold: Option<f64>) -> bool {
        if !self.key_down(key) {
            self.key_up(key);
            return false;
        }
        let hold = hold.unwrap_or_else(|| human_hold(Some(key)));
        (self.sleep)(hold);
        self.key_up(key)
    }

    pub fn move_by(&mut self, dx: i32, dy: i32) -> bool {
        (self.send)(&format!("hid|move|{dx}|{dy}"))
    }

    pub fn click(&mut self, button: &str) -> bool {
        if !(self.send)(&format!("hid|mouse|down|{button}")) {
            return false;
        }
        (self.sleep)(human_hold(None));
        (self.send)(&format!("hid|mouse|up|{button}"))
    }

    pub fn scroll(&mut self, dy: i32) -> bool {
        (self.send)(&format!("hid|scroll|0|{dy}"))
    }

    pub fn held_keys(&self) -> impl Iterator<Item = &str> {
        self.held.iter().map(String::as_str)
    }

    /// Release every key this controller believes is held. Tracking is
    /// cleared even if a send fails.
    pub fn release_all(&mut self) {
        let keys: Vec<String> = std::mem::take(&mut self.held).into_iter().collect();
        for key in keys {
            self.space();
            (self.send)(&format!("hid|key|up|{key}"));
        }
    }
}

impl Drop for HidController {
    /// Whatever ends the controller's life — a stop, an error, a panic
    /// unwinding the bot thread — its keys come back up.
    fn drop(&mut self) {
        self.release_all();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    type Log<T> = Arc<Mutex<Vec<T>>>;

    /// A controller over a recording sender (payloads sent, sleeps taken);
    /// `fail` makes every send report failure.
    fn recorder(fail: bool) -> (HidController, Log<String>, Log<f64>) {
        let sent = Arc::new(Mutex::new(Vec::new()));
        let slept = Arc::new(Mutex::new(Vec::new()));
        let (s, z) = (sent.clone(), slept.clone());
        let hid = HidController::with(
            move |p| {
                s.lock().unwrap().push(p.to_owned());
                !fail
            },
            move |t| z.lock().unwrap().push(t),
            None,
            || 0.0,
        );
        (hid, sent, slept)
    }

    #[test]
    fn press_sends_down_then_up_with_the_hold() {
        let (mut hid, sent, slept) = recorder(false);
        assert!(hid.press("a", Some(0.1)));
        assert_eq!(*sent.lock().unwrap(), ["hid|key|down|a", "hid|key|up|a"]);
        assert_eq!(*slept.lock().unwrap(), [0.1]);
        assert_eq!(hid.held_keys().count(), 0);
    }

    #[test]
    fn unconfirmed_presses_are_still_released() {
        let (mut hid, sent, _) = recorder(true);
        assert!(!hid.key_down("a"));
        assert_eq!(hid.held_keys().collect::<Vec<_>>(), ["a"]);
        hid.release_all();
        assert_eq!(sent.lock().unwrap().last().unwrap(), "hid|key|up|a");
        assert!(!hid.press("b", None));
        assert_eq!(sent.lock().unwrap().last().unwrap(), "hid|key|up|b");
        assert_eq!(hid.held_keys().count(), 0);
    }

    #[test]
    fn dropping_the_controller_releases_held_keys() {
        let (mut hid, sent, _) = recorder(false);
        hid.key_down("left");
        hid.key_down("space");
        drop(hid);
        let sent = sent.lock().unwrap();
        assert!(sent.contains(&"hid|key|up|left".to_owned()));
        assert!(sent.contains(&"hid|key|up|space".to_owned()));
    }

    #[test]
    fn key_events_are_spaced_but_time_already_spent_counts() {
        let clock = Arc::new(Mutex::new(0.0f64));
        let slept = Arc::new(Mutex::new(Vec::new()));
        let (c, z, c2) = (clock.clone(), slept.clone(), clock.clone());
        let mut hid = HidController::with(
            |_| true,
            move |t| {
                z.lock().unwrap().push(t);
                *c2.lock().unwrap() += t;
            },
            Some(Box::new(|| 0.03)),
            move || *c.lock().unwrap(),
        );
        hid.key_down("a"); // first event: no wait
        hid.key_down("b"); // immediately after: waits the whole gap
        *clock.lock().unwrap() += 0.02;
        hid.key_down("c"); // 20ms already passed: waits the remaining 10
        let s = slept.lock().unwrap();
        assert_eq!(s.len(), 2);
        assert!((s[0] - 0.03).abs() < 1e-9 && (s[1] - 0.01).abs() < 1e-9);
    }

    #[test]
    fn mouse_payloads() {
        let (mut hid, sent, _) = recorder(false);
        hid.move_by(5, -3);
        hid.scroll(2);
        hid.click("left");
        assert_eq!(
            *sent.lock().unwrap(),
            [
                "hid|move|5|-3",
                "hid|scroll|0|2",
                "hid|mouse|down|left",
                "hid|mouse|up|left"
            ]
        );
    }
}
