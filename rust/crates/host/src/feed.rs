//! The vision feed: the minimap analyzer everyone shares, and the
//! `MapMonitor` thread — the one place that watches for map changes.
//!
//! The monitor samples the minimap at 20 Hz with its own screen grabber
//! (captures are per-thread), feeds the blackout detector, relocates a
//! moved panel, and asks identity for title reads on arrival.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Sender;
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::Duration;

use picobot_core::config::BotConfig;
use picobot_core::identity::MapIdentity;
use picobot_core::minimap::MinimapAnalyzer;
use picobot_core::timing::monotonic;
use picobot_core::title::name_strip_region;
use picobot_core::vision::{Image, Region};
use picobot_io::capture::ScreenGrabber;
use picobot_io::window::GameWindow;

use crate::bus::Bus;

/// A thread's own view of the game: its window handle and grabber.
pub struct Eyes {
    pub window: GameWindow,
    grabber: ScreenGrabber,
}

impl Eyes {
    pub fn open(title: &str) -> Option<Eyes> {
        Some(Eyes {
            window: GameWindow::find(title)?,
            grabber: ScreenGrabber::new(),
        })
    }

    /// The whole client area.
    pub fn window_img(&mut self) -> Option<Image> {
        let (l, t, r, b) = self.window.client_rect()?;
        self.grabber.capture(l, t, r - l, b - t)
    }

    /// A client-area rectangle.
    pub fn capture(&mut self, rect: Region) -> Option<Image> {
        let (l, t, _, _) = self.window.client_rect()?;
        let (x, y, w, h) = rect;
        if w <= 0 || h <= 0 {
            return None;
        }
        self.grabber.capture(l + x, t + y, w, h)
    }
}

/// The title band above the minimap (client-area px).
pub fn name_region(cfg: &BotConfig, minimap: Option<Region>) -> Option<Region> {
    if let Some([x, y, w, h]) = cfg.minimap_name_region {
        return Some((x as i32, y as i32, w as i32, h as i32));
    }
    Some(name_strip_region(minimap?, cfg.name_scan_px as i32, 4))
}

pub struct Feed {
    pub analyzer: Arc<MinimapAnalyzer>,
    stop: Arc<AtomicBool>,
    monitor: Option<JoinHandle<()>>,
}

impl Feed {
    /// None when the game window can't be found.
    pub fn start(
        title: &str,
        cfg: &BotConfig,
        identity: Arc<MapIdentity>,
        bus: Arc<Bus>,
        ocr: Option<Sender<TitleJob>>,
    ) -> Option<Feed> {
        GameWindow::find(title)?;
        let region = cfg
            .minimap_region
            .map(|[x, y, w, h]| (x as i32, y as i32, w as i32, h as i32));
        let analyzer = Arc::new(MinimapAnalyzer::new(
            cfg.minimap_colors,
            region,
            cfg.marker_inset_px as usize,
        ));
        let stop = Arc::new(AtomicBool::new(false));
        let mut mon = Monitor {
            title: title.to_owned(),
            analyzer: analyzer.clone(),
            identity,
            bus,
            ocr,
            cfg: cfg.clone(),
            stop: stop.clone(),
            last_locate: f64::NEG_INFINITY,
            was_loading: false,
            last_verify: monotonic(),
            last_arrival: f64::NEG_INFINITY,
            last_title: None,
        };
        let monitor = std::thread::Builder::new()
            .name("MapMonitor".into())
            .spawn(move || mon.run())
            .ok()?;
        Some(Feed {
            analyzer,
            stop,
            monitor: Some(monitor),
        })
    }
}

impl Drop for Feed {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(h) = self.monitor.take() {
            let _ = h.join();
        }
    }
}

/// A title band to read, for identity read generation `gen`.
pub struct TitleJob {
    pub band: Image,
    pub gen: u64,
}

struct Monitor {
    ocr: Option<Sender<TitleJob>>,
    cfg: BotConfig,
    title: String,
    analyzer: Arc<MinimapAnalyzer>,
    identity: Arc<MapIdentity>,
    bus: Arc<Bus>,
    stop: Arc<AtomicBool>,
    last_locate: f64,
    was_loading: bool,
    last_verify: f64,
    last_arrival: f64,
    last_title: Option<String>,
}

const PERIOD: f64 = 0.05;
const LOCATE_EVERY: f64 = 0.25;
/// The title is re-read this often: a map change with no blackout (or one
/// too short to see) still shows up in it.
const VERIFY_EVERY: f64 = 5.0;

impl Monitor {
    fn run(&mut self) {
        let mut eyes: Option<Eyes> = None;
        while !self.stop.load(Ordering::Relaxed) {
            let started = monotonic();
            if eyes.is_none() {
                eyes = Eyes::open(&self.title);
            }
            if let Some(e) = eyes.as_mut() {
                self.tick(e);
            }
            let rest = (PERIOD - (monotonic() - started)).max(0.005);
            std::thread::sleep(Duration::from_secs_f64(rest));
        }
    }

    fn emit(&self, notes: Vec<String>) {
        for n in notes {
            self.bus.emit("map", &n);
        }
    }

    fn tick(&mut self, eyes: &mut Eyes) {
        let mm = &self.analyzer;
        let now = monotonic();
        let region = match mm.region() {
            Some(r) => r,
            None => {
                if now - self.last_locate < LOCATE_EVERY {
                    return;
                }
                self.last_locate = now;
                let Some(win) = eyes.window_img() else { return };
                let Some(r) = mm.locate(&win) else { return };
                r
            }
        };
        let Some(img) = eyes.capture(region) else {
            return;
        };
        let arrived = mm.note_frame(&img, now);
        let loading = mm.loading();
        if loading && !self.was_loading {
            self.bus.emit("vision", "map transfer — loading screen");
        }
        self.was_loading = loading;
        if arrived {
            self.last_arrival = now;
            self.bus
                .emit("vision", "arrived on a new map — re-detecting minimap");
            self.emit(self.identity.request(true));
        } else if mm.edge_lost(now) {
            let moved = eyes.window_img().is_some_and(|w| mm.relocate(&w, now));
            if moved {
                let r = mm.region().unwrap_or_default();
                self.bus.emit(
                    "vision",
                    &format!("minimap panel moved: [{}, {}, {}, {}]", r.0, r.1, r.2, r.3),
                );
                if self.identity.current().title.is_none() {
                    self.emit(self.identity.request(false));
                }
            }
        }
        if !loading && now - self.last_verify >= VERIFY_EVERY && !self.identity.pending() {
            self.last_verify = now;
            self.emit(self.identity.request(false));
        }
        let title = self.identity.current().title;
        if title != self.last_title {
            if title.is_some() && self.last_title.is_some() && now - self.last_arrival > 5.0 {
                self.bus.emit(
                    "vision",
                    "the map title changed with no loading screen — a transfer was missed",
                );
            }
            self.last_title = title;
        }
        if !loading && self.identity.wants_band(now) {
            if let Some(tx) = &self.ocr {
                let band = name_region(&self.cfg, mm.region()).and_then(|r| eyes.capture(r));
                // Claim the read only once a band was captured.
                if let Some(band) = band {
                    if let Some(gen) = self.identity.begin_read() {
                        let _ = tx.send(TitleJob { band, gen });
                    }
                }
            }
        }
    }
}
