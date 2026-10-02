//! The minimap panel: where it is, what's on it, and when the map changes.
//!
//! One [`MinimapAnalyzer`] is shared (behind an `Arc`) by the map monitor,
//! the bot and the dashboard streamer, so its region and transition state
//! sit behind a mutex. Dot tracking is per reader: each thread keeps its own
//! [`PlayerTracker`] (in Python this was hidden thread-local state).

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

use crate::config::MinimapColors;
use crate::vision::{color_mask, find_frame, is_dark, marker_blobs, Image, Region};

/// Map transfers black the whole client out for about a second — the only
/// map-change trigger (translucent UI defeats pixel-change checks).
/// `normal` → `dark` (after `min_dark_s`) → `settling` → `normal` (after
/// `settle_s` of visible frames).
#[derive(Debug, Clone)]
pub struct TransitionDetector {
    pub level: f64,
    pub min_dark_s: f64,
    pub settle_s: f64,
    pub state: TransitionState,
    dark_since: Option<f64>,
    settle_since: Option<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransitionState {
    Normal,
    Dark,
    Settling,
}

impl TransitionState {
    pub fn as_str(self) -> &'static str {
        match self {
            TransitionState::Normal => "normal",
            TransitionState::Dark => "dark",
            TransitionState::Settling => "settling",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransitionEvent {
    /// A blackout was confirmed.
    Loading,
    /// The new map has been visible for `settle_s`.
    Arrived,
}

impl Default for TransitionDetector {
    fn default() -> Self {
        TransitionDetector {
            level: 12.0,
            min_dark_s: 0.15,
            settle_s: 0.6,
            state: TransitionState::Normal,
            dark_since: None,
            settle_since: None,
        }
    }
}

impl TransitionDetector {
    pub fn loading(&self) -> bool {
        self.state != TransitionState::Normal
    }

    pub fn reset(&mut self) {
        self.state = TransitionState::Normal;
        self.dark_since = None;
        self.settle_since = None;
    }

    pub fn note(&mut self, img: &Image, now: f64) -> Option<TransitionEvent> {
        use TransitionState::*;
        if is_dark(img, self.level) {
            match self.state {
                Settling => self.state = Dark,
                Normal => {
                    let since = *self.dark_since.get_or_insert(now);
                    if now - since >= self.min_dark_s {
                        self.state = Dark;
                        return Some(TransitionEvent::Loading);
                    }
                }
                Dark => {}
            }
            return None;
        }
        self.dark_since = None;
        if self.state == Dark {
            self.state = Settling;
            self.settle_since = Some(now);
        }
        if self.state == Settling && self.settle_since.is_some_and(|t| now - t >= self.settle_s) {
            self.state = Normal;
            return Some(TransitionEvent::Arrived);
        }
        None
    }
}

/// Where the panel region came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RegionSource {
    /// Pinned in config.json.
    Config,
    /// Drawn by hand.
    Manual,
    /// A map file's remembered layout.
    Stored,
    /// Frame detection.
    Auto,
}

impl RegionSource {
    pub fn as_str(self) -> &'static str {
        match self {
            RegionSource::Config => "config",
            RegionSource::Manual => "manual",
            RegionSource::Stored => "stored",
            RegionSource::Auto => "auto",
        }
    }
}

/// One reader's view of the player dot: the last position (the nearest
/// candidate to it wins) and how many reads in a row missed.
#[derive(Debug, Clone, Default)]
pub struct PlayerTracker {
    epoch: u64,
    last: Option<(i32, i32)>,
    misses: u32,
    /// The last real sighting, kept after the dot is lost.
    seen: Option<(i32, i32)>,
}

impl PlayerTracker {
    /// The last position read, if the dot hasn't been lost since.
    pub fn last(&self) -> Option<(i32, i32)> {
        self.last
    }
}

/// An inclusive pixel box: `(x0, y0, x1, y1)`.
pub type BoxPx = (i32, i32, i32, i32);

struct State {
    region: Option<Region>,
    source: Option<RegionSource>,
    transition: TransitionDetector,
    edge_lost_since: Option<f64>,
    /// A live frame that differs from the region, and how many checks in a
    /// row have seen it.
    pending: Option<(Region, u32)>,
    /// (feet, bbox) of the last detected dot, for the overlay.
    last_box: Option<((i32, i32), BoxPx)>,
}

pub struct MinimapAnalyzer {
    pub colors: MinimapColors,
    pub marker_inset: usize,
    pub border_tolerance: i32,
    /// How long the frame edge may be missing before a relocation.
    pub edge_lost_s: f64,
    state: Mutex<State>,
    /// Bumped when trackers must forget the dot (new map, region change).
    epoch: AtomicU64,
}

/// Smallest group of matching pixels accepted as a marker (the player dot
/// is ~24px; same-coloured specks are 1–3px).
pub const MARKER_MIN_PX: usize = 6;
/// A lost dot is held this many consecutive reads (one flicker).
pub const PLAYER_HOLD_FRAMES: u32 = 1;
/// How far (px) from its last sighting a dot in the frame rim is still
/// taken for the player.
pub const RIM_REACH: i32 = 12;
/// The client's player glyph (6x6).
pub const DOT_W: i32 = 6;
pub const DOT_H: i32 = 6;

impl MinimapAnalyzer {
    /// `region` pins the panel (config); None finds it automatically.
    pub fn new(colors: MinimapColors, region: Option<Region>, marker_inset: usize) -> Self {
        MinimapAnalyzer {
            colors,
            marker_inset,
            border_tolerance: 10,
            edge_lost_s: 1.0,
            state: Mutex::new(State {
                region,
                source: region.map(|_| RegionSource::Config),
                transition: TransitionDetector::default(),
                edge_lost_since: None,
                pending: None,
                last_box: None,
            }),
            epoch: AtomicU64::new(0),
        }
    }

    fn bump(&self) {
        self.epoch.fetch_add(1, Ordering::Relaxed);
    }

    pub fn region(&self) -> Option<Region> {
        self.state.lock().unwrap().region
    }

    pub fn region_source(&self) -> Option<RegionSource> {
        self.state.lock().unwrap().source
    }

    /// True from a confirmed blackout until the new map settles.
    pub fn loading(&self) -> bool {
        self.state.lock().unwrap().transition.loading()
    }

    /// Install a known region: a map's stored layout, or a hand-drawn one.
    pub fn set_region(&self, region: Region, manual: bool) {
        let mut s = self.state.lock().unwrap();
        s.region = Some(region);
        s.source = Some(if manual {
            RegionSource::Manual
        } else {
            RegionSource::Stored
        });
        drop(s);
        self.bump();
    }

    /// Drop the region (any source); the next `locate` re-detects.
    pub fn reset_region(&self) {
        let mut s = self.state.lock().unwrap();
        s.region = None;
        s.source = None;
        drop(s);
        self.bump();
    }

    /// Feed one minimap capture (map monitor only). True when arrival at a
    /// new map is confirmed; a non-config region is then dropped so the
    /// (per-map sized) panel is found again.
    pub fn note_frame(&self, img: &Image, now: f64) -> bool {
        let mut s = self.state.lock().unwrap();
        let event = s.transition.note(img, now);
        let arrived = event == Some(TransitionEvent::Arrived);
        if arrived && s.source != Some(RegionSource::Config) {
            s.region = None;
            s.source = None;
        }
        let dark = is_dark(img, s.transition.level);
        let watch = matches!(s.source, Some(RegionSource::Auto | RegionSource::Stored));
        if !watch || dark || s.transition.loading() || img.height < 2 {
            s.edge_lost_since = None;
        } else {
            // Is the frame's top edge still where the region says?
            let top = img.crop(0, 0, img.width, 2);
            let m = color_mask(&top, self.colors.border, self.border_tolerance);
            let best_row = (0..m.height)
                .map(|y| {
                    (0..m.width).filter(|&x| m.get(x, y)).count() as f64 / m.width.max(1) as f64
                })
                .fold(0.0, f64::max);
            if best_row >= 0.6 {
                s.edge_lost_since = None;
            } else if s.edge_lost_since.is_none() {
                s.edge_lost_since = Some(now);
            }
        }
        drop(s);
        if arrived {
            self.bump(); // new map: forget the dot
        }
        arrived
    }

    pub fn transition_state(&self) -> TransitionState {
        self.state.lock().unwrap().transition.state
    }

    /// The frame edge has been missing for a while: the panel moved or was
    /// resized.
    pub fn edge_lost(&self, now: f64) -> bool {
        self.state
            .lock()
            .unwrap()
            .edge_lost_since
            .is_some_and(|t| now - t >= self.edge_lost_s)
    }

    /// Check the whole frame against the live window: every edge, not just
    /// the top. A different frame seen on `CONFIRM_CHECKS` checks in a row
    /// replaces the region (source `Auto`); returns `(old, new)` then.
    /// A pinned or hand-drawn region, no region, or no frame found leaves
    /// things alone.
    pub fn verify_region(&self, window: &Image) -> Option<(Region, Region)> {
        const CONFIRM_CHECKS: u32 = 2;
        let near = |a: Region, b: Region| {
            (a.0 - b.0).abs() <= 1
                && (a.1 - b.1).abs() <= 1
                && (a.2 - b.2).abs() <= 1
                && (a.3 - b.3).abs() <= 1
        };
        let (region, source) = {
            let s = self.state.lock().unwrap();
            (s.region?, s.source?)
        };
        if matches!(source, RegionSource::Config | RegionSource::Manual) {
            return None;
        }
        let found = find_frame(window, self.colors.border, self.border_tolerance);
        let mut s = self.state.lock().unwrap();
        let Some(found) = found.filter(|f| !near(*f, region)) else {
            s.pending = None;
            return None;
        };
        let seen = match s.pending {
            Some((p, n)) if near(p, found) => n + 1,
            _ => 1,
        };
        if seen < CONFIRM_CHECKS {
            s.pending = Some((found, seen));
            return None;
        }
        s.pending = None;
        s.region = Some(found);
        s.source = Some(RegionSource::Auto);
        s.edge_lost_since = None;
        drop(s);
        self.bump();
        Some((region, found))
    }

    /// Re-detect after `edge_lost`; switches only when a frame is actually
    /// found elsewhere. True when the region changed.
    pub fn relocate(&self, window: &Image, now: f64) -> bool {
        let found = find_frame(window, self.colors.border, self.border_tolerance);
        let mut s = self.state.lock().unwrap();
        s.edge_lost_since = Some(now);
        match found {
            Some(r) if Some(r) != s.region => {
                s.region = Some(r);
                s.source = Some(RegionSource::Auto);
                s.edge_lost_since = None;
                drop(s);
                self.bump();
                true
            }
            _ => false,
        }
    }

    /// The cached region, else detect the panel frame in `window`.
    pub fn locate(&self, window: &Image) -> Option<Region> {
        if let Some(r) = self.region() {
            return Some(r);
        }
        let r = find_frame(window, self.colors.border, self.border_tolerance)?;
        let mut s = self.state.lock().unwrap();
        s.region = Some(r);
        s.source = Some(RegionSource::Auto);
        Some(r)
    }

    /// The panel's image cut out of a full window capture.
    pub fn crop(&self, window: &Image) -> Option<Image> {
        let (x, y, w, h) = self.locate(window)?;
        Some(window.crop(
            x.max(0) as usize,
            y.max(0) as usize,
            w.max(0) as usize,
            h.max(0) as usize,
        ))
    }

    /// The image minus `marker_inset` px of frame on each edge, and the
    /// offset of that crop.
    fn interior(&self, img: &Image) -> (Image, usize) {
        let i = self.marker_inset;
        if i == 0 || img.height <= 2 * i || img.width <= 2 * i {
            return (img.clone(), 0);
        }
        (img.crop(i, i, img.width - 2 * i, img.height - 2 * i), i)
    }

    /// The largest marker of colour `bgr` — or, given `near`, the one
    /// closest to it (a tracked dot beats a bigger stray). `feet` reports
    /// the bottom row instead of the centre.
    fn marker(
        &self,
        img: &Image,
        bgr: [u8; 3],
        tolerance: i32,
        feet: bool,
        near: Option<(i32, i32)>,
    ) -> Option<(i32, i32)> {
        let (inner, off) = self.interior(img);
        let blobs = marker_blobs(&color_mask(&inner, bgr, tolerance), MARKER_MIN_PX);
        let first = *blobs.first()?;
        let blob = match near {
            Some((nx, ny)) if blobs.len() > 1 => {
                let (nx, ny) = ((nx - off as i32) as f64, (ny - off as i32) as f64);
                let d =
                    |b: &crate::vision::Blob| (b.cx - nx).powi(2) + (b.y_max as f64 - ny).powi(2);
                *blobs.iter().min_by(|a, b| d(a).total_cmp(&d(b))).unwrap()
            }
            _ => first,
        };
        let off = off as i32;
        // Half-up, so an even-width dot's centre (x.5) always lands on the
        // same side.
        let x = (blob.cx + 0.5).floor() as i32 + off;
        let y = if feet {
            blob.y_max as i32
        } else {
            (blob.cy + 0.5).floor() as i32
        } + off;
        if feet {
            let bbox = (
                blob.x_min as i32 + off,
                blob.y_min as i32 + off,
                blob.x_max as i32 + off,
                blob.y_max as i32 + off,
            );
            self.state.lock().unwrap().last_box = Some(((x, y), bbox));
        }
        Some((x, y))
    }

    /// The player's feet, tracked by `tracker`: the candidate nearest the
    /// last position wins, and a single missed read repeats it.
    pub fn player_pos(&self, img: &Image, tracker: &mut PlayerTracker) -> Option<(i32, i32)> {
        let epoch = self.epoch.load(Ordering::Relaxed);
        if tracker.epoch != epoch {
            *tracker = PlayerTracker {
                epoch,
                ..Default::default()
            };
        }
        let found = self
            .marker(img, self.colors.player, 10, true, tracker.last)
            .or_else(|| tracker.seen.and_then(|s| self.rim_dot(img, s)));
        if let Some(pos) = found {
            tracker.last = Some(pos);
            tracker.seen = Some(pos);
            tracker.misses = 0;
            return Some(pos);
        }
        tracker.misses += 1;
        if tracker.last.is_some() && tracker.misses <= PLAYER_HOLD_FRAMES {
            return tracker.last;
        }
        tracker.last = None;
        None
    }

    /// The dot half under the frame rim (a rope lift to a platform at the
    /// map's top edge): the inset crop hides it, so look in the rim itself
    /// — only near `seen`, the last sighting, since the rim is frame. Its
    /// bottom row is still the feet.
    fn rim_dot(&self, img: &Image, seen: (i32, i32)) -> Option<(i32, i32)> {
        let i = self.marker_inset;
        if i == 0 {
            return None; // nothing was cropped
        }
        let (w, h) = (img.width, img.height);
        marker_blobs(&color_mask(img, self.colors.player, 10), MARKER_MIN_PX)
            .iter()
            .filter(|b| b.y_min < i || b.x_min < i || b.y_max + i >= h || b.x_max + i >= w)
            .map(|b| ((b.cx + 0.5).floor() as i32, b.y_max as i32))
            .filter(|p| (p.0 - seen.0).abs() <= RIM_REACH && (p.1 - seen.1).abs() <= RIM_REACH)
            .min_by_key(|p| (p.0 - seen.0).abs() + (p.1 - seen.1).abs())
    }

    pub fn rune_pos(&self, img: &Image) -> Option<(i32, i32)> {
        self.marker(img, self.colors.rune, 10, false, None)
    }

    /// The rune marker's box (inclusive, minimap px): the largest blob
    /// inside the rim crop, else anywhere — a rune on a platform at the
    /// map's top edge sits partly under the rim, like the player dot.
    pub fn rune_box(&self, img: &Image) -> Option<crate::rune::BoxPx> {
        let (inner, off) = self.interior(img);
        let found = |img: &Image, off: usize| {
            let b = *marker_blobs(&color_mask(img, self.colors.rune, 10), MARKER_MIN_PX).first()?;
            let o = off as i32;
            Some((
                b.x_min as i32 + o,
                b.y_min as i32 + o,
                b.x_max as i32 + o,
                b.y_max as i32 + o,
            ))
        };
        found(&inner, off).or_else(|| found(img, 0))
    }

    pub fn has_other_players(&self, img: &Image) -> bool {
        self.count_other_players(img, MARKER_MIN_PX) > 0
    }

    /// Other-player markers on the minimap: blobs of at least `min_px`
    /// pixels (markers within 2px of each other merge into one).
    pub fn count_other_players(&self, img: &Image, min_px: usize) -> usize {
        let (inner, _) = self.interior(img);
        marker_blobs(&color_mask(&inner, self.colors.other_player, 10), min_px).len()
    }

    /// Where a dot standing at `feet` covers (inclusive, bottom row on the feet).
    pub fn glyph_box(feet: (i32, i32)) -> (i32, i32, i32, i32) {
        let (x, y) = feet;
        (x - DOT_W / 2, y - DOT_H + 1, x + (DOT_W - 1) / 2, y)
    }

    /// The dot's box for the overlay: the detected one when `feet` is the
    /// last detection, else the glyph at `feet`.
    pub fn player_box(&self, feet: (i32, i32)) -> (i32, i32, i32, i32) {
        match self.state.lock().unwrap().last_box {
            Some((f, b)) if f == feet => b,
            _ => MinimapAnalyzer::glyph_box(feet),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(w: usize, h: usize, fill: [u8; 3]) -> Image {
        let mut img = Image::new(w, h);
        for y in 0..h {
            for x in 0..w {
                img.set_bgr(x, y, fill);
            }
        }
        img
    }

    fn dot(img: &mut Image, x: usize, y: usize, c: [u8; 3]) {
        for yy in y - 5..=y {
            for xx in x - 3..x + 3 {
                img.set_bgr(xx, yy, c);
            }
        }
    }

    #[test]
    fn blackout_then_settle_reports_loading_and_arrived() {
        let mut t = TransitionDetector::default();
        let (lit, dark) = (frame(40, 30, [90, 90, 90]), frame(40, 30, [0, 0, 0]));
        assert_eq!(t.note(&lit, 0.0), None);
        assert_eq!(t.note(&dark, 0.1), None);
        assert_eq!(t.note(&dark, 0.3), Some(TransitionEvent::Loading));
        assert_eq!(t.note(&lit, 0.5), None);
        assert_eq!(t.state, TransitionState::Settling);
        assert_eq!(t.note(&dark, 0.6), None); // a flicker back to dark
        assert_eq!(t.note(&lit, 0.7), None);
        assert_eq!(t.note(&lit, 1.4), Some(TransitionEvent::Arrived));
        assert!(!t.loading());
    }

    #[test]
    fn tracks_the_nearest_dot_and_holds_one_missed_read() {
        let colors = MinimapColors::default();
        let mm = MinimapAnalyzer::new(colors, Some((0, 0, 100, 60)), 0);
        let mut img = frame(100, 60, [40, 30, 20]);
        dot(&mut img, 20, 30, colors.player);
        let mut t = PlayerTracker::default();
        assert_eq!(mm.player_pos(&img, &mut t), Some((20, 30)));
        // A bigger stray appears far away: the tracked dot still wins.
        for y in 40..55 {
            for x in 70..85 {
                img.set_bgr(x, y, colors.player);
            }
        }
        dot(&mut img, 22, 30, colors.player);
        let got = mm.player_pos(&img, &mut t).unwrap();
        assert!(got.0 < 40, "{got:?}");
        let empty = frame(100, 60, [40, 30, 20]);
        assert_eq!(mm.player_pos(&empty, &mut t), Some(got)); // held once
        assert_eq!(mm.player_pos(&empty, &mut t), None);
    }

    #[test]
    fn a_dot_rising_under_the_frame_rim_is_still_read_near_its_last_sighting() {
        let colors = MinimapColors::default();
        let mm = MinimapAnalyzer::new(colors, Some((0, 0, 100, 60)), 4);
        let bg = [40, 30, 20];
        let mut img = frame(100, 60, bg);
        dot(&mut img, 50, 14, colors.player);
        let mut t = PlayerTracker::default();
        assert_eq!(mm.player_pos(&img, &mut t), Some((50, 14)));
        // A rope lift to the top: only rows 2-4 of the dot show, and just
        // its 2px bottom tip is inside the 4px rim (the captured Hotel
        // Arcus case).
        let sliver = |img: &mut Image, x0: usize| {
            for y in 2..=3 {
                for x in x0..x0 + 6 {
                    img.set_bgr(x, y, colors.player);
                }
            }
            img.set_bgr(x0 + 2, 4, colors.player);
            img.set_bgr(x0 + 3, 4, colors.player);
        };
        let mut top = frame(100, 60, bg);
        sliver(&mut top, 47);
        for _ in 0..3 {
            assert_eq!(mm.player_pos(&top, &mut t), Some((50, 4)));
        }
        // Never seen: the rim is frame, not a dot.
        assert_eq!(mm.player_pos(&top, &mut PlayerTracker::default()), None);
        // Far from the last sighting: not the player either.
        let mut far = frame(100, 60, bg);
        sliver(&mut far, 5);
        let mut t = PlayerTracker::default();
        mm.player_pos(&img, &mut t);
        mm.player_pos(&far, &mut t); // the one held read
        assert_eq!(mm.player_pos(&far, &mut t), None);
    }

    #[test]
    fn arrival_drops_a_detected_region_but_not_a_pinned_one() {
        let colors = MinimapColors::default();
        let mm = MinimapAnalyzer::new(colors, None, 0);
        mm.set_region((5, 5, 100, 60), false);
        let (lit, dark) = (frame(40, 30, [90, 90, 90]), frame(40, 30, [0, 0, 0]));
        mm.note_frame(&dark, 0.0);
        mm.note_frame(&dark, 0.2);
        mm.note_frame(&lit, 0.3);
        assert!(mm.note_frame(&lit, 1.0));
        assert_eq!(mm.region(), None);

        let pinned = MinimapAnalyzer::new(colors, Some((5, 5, 100, 60)), 0);
        pinned.note_frame(&dark, 0.0);
        pinned.note_frame(&dark, 0.2);
        pinned.note_frame(&lit, 0.3);
        assert!(pinned.note_frame(&lit, 1.0));
        assert_eq!(pinned.region(), Some((5, 5, 100, 60)));
    }
}
