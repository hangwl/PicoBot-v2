//! The bot's body on the real machine: keys go to the Pico, eyes are a
//! screen grabber on the game window, time is the wall clock, and a stop
//! wakes every sleep. Also the thread that runs the state machine.

use std::cell::RefCell;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use picobot_core::bot::{Body, BotState, FlightRecorder, Keys, Machine, Mode, MoveMeasurer};
use picobot_core::config::BotConfig;
use picobot_core::maps::MapEntry;
use picobot_core::minimap::{MinimapAnalyzer, PlayerTracker};
use picobot_core::timing::{monotonic, new_session};
use picobot_core::vision::Image;
use picobot_io::hid::HidController;

use crate::feed::Eyes;
use crate::host::Host;

/// A stop that wakes sleepers at once.
#[derive(Default)]
pub struct Stop {
    flag: Mutex<bool>,
    cv: Condvar,
    fast: AtomicBool,
}

impl Stop {
    pub fn set(&self) {
        *self.flag.lock().unwrap() = true;
        self.fast.store(true, Ordering::Relaxed);
        self.cv.notify_all();
    }

    pub fn is_set(&self) -> bool {
        self.fast.load(Ordering::Relaxed)
    }

    /// Sleep up to `secs`; true when woken by the stop.
    pub fn wait(&self, secs: f64) -> bool {
        let guard = self.flag.lock().unwrap();
        let (guard, _) = self
            .cv
            .wait_timeout_while(guard, Duration::from_secs_f64(secs.max(0.0)), |stopped| {
                !*stopped
            })
            .unwrap();
        *guard
    }
}

pub fn epoch() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

/// (identity version, map edits): the bot's entry is current while it holds.
type MapKey = (u64, u64);

pub struct HostBody {
    pub host: Arc<Host>,
    pub cfg: BotConfig,
    pub state: BotState,
    pub hid: HidController,
    pub eyes: Eyes,
    pub analyzer: Arc<MinimapAnalyzer>,
    pub stop: Arc<Stop>,
    tracker: PlayerTracker,
    map: Option<(MapKey, Option<Arc<MapEntry>>)>,
    /// Bumped only when the resolved map's name changes.
    map_version: u64,
    map_name: Option<String>,
    viz_at: f64,
    minimap_warned: bool,
}

impl HostBody {
    pub fn new(
        host: Arc<Host>,
        cfg: BotConfig,
        state: BotState,
        hid: HidController,
        eyes: Eyes,
        analyzer: Arc<MinimapAnalyzer>,
        stop: Arc<Stop>,
    ) -> Self {
        HostBody {
            host,
            cfg,
            state,
            hid,
            eyes,
            analyzer,
            stop,
            tracker: PlayerTracker::default(),
            map: None,
            map_version: 0,
            map_name: None,
            viz_at: f64::NEG_INFINITY,
            minimap_warned: false,
        }
    }

    /// Hand the dashboard what the bot sees, at most 20 times a second.
    fn publish(&mut self) {
        let now = monotonic();
        if now - self.viz_at >= 0.05 {
            self.viz_at = now;
            self.host.set_bot_viz(Some(self.state.viz.clone()));
        }
    }

    fn refresh_map(&mut self) -> Option<Arc<MapEntry>> {
        let key = (self.host.identity.version(), self.host.map_edits());
        if let Some((k, e)) = &self.map {
            if *k == key {
                return e.clone();
            }
        }
        let entry = self.host.identity.entry().map(Arc::new);
        let name = entry.as_ref().map(|e| e.name.clone());
        if name != self.map_name {
            self.map_name = name;
            self.map_version += 1;
        }
        self.map = Some((key, entry.clone()));
        entry
    }
}

impl Body for HostBody {
    fn config(&self) -> &BotConfig {
        &self.cfg
    }
    fn state(&mut self) -> &mut BotState {
        &mut self.state
    }
    fn state_ref(&self) -> &BotState {
        &self.state
    }
    fn keys(&mut self) -> &mut dyn Keys {
        &mut self.hid
    }

    fn frame(&mut self) -> Option<Image> {
        let Some(region) = self.analyzer.region() else {
            if !self.minimap_warned && !self.analyzer.loading() {
                self.minimap_warned = true;
                self.log("Minimap not found — check the minimap is open and minimap_colors.border matches its frame.");
            }
            return None;
        };
        self.minimap_warned = false;
        self.eyes.capture(region)
    }

    fn locate_player(&mut self, img: &Image) -> Option<(i32, i32)> {
        self.analyzer.player_pos(img, &mut self.tracker)
    }

    fn hazard_in(&mut self, img: &Image) -> Option<String> {
        let reason = if self.analyzer.loading() {
            Some("map transfer (loading screen)")
        } else if self.cfg.stop_when_rune_appears && self.analyzer.rune_pos(img).is_some() {
            Some("rune")
        } else if self.cfg.stop_when_players_appear && self.analyzer.has_other_players(img) {
            Some("other players")
        } else {
            None
        };
        if let Some(r) = reason {
            if self.state.viz.hazard.as_deref() != Some(r) {
                self.host.bus.emit("safety", r);
            }
        }
        reason.map(str::to_owned)
    }

    fn focused(&mut self) -> bool {
        self.eyes.window.is_active()
    }

    fn sleep(&mut self, secs: f64) -> bool {
        self.publish();
        let woke = self.stop.wait(secs);
        if let Some(skills) = self.host.take_pending_skills() {
            self.state.pending_skills = Some(skills);
        }
        woke
    }

    fn stopped(&self) -> bool {
        self.stop.is_set()
    }

    fn now(&self) -> f64 {
        monotonic()
    }

    fn log(&mut self, msg: &str) {
        self.host.bus.emit("log", msg);
    }

    fn map(&mut self) -> Option<Arc<MapEntry>> {
        self.refresh_map()
    }

    fn region_wh(&self) -> Option<(f64, f64)> {
        self.analyzer.region().map(|r| (r.2 as f64, r.3 as f64))
    }

    fn map_version(&self) -> u64 {
        self.map_version
    }

    fn note_pos(&mut self, pos: (f64, f64)) {
        let entry = self.refresh_map();
        let wh = self.region_wh();
        self.host.observe_fit(entry.as_deref(), wh, pos);
    }

    fn save_map(&mut self, entry: MapEntry) {
        self.host.save_entry(entry);
    }

    fn stat(&mut self, kind: &str, anchor: &str, why: &str) {
        let map = self.refresh_map().map(|e| e.name.clone());
        self.host.anchor_stat(map.as_deref(), kind, anchor, why);
    }

    fn notify(&mut self, msg: &str) {
        self.log(msg);
        self.host.notify(msg);
    }
}

/// A running bot: its thread and its stop.
pub struct BotRun {
    pub stop: Arc<Stop>,
    pub thread: JoinHandle<()>,
}

impl BotRun {
    pub fn running(&self) -> bool {
        !self.thread.is_finished()
    }
}

/// The body the bot and the measurer share: keys over the serial link,
/// the host's analyzer, fresh eyes on the game window. Built on the
/// thread that uses it (a screen grabber stays on its thread).
fn make_body(host: &Arc<Host>, stop: Arc<Stop>) -> Result<HostBody, String> {
    let link = host
        .serial_link()
        .ok_or("no serial — pick a port in the Connection panel")?;
    let feed = host.get_feed().ok_or("game window not found")?;
    let eyes = Eyes::open(&host.window_title()).ok_or("game window not found")?;
    let cfg = host.bot_config();
    let bus = host.bus.clone();
    let s = stop.clone();
    let hid = HidController::with(
        move |payload| {
            bus.emit_level("hid", payload, "debug");
            link.send_acked(payload, Duration::from_secs_f64(1.5))
                .is_ok()
        },
        move |secs| {
            s.wait(secs);
        },
        Some(Box::new(picobot_core::timing::key_gap)),
        monotonic,
    );
    let state = BotState::new(&cfg, host.reach_clone(), None);
    Ok(HostBody::new(
        host.clone(),
        cfg,
        state,
        hid,
        eyes,
        feed.analyzer.clone(),
        stop,
    ))
}

/// What a run needs before it can start, checked on the caller's thread
/// so the dashboard hears at once.
fn preflight(host: &Arc<Host>) -> Result<(), String> {
    host.serial_link()
        .ok_or("no serial — pick a port in the Connection panel")?;
    host.get_feed().ok_or("game window not found")?;
    Ok(())
}

/// Start the bot thread.
pub fn spawn_bot(host: &Arc<Host>) -> Result<BotRun, String> {
    preflight(host)?;
    let stop = Arc::new(Stop::default());
    let s = stop.clone();
    let h = host.clone();
    let thread = std::thread::Builder::new()
        .name("SmartBot".into())
        .spawn(move || {
            match make_body(&h, s) {
                Ok(body) => run_bot(&h, body),
                Err(e) => h.bus.emit("error", &format!("smart bot failed: {e}")),
            }
            h.set_bot_viz(None);
            h.bus.emit("bot", "stopped");
            h.send_bot_state(false);
        })
        .map_err(|e| e.to_string())?;
    Ok(BotRun { stop, thread })
}

fn run_bot(h: &Arc<Host>, mut body: HostBody) {
    h.bus.emit("bot", "started");
    h.send_bot_state(true);
    new_session(); // this run's pace differs from the last
    if h.identity.current().title.is_none() && !h.identity.pending() {
        for n in h.identity.request(false) {
            h.bus.emit("map", &n);
        }
    }
    body.eyes.window.activate();
    body.sleep(1.0);
    Machine::default().run(&mut body);
    body.log("Smart bot stopped");
    body.hid.release_all();
    if let Err(e) = body.state.reach.save(true) {
        h.bus.emit("error", &format!("saving reach failed: {e}"));
    }
    h.put_reach(body.state.reach.clone());
}

/// The dot for the flight recorder: its thread keeps its own eyes.
fn dot_sampler(
    title: String,
    analyzer: Arc<MinimapAnalyzer>,
) -> Box<dyn FnMut() -> Option<(i32, i32)> + Send> {
    Box::new(move || {
        thread_local! {
            static EYES: RefCell<Option<(Eyes, PlayerTracker)>> = const { RefCell::new(None) };
        }
        EYES.with(|cell| {
            let mut cell = cell.borrow_mut();
            if cell.is_none() {
                *cell = Eyes::open(&title).map(|e| (e, PlayerTracker::default()));
            }
            let (eyes, tracker) = cell.as_mut()?;
            let img = eyes.capture(analyzer.region()?)?;
            analyzer.player_pos(&img, tracker)
        })
    })
}

/// Start a move measurement (`only`: one move of the plan).
pub fn spawn_measure(host: &Arc<Host>, mode: Mode, only: Option<String>) -> Result<BotRun, String> {
    preflight(host).map_err(|e| {
        if e.starts_with("game") {
            "measuring needs the game window".into()
        } else {
            e
        }
    })?;
    let stop = Arc::new(Stop::default());
    let s = stop.clone();
    let h = host.clone();
    let thread = std::thread::Builder::new()
        .name("MoveMeasurer".into())
        .spawn(move || {
            let mut body = match make_body(&h, s) {
                Ok(b) => b,
                Err(e) => {
                    h.bus.emit("error", &format!("measurement failed: {e}"));
                    return;
                }
            };
            let recorder = FlightRecorder::new(
                dot_sampler(h.window_title(), body.analyzer.clone()),
                1.0 / 60.0,
            );
            let bus = h.bus.clone();
            let hs = h.clone();
            let mut m = MoveMeasurer::new(
                Some(Box::new(recorder)),
                Box::new(move |kind, msg| {
                    bus.emit(kind, msg);
                }),
                Box::new(move |st| hs.send_measure(Some(st))),
            );
            m.run(&mut body, mode, only.as_deref());
            body.hid.release_all();
            h.put_reach(body.state.reach.clone());
        })
        .map_err(|e| e.to_string())?;
    Ok(BotRun { stop, thread })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_stop_wakes_a_sleeper_at_once() {
        let stop = Arc::new(Stop::default());
        let s = stop.clone();
        let t = std::thread::spawn(move || {
            let started = std::time::Instant::now();
            let woke = s.wait(5.0);
            (woke, started.elapsed())
        });
        std::thread::sleep(Duration::from_millis(30));
        stop.set();
        let (woke, took) = t.join().unwrap();
        assert!(woke && took < Duration::from_secs(1));
        assert!(stop.wait(1.0)); // stays set
        assert!(!Stop::default().wait(0.01));
    }
}
