//! The bot's body on the real machine: keys go to the Pico, eyes are a
//! screen grabber on the game window, time is the wall clock, and a stop
//! wakes every sleep. Also the thread that runs the state machine.

use std::cell::RefCell;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use picobot_core::bot::{Body, BotState, FlightRecorder, Keys, Machine, Mode, MoveMeasurer};
use picobot_core::config::BotConfig;
use picobot_core::lie_detector::find_lie_detector;
use picobot_core::maps::{MapEntry, PlayerRule};
use picobot_core::minimap::{MinimapAnalyzer, PlayerTracker, RegionSource};
use picobot_core::timing::{monotonic, new_session};
use picobot_core::vision::Image;
use picobot_io::hid::HidController;

use crate::evidence::{merged, pixel_stats, Evidence};
use crate::feed::Eyes;
use crate::frames::{annotate, Overlay};
use crate::host::Host;

/// The game window is checked for the lie detector this often (s).
const LIE_CHECK_S: f64 = 1.0;

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
    lost_dot: Evidence,
    off_platform: Evidence,
    rune_seen: Evidence,
    /// Solve attempts get their own throttle: one may follow a sighting
    /// within seconds.
    rune_attempt: Evidence,
    map: Option<(MapKey, Option<Arc<MapEntry>>)>,
    /// Bumped only when the resolved map's name changes.
    map_version: u64,
    map_name: Option<String>,
    viz_at: f64,
    minimap_warned: bool,
    cfg_version: u64,
    /// Other players tolerated on the current map (global or its rule).
    players_allowed: i64,
    /// When the window was last checked for the lie detector, and whether
    /// it showed then.
    lie_checked_at: f64,
    lie_seen: bool,
    lie_evidence: Evidence,
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
        let host_version = host.config_version();
        HostBody {
            host,
            cfg,
            state,
            hid,
            eyes,
            analyzer,
            stop,
            tracker: PlayerTracker::default(),
            lost_dot: Evidence::dot_lost(),
            off_platform: Evidence::off_platform(),
            rune_seen: Evidence::rune(),
            rune_attempt: Evidence::rune(),
            map: None,
            map_version: 0,
            map_name: None,
            viz_at: f64::NEG_INFINITY,
            minimap_warned: false,
            cfg_version: host_version,
            players_allowed: 0,
            lie_checked_at: f64::NEG_INFINITY,
            lie_seen: false,
            lie_evidence: Evidence::lie_detector(),
        }
    }

    /// Whether the lie detector's window shows: the whole window is
    /// checked at most once a second, the first sighting saved.
    fn lie_detector(&mut self) -> bool {
        let now = monotonic();
        if now - self.lie_checked_at < LIE_CHECK_S {
            return self.lie_seen;
        }
        self.lie_checked_at = now;
        let Some(win) = self.eyes.window_img() else {
            return self.lie_seen;
        };
        let found = find_lie_detector(&win);
        if let (Some(at), false) = (found, self.lie_seen) {
            let meta = self.with_context(serde_json::json!({ "title_at": [at.0, at.1] }));
            self.lie_evidence.save(&[("window", &win)], &meta);
        }
        self.lie_seen = found.is_some();
        self.lie_seen
    }

    /// The drawn platforms and learned ropes, for an evidence overlay.
    fn geometry_overlay(&mut self) -> Overlay {
        let ropes = match (self.map(), self.region_wh()) {
            (Some(e), Some((w, h))) => e
                .ropes
                .iter()
                .flatten()
                .map(|r| [r[0] * w, r[1] * h, r[2] * w, r[3] * h])
                .collect(),
            _ => Vec::new(),
        };
        Overlay {
            platforms: self.segments_px(),
            ropes,
            ..Default::default()
        }
    }

    /// `info` with the map and bot state added.
    fn with_context(&self, info: serde_json::Value) -> serde_json::Value {
        merged(
            info,
            serde_json::json!({
                "map": self.map_name,
                "bot_state": self.state.viz.state,
            }),
        )
    }

    /// Hand the dashboard what the bot sees, at most 20 times a second.
    fn publish(&mut self) {
        let now = monotonic();
        if now - self.viz_at >= 0.05 {
            self.viz_at = now;
            let mut session = self.state.session.snapshot(now);
            session["apm"] = self.attack_rate().round().into();
            self.state.viz.session = Some(session);
            self.state.viz.heat = self.state.heat.snapshot(now);
            self.host.set_bot_viz(Some(self.state.viz.clone()));
        }
    }

    /// Reinstall a title-verified map's remembered region, so its anchors
    /// and platforms line up with the layout they were drawn in (not when
    /// the region is pinned in config).
    fn apply_stored_layout(&mut self, entry: Option<&MapEntry>) {
        let Some([x, y, w, h]) = entry.and_then(|e| e.minimap_region) else {
            return;
        };
        if self.cfg.minimap_region.is_some() {
            return;
        }
        let region = (x as i32, y as i32, w as i32, h as i32);
        // A frame found on screen beats a remembered one: the stored layout
        // only fills in when nothing is located.
        let unlocated = matches!(
            self.analyzer.region_source(),
            None | Some(RegionSource::Stored)
        );
        if unlocated && self.analyzer.region() != Some(region) {
            self.analyzer.set_region(region, false);
            self.host
                .bus
                .emit("vision", &format!("layout restored: [{x}, {y}, {w}, {h}]"));
        }
    }

    /// Log the other-player count when it changes.
    fn note_others(&mut self, n: usize, ignored: bool) {
        if n == self.state.viz.others {
            return;
        }
        self.state.viz.others = n;
        let allowed = self.players_allowed;
        let s = if n == 1 { "" } else { "s" };
        let (msg, level) = match n {
            0 => ("minimap clear of other players".to_owned(), "info"),
            _ if ignored => (
                format!("{n} other player marker{s} on the minimap (ignored on this map)"),
                "debug",
            ),
            _ => (
                format!("{n} other player{s} on the minimap ({allowed} allowed)"),
                "warn",
            ),
        };
        self.host.bus.emit_level("safety", &msg, level);
    }

    fn refresh_map(&mut self) -> Option<Arc<MapEntry>> {
        let key = (self.host.identity.version(), self.host.map_edits());
        if let Some((k, e)) = &self.map {
            if *k == key {
                return e.clone();
            }
        }
        let entry = self.host.identity.entry().map(Arc::new);
        if self.host.identity.current().via == Some("ocr") {
            self.apply_stored_layout(entry.as_deref());
        }
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
        let before = self.tracker.last();
        let pos = self.analyzer.player_pos(img, &mut self.tracker);
        if pos.is_none() && !self.analyzer.loading() {
            let patrol = self.state.viz.patrol.as_ref();
            let meta = merged(
                pixel_stats(img, self.analyzer.colors.player),
                serde_json::json!({
                    "map": self.map_name,
                    "bot_state": self.state.viz.state,
                    "last_pos": before,
                    "marker_inset": self.analyzer.marker_inset,
                    "patrol_target": patrol.and_then(|p| p.target.clone()),
                    "patrol_move": patrol.and_then(|p| p.move_kind.clone()),
                }),
            );
            self.lost_dot.save(&[("frame", img)], &meta);
        }
        pos
    }

    fn off_platform(&mut self, pos: (f64, f64), info: serde_json::Value) {
        let Some(img) = self.frame() else { return };
        let mut o = self.geometry_overlay();
        o.player = Some(pos);
        if let Some(l) = info["last_leg"].as_object() {
            let pt = |k: &str, i: usize| l[k][i].as_f64().unwrap_or(0.0);
            let kind = l["kind"].as_str().unwrap_or("").to_owned();
            o.nav_route = vec![(kind, pt("from", 0), pt("from", 1), pt("to", 0), pt("to", 1))];
        }
        let mut overlay = img.clone();
        annotate(&mut overlay, &o);
        let meta = self.with_context(info);
        self.off_platform
            .save(&[("frame", &img), ("overlay", &overlay)], &meta);
    }

    fn window_frame(&mut self) -> Option<Image> {
        self.eyes.window_img()
    }

    fn locate_rune(&mut self, img: &Image) -> Option<picobot_core::rune::BoxPx> {
        if self.analyzer.loading() {
            return None;
        }
        self.analyzer.rune_box(img)
    }

    fn rune_event(&mut self, event: &str, info: serde_json::Value) {
        let Some(img) = self.frame() else { return };
        let mut o = self.geometry_overlay();
        o.player = self.state.viz.player;
        if let Some(b) = info["rune"].as_array() {
            let v = |i: usize| b.get(i).and_then(|v| v.as_f64()).unwrap_or(0.0);
            o.rune = Some(((v(0) + v(2)) / 2.0, (v(1) + v(3)) / 2.0));
        }
        let mut overlay = img.clone();
        annotate(&mut overlay, &o);
        let meta = self.with_context(merged(serde_json::json!({ "event": event }), info));
        if event == "attempt" || event == "unread" {
            // The puzzle is still up (the answer comes after a pause): keep
            // the window it was read from, to check a misread against.
            let window = self.eyes.window_img();
            let mut images = vec![("frame", &img), ("overlay", &overlay)];
            if let Some(w) = window.as_ref() {
                images.push(("window", w));
            }
            self.rune_attempt.save(&images, &meta);
        } else {
            self.rune_seen
                .save(&[("frame", &img), ("overlay", &overlay)], &meta);
        }
    }

    fn hazard_note(&self) -> Option<String> {
        (self.state.viz.hazard.as_deref() == Some("other players")).then(|| {
            format!(
                " ({} on the minimap, {} allowed)",
                self.state.viz.others, self.players_allowed
            )
        })
    }

    fn hazard_in(&mut self, img: &Image) -> Option<String> {
        let loading = self.analyzer.loading();
        let rule = self.refresh_map().and_then(|e| e.other_players);
        self.players_allowed = match rule {
            Some(PlayerRule::Ignore) => i64::MAX,
            Some(PlayerRule::Allow(n)) => n,
            None => self.cfg.allowed_other_players,
        };
        if !loading {
            let min_px = self.cfg.other_player_min_px.max(1) as usize;
            let n = self.analyzer.count_other_players(img, min_px);
            self.note_others(n, rule == Some(PlayerRule::Ignore));
        }
        let others = self.state.viz.others as i64;
        let lie = self.cfg.pause_on_lie_detector && self.lie_detector();
        let reason = if lie {
            Some("lie detector")
        } else if loading {
            Some("map transfer (loading screen)")
        } else if self.host.identity.identifying() {
            Some("identifying map")
        } else if self.cfg.stop_when_map_unrecognized && self.host.identity.unrecognized() {
            Some("unrecognized map")
        } else if self.cfg.stop_when_players_appear && others > self.players_allowed {
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
        let v = self.host.config_version();
        if v != self.cfg_version {
            self.cfg_version = v;
            self.cfg = self.host.bot_config();
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
        self.state.session.record(kind);
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
            let ended = match make_body(&h, s.clone()) {
                Ok(body) => run_bot(&h, body),
                Err(e) => Err(format!("Bot failed to start: {e}")),
            };
            match ended {
                Ok(msg) => h.bus.emit("notify", &msg),
                Err(e) => h.bus.emit("error", &e),
            }
            h.set_bot_viz(None);
            h.bus.emit("bot", "stopped");
            h.send_bot_state(false);
        })
        .map_err(|e| e.to_string())?;
    Ok(BotRun { stop, thread })
}

/// Run the machine to its end: the stop message with the run's summary,
/// or the panic as the error (keys released either way).
fn run_bot(h: &Arc<Host>, mut body: HostBody) -> Result<String, String> {
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
    let ran = catch_unwind(AssertUnwindSafe(|| Machine::default().run(&mut body)));
    body.log("Smart bot stopped");
    body.hid.release_all();
    if let Err(e) = body.state.reach.save(true) {
        h.bus.emit("error", &format!("saving reach failed: {e}"));
    }
    h.put_reach(body.state.reach.clone());
    let summary = body.state.session.summary(body.now());
    ran.map(|()| format!("Bot stopped — {summary}"))
        .map_err(|p| {
            let why = p
                .downcast_ref::<&str>()
                .map(|s| (*s).to_owned())
                .or_else(|| p.downcast_ref::<String>().cloned())
                .unwrap_or_else(|| "unknown".into());
            format!("Bot crashed: {why} — {summary}")
        })
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
            h.set_bot_viz(None);
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
