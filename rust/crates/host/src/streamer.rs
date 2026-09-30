//! The `FrameStreamer` thread: while a client is subscribed, capture the
//! chosen view at the view rate, draw the overlays, JPEG it and hand the
//! frame to the clients. Captures failing for 5s straight report a stall
//! (the host rebuilds the vision feed).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::Duration;

use picobot_core::minimap::PlayerTracker;
use picobot_core::timing::monotonic;
use picobot_core::vision::Image;
use serde_json::{json, Map, Value};

use crate::feed::{name_region, Eyes};
use crate::frames::{annotate, annotate_title, assemble_panel, encode_jpeg, pack_frame, Overlay};
use crate::host::Host;

pub const STALL_AFTER: f64 = 5.0;
const QUALITY: u8 = 70;

/// What one tick produced.
#[allow(clippy::large_enum_variant)] // one per tick, never stored
pub enum Shot {
    Frame {
        img: Image,
        overlay: Overlay,
        meta: Map<String, Value>,
    },
    /// Nothing to show yet (no panel located): not a failure.
    Idle,
    Fail(&'static str),
}

pub struct Streamer {
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Streamer {
    pub fn start(host: Arc<Host>) -> Streamer {
        let stop = Arc::new(AtomicBool::new(false));
        let s = stop.clone();
        let thread = std::thread::Builder::new()
            .name("FrameStreamer".into())
            .spawn(move || run(host, s))
            .expect("spawn the frame streamer");
        Streamer {
            stop,
            thread: Some(thread),
        }
    }
}

impl Drop for Streamer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

fn run(host: Arc<Host>, stop: Arc<AtomicBool>) {
    let mut eyes: Option<Eyes> = None;
    let mut tracker = PlayerTracker::default();
    let mut failing_since: Option<f64> = None;
    while !stop.load(Ordering::Relaxed) {
        let started = monotonic();
        if host.clients.has_frame_clients() {
            let title = host.window_title();
            if eyes.as_ref().is_some_and(|e| e.window.title != title) || host.take_eyes_reset() {
                eyes = None;
            }
            if eyes.is_none() {
                eyes = Eyes::open(&title);
            }
            let mode = host.view_mode();
            let shot = match eyes.as_mut() {
                Some(e) => provide(&host, &mode, e, &mut tracker),
                None => Shot::Fail("game window not found"),
            };
            match shot {
                Shot::Frame { img, overlay, meta } => {
                    failing_since = None;
                    send(&host, &mode, img, &overlay, meta);
                }
                Shot::Idle => failing_since = None,
                Shot::Fail(why) => {
                    let now = monotonic();
                    match failing_since {
                        None => failing_since = Some(now),
                        Some(t) if now - t >= STALL_AFTER => {
                            failing_since = Some(now);
                            eyes = None;
                            host.stream_stalled(why);
                        }
                        Some(_) => {}
                    }
                }
            }
        } else {
            failing_since = None;
        }
        let interval = 1.0 / host.view_fps().max(1.0);
        let rest = (interval - (monotonic() - started)).max(0.05);
        std::thread::sleep(Duration::from_secs_f64(rest));
    }
}

fn send(host: &Host, mode: &str, mut img: Image, overlay: &Overlay, extra: Map<String, Value>) {
    annotate(&mut img, overlay);
    let Some(jpeg) = encode_jpeg(&img, QUALITY) else {
        return;
    };
    let mut meta = Map::new();
    meta.insert("event".into(), "frame".into());
    meta.insert("mode".into(), mode.into());
    meta.insert("w".into(), img.width.into());
    meta.insert("h".into(), img.height.into());
    for key in [
        "state",
        "map",
        "map_via",
        "map_conf",
        "map_title",
        "hazard",
        "player",
        "summons",
        "patrol",
        "layout",
        "no_rotation",
        "ox",
        "oy",
    ] {
        if let Some(v) = extra.get(key).filter(|v| !v.is_null()) {
            meta.insert(key.into(), v.clone());
        }
    }
    let frame = Arc::new(pack_frame(&Value::Object(meta), &jpeg));
    for peer in host.clients.broadcast_frame(frame) {
        host.bus
            .emit("remote", &format!("WS: frame send stuck — closing {peer}"));
    }
}

/// One capture of the current view with its overlay and metadata.
fn provide(host: &Host, mode: &str, eyes: &mut Eyes, tracker: &mut PlayerTracker) -> Shot {
    let Some(feed) = host.get_feed() else {
        return Shot::Fail("vision feed unavailable");
    };
    let mm = &feed.analyzer;
    let region = mm.region();
    let cfg = host.bot_config();
    let (mut overlay, mut meta) = host.map_meta(region);
    meta.insert("state".into(), "IDLE".into());
    meta.insert(
        "layout".into(),
        mm.region_source().map(|s| s.as_str()).into(),
    );
    match mode {
        "title" => {
            let Some(band) = name_region(&cfg, region).and_then(|r| eyes.capture(r)) else {
                return Shot::Idle;
            };
            overlay.clear_minimap();
            Shot::Frame {
                img: annotate_title(&band),
                overlay,
                meta,
            }
        }
        "window" => {
            let Some(img) = eyes.window_img() else {
                return Shot::Fail("window capture failed");
            };
            match region {
                Some(r) => overlay.shift(r.0 as f64, r.1 as f64),
                None => overlay.clear_minimap(),
            }
            Shot::Frame { img, overlay, meta }
        }
        _ => {
            let Some(region) = region else {
                return Shot::Idle;
            };
            let Some(img) = eyes.capture(region) else {
                return Shot::Idle;
            };
            if let Some(p) = mm.player_pos(&img, tracker) {
                overlay.player = Some((p.0 as f64, p.1 as f64));
                overlay.player_box = Some(mm.player_box(p));
                meta.insert("player".into(), json!([p.0, p.1]));
            }
            let band_rect = name_region(&cfg, Some(region));
            let band = band_rect.and_then(|r| eyes.capture(r));
            let band_xy = band_rect.map_or((0, 0), |r| (r.0, r.1));
            let (panel, dx, dy) = assemble_panel(band.as_ref(), &img, region, band_xy);
            overlay.shift(dx as f64, dy as f64);
            if let Some(p) = meta.get_mut("player") {
                *p = json!([
                    p[0].as_i64().unwrap_or(0) + dx,
                    p[1].as_i64().unwrap_or(0) + dy
                ]);
            }
            meta.insert("ox".into(), dx.into());
            meta.insert("oy".into(), dy.into());
            Shot::Frame {
                img: panel,
                overlay,
                meta,
            }
        }
    }
}
