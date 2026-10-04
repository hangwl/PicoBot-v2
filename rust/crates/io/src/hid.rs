//! Keyboard and mouse commands to the Pico, with held-key tracking.
//!
//! The firmware relays raw down/up events, so key-hold duration and the
//! spacing between events are decided here (human-like, see
//! `picobot_core::timing`). A key counts as held from the moment a press
//! is *attempted* — a press whose ACK timed out may still land — and
//! stays held until a key-up is confirmed; dropping the controller
//! releases everything it holds. With a lease, each key-down tells the
//! firmware to let go on its own unless renewed in time, so a hung
//! caller can't leave a key down.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use picobot_core::timing::{human_hold, key_gap_for, monotonic};

use crate::serial::SerialLink;

type SendFn = Box<dyn FnMut(&str) -> bool + Send>;
type SleepFn = Box<dyn FnMut(f64) + Send>;
/// The spacing before an event; told whether the same finger as the last one.
type GapFn = Box<dyn FnMut(bool) -> f64 + Send>;
type ClockFn = Box<dyn Fn() -> f64 + Send>;

/// Attempts at a key-up before giving up on it (a release is idempotent).
const UP_TRIES: usize = 3;
/// Firmware-side sweep: releases every key and button the Pico holds.
pub const RELEASE_ALL: &str = "hid|release_all";
/// Asks what the Pico holds: `ACK <seq> left|space|mouse:left`.
pub const HELD: &str = "hid|held";
/// Asks which key names the Pico's firmware knows, `|`-joined.
pub const KEYS: &str = "hid|keys";
/// How long the firmware keeps a bot-held key without a renewal (s).
pub const KEY_LEASE: f64 = 3.0;

pub struct HidController {
    send: SendFn,
    sleep: SleepFn,
    gap: Option<GapFn>,
    clock: ClockFn,
    /// Held keys, with when each was last sent down.
    held: BTreeMap<String, f64>,
    last_event: Option<f64>,
    last_key: Option<String>,
    lease: Option<f64>,
}

impl HidController {
    /// Real timing: blocking sleeps, human key gaps, the monotonic clock.
    pub fn new(send: impl FnMut(&str) -> bool + Send + 'static) -> Self {
        HidController::with(
            send,
            |s| std::thread::sleep(Duration::from_secs_f64(s.max(0.0))),
            Some(Box::new(|same| key_gap_for(same, 0.0))),
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
            held: BTreeMap::new(),
            last_event: None,
            last_key: None,
            lease: None,
        }
    }

    /// Key-downs carry a `lease` (s) that `renew` keeps alive.
    pub fn with_lease(mut self, lease: f64) -> Self {
        self.lease = Some(lease);
        self
    }

    /// The longest a caller should go between `renew` calls while keys
    /// are held (None without a lease); a key is due at half of it.
    pub fn renew_every(&self) -> Option<f64> {
        self.lease.map(|l| l / 3.0)
    }

    fn down_payload(&self, key: &str) -> String {
        match self.lease {
            Some(l) => format!("hid|key|down|{key}|{}", (l * 1000.0).round() as u64),
            None => format!("hid|key|down|{key}"),
        }
    }

    /// Re-send the down of every held key due for renewal. Not a finger
    /// event: unspaced, and the OS sees no change.
    pub fn renew(&mut self) {
        let Some(every) = self.renew_every() else {
            return;
        };
        let now = (self.clock)();
        let due: Vec<String> = self
            .held
            .iter()
            .filter(|(_, sent)| now - **sent >= every / 2.0)
            .map(|(k, _)| k.clone())
            .collect();
        for key in due {
            let payload = self.down_payload(&key);
            if (self.send)(&payload) {
                self.held.insert(key, (self.clock)());
            }
        }
    }

    /// Commands go over `link`, each waiting up to `timeout` for its ACK.
    pub fn for_link(link: Arc<SerialLink>, timeout: Duration) -> Self {
        HidController::new(move |payload| link.send_acked(payload, timeout).is_ok())
    }

    /// Fingers never land at once: consecutive key events are spaced by at
    /// least a drawn human gap. Time already spent (a deliberate sleep, the
    /// serial round trip) counts toward it, so timed sequences barely shift.
    fn space(&mut self, key: &str) {
        if let (Some(gap), Some(last)) = (self.gap.as_mut(), self.last_event) {
            let same = self.last_key.as_deref() == Some(key);
            let wait = gap(same) - ((self.clock)() - last);
            if wait > 0.0 {
                (self.sleep)(wait);
            }
        }
        self.last_event = Some((self.clock)());
        self.last_key = Some(key.to_owned());
    }

    pub fn key_down(&mut self, key: &str) -> bool {
        self.space(key);
        self.renew();
        self.held.insert(key.to_owned(), (self.clock)());
        let payload = self.down_payload(key);
        (self.send)(&payload)
    }

    /// A key stays tracked as held until its key-up is confirmed.
    pub fn key_up(&mut self, key: &str) -> bool {
        self.space(key);
        let ok = self.send_up(&format!("hid|key|up|{key}"));
        if ok {
            self.held.remove(key);
        }
        ok
    }

    fn send_up(&mut self, payload: &str) -> bool {
        (0..UP_TRIES).any(|_| (self.send)(payload))
    }

    /// Tap `key`; the hold defaults to a human-like duration. An
    /// unconfirmed press still sends its key-up.
    pub fn press(&mut self, key: &str, hold: Option<f64>) -> bool {
        if !self.key_down(key) {
            self.key_up(key);
            return false;
        }
        let hold = hold.unwrap_or_else(|| human_hold(Some(key)));
        self.hold(hold);
        self.key_up(key)
    }

    /// Sleep `secs`, renewing held keys along the way.
    pub fn hold(&mut self, secs: f64) {
        let slices = match self.renew_every() {
            Some(every) => (secs / every).ceil().max(1.0) as usize,
            None => 1,
        };
        for i in 0..slices {
            if i > 0 {
                self.renew();
            }
            (self.sleep)(secs / slices as f64);
        }
    }

    pub fn move_by(&mut self, dx: i32, dy: i32) -> bool {
        (self.send)(&format!("hid|move|{dx}|{dy}"))
    }

    /// An unconfirmed button press still sends its release.
    pub fn click(&mut self, button: &str) -> bool {
        let down = (self.send)(&format!("hid|mouse|down|{button}"));
        if down {
            (self.sleep)(human_hold(None));
        }
        let up = self.send_up(&format!("hid|mouse|up|{button}"));
        down && up
    }

    pub fn scroll(&mut self, dy: i32) -> bool {
        (self.send)(&format!("hid|scroll|0|{dy}"))
    }

    pub fn held_keys(&self) -> impl Iterator<Item = &str> {
        self.held.keys().map(String::as_str)
    }

    /// Release every key this controller believes is held; if any key-up
    /// goes unconfirmed, the firmware is asked to release everything.
    /// Tracking is cleared either way.
    pub fn release_all(&mut self) {
        let keys: Vec<String> = std::mem::take(&mut self.held).into_keys().collect();
        let mut all_up = true;
        for key in keys {
            self.space(&key);
            all_up &= self.send_up(&format!("hid|key|up|{key}"));
        }
        if !all_up {
            (self.send)(RELEASE_ALL);
        }
    }
}

/// The bot presses keys through this.
impl picobot_core::bot::Keys for HidController {
    fn key_down(&mut self, key: &str) -> bool {
        HidController::key_down(self, key)
    }
    fn key_up(&mut self, key: &str) -> bool {
        HidController::key_up(self, key)
    }
    fn press(&mut self, key: &str, hold: Option<f64>) -> bool {
        HidController::press(self, key, hold)
    }
    fn release_all(&mut self) {
        HidController::release_all(self)
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
        assert!(sent.lock().unwrap().contains(&"hid|key|up|a".to_owned()));
        assert!(!hid.press("b", None));
        assert_eq!(sent.lock().unwrap().last().unwrap(), "hid|key|up|b");
    }

    /// The first `fail` sends fail; the rest succeed.
    fn flaky(fail: usize) -> (HidController, Log<String>) {
        let sent = Arc::new(Mutex::new(Vec::new()));
        let left = Arc::new(Mutex::new(fail));
        let s = sent.clone();
        let hid = HidController::with(
            move |p| {
                s.lock().unwrap().push(p.to_owned());
                let mut n = left.lock().unwrap();
                let ok = *n == 0;
                *n = n.saturating_sub(1);
                ok
            },
            |_| {},
            None,
            || 0.0,
        );
        (hid, sent)
    }

    #[test]
    fn an_unconfirmed_key_up_is_retried() {
        let (mut hid, sent) = flaky(2);
        hid.held.insert("a".into(), 0.0);
        assert!(hid.key_up("a"));
        assert_eq!(*sent.lock().unwrap(), ["hid|key|up|a"; 3]);
        assert_eq!(hid.held_keys().count(), 0);
    }

    #[test]
    fn a_key_up_that_never_lands_keeps_the_key_held() {
        let (mut hid, sent, _) = recorder(true);
        hid.held.insert("a".into(), 0.0);
        assert!(!hid.key_up("a"));
        assert_eq!(sent.lock().unwrap().len(), UP_TRIES);
        assert_eq!(hid.held_keys().collect::<Vec<_>>(), ["a"]);
        hid.release_all();
        assert_eq!(sent.lock().unwrap().last().unwrap(), RELEASE_ALL);
        assert_eq!(hid.held_keys().count(), 0);
    }

    #[test]
    fn a_clean_release_needs_no_firmware_sweep() {
        let (mut hid, sent, _) = recorder(false);
        hid.key_down("a");
        hid.release_all();
        assert!(!sent.lock().unwrap().contains(&RELEASE_ALL.to_owned()));
    }

    #[test]
    fn an_unconfirmed_click_still_releases_the_button() {
        let (mut hid, sent, slept) = recorder(true);
        assert!(!hid.click("left"));
        let sent = sent.lock().unwrap();
        assert_eq!(sent[0], "hid|mouse|down|left");
        assert!(sent[1..].iter().all(|p| p == "hid|mouse|up|left"));
        assert!(slept.lock().unwrap().is_empty());
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
            Some(Box::new(|_| 0.03)),
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
    fn the_gap_is_told_whether_the_same_finger_follows() {
        let told = Arc::new(Mutex::new(Vec::new()));
        let t = told.clone();
        let mut hid = HidController::with(
            |_| true,
            |_| {},
            Some(Box::new(move |same| {
                t.lock().unwrap().push(same);
                0.0
            })),
            || 0.0,
        );
        hid.key_down("a"); // first event: nothing to space from
        hid.key_up("a"); // same key
        hid.key_down("b"); // another finger
        hid.key_down("b"); // the same again
        assert_eq!(*told.lock().unwrap(), [true, false, true]);
    }

    #[test]
    fn a_gap_below_the_round_trip_is_waited_out_by_the_round_trip() {
        // A chord gap shorter than the time a send already took adds nothing.
        let clock = Arc::new(Mutex::new(0.0f64));
        let slept = Arc::new(Mutex::new(Vec::new()));
        let (c, z, c2, c3) = (clock.clone(), slept.clone(), clock.clone(), clock.clone());
        let mut hid = HidController::with(
            move |_| {
                *c3.lock().unwrap() += 0.0045; // the ACK round trip
                true
            },
            move |t| {
                z.lock().unwrap().push(t);
                *c2.lock().unwrap() += t;
            },
            Some(Box::new(|same| if same { 0.03 } else { 0.006 })),
            move || *c.lock().unwrap(),
        );
        hid.key_down("a");
        hid.key_down("b");
        let s = slept.lock().unwrap();
        assert_eq!(s.len(), 1);
        assert!((s[0] - 0.0015).abs() < 1e-9, "{s:?}");
    }

    /// A leased controller over a recording sender and a hand-set clock.
    fn leased() -> (HidController, Log<String>, Arc<Mutex<f64>>) {
        let sent = Arc::new(Mutex::new(Vec::new()));
        let clock = Arc::new(Mutex::new(0.0f64));
        let (s, c, c2) = (sent.clone(), clock.clone(), clock.clone());
        let hid = HidController::with(
            move |p| {
                s.lock().unwrap().push(p.to_owned());
                true
            },
            move |t| *c2.lock().unwrap() += t,
            None,
            move || *c.lock().unwrap(),
        )
        .with_lease(3.0);
        (hid, sent, clock)
    }

    #[test]
    fn leased_downs_carry_the_lease_and_ups_do_not() {
        let (mut hid, sent, _) = leased();
        hid.key_down("left");
        hid.key_up("left");
        assert_eq!(
            *sent.lock().unwrap(),
            ["hid|key|down|left|3000", "hid|key|up|left"]
        );
    }

    #[test]
    fn only_keys_due_are_renewed() {
        let (mut hid, sent, clock) = leased();
        hid.key_down("left");
        *clock.lock().unwrap() = 0.3;
        hid.key_down("space");
        hid.renew();
        assert_eq!(sent.lock().unwrap().len(), 2, "nothing due yet");
        *clock.lock().unwrap() = 0.6;
        hid.renew();
        assert_eq!(sent.lock().unwrap()[2], "hid|key|down|left|3000");
        assert_eq!(sent.lock().unwrap().len(), 3, "space was sent 0.3s ago");
    }

    #[test]
    fn a_long_hold_is_renewed_through() {
        let (mut hid, sent, clock) = leased();
        assert!(hid.press("x", Some(3.5)));
        let sent = sent.lock().unwrap();
        let downs = sent.iter().filter(|p| *p == "hid|key|down|x|3000").count();
        assert_eq!(downs, 4, "the press and a renewal every ~0.9s: {sent:?}");
        assert_eq!(sent.last().unwrap(), "hid|key|up|x");
        assert!((*clock.lock().unwrap() - 3.5).abs() < 1e-9);
    }

    #[test]
    fn without_a_lease_nothing_is_renewed() {
        let (mut hid, sent, _) = recorder(false);
        hid.key_down("a");
        hid.renew();
        hid.hold(10.0);
        assert_eq!(*sent.lock().unwrap(), ["hid|key|down|a"]);
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
