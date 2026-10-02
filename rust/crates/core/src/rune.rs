//! Runes on the minimap: remembering one the player's dot may cover, and
//! where to stand beside it.
//!
//! The player's glyph is drawn over the rune's, so standing on a rune
//! hides it: a rune that vanishes under the player is still there. It
//! counts as gone only once it stays unseen with the player clearly off
//! it. The bot stands touching it ([`TARGET_GAP`]) to solve it; standing
//! on it counts too ([`at_rune`]).

use crate::minimap::{MinimapAnalyzer, DOT_W};
use crate::navgraph::NavGraph;

/// Inclusive pixel box: (x0, y0, x1, y1).
pub type BoxPx = (i32, i32, i32, i32);

/// Reads in a row without the rune, the player clearly off it, before it
/// counts as gone (solved, or taken by someone else).
pub const GONE_READS: u32 = 5;
/// A rune covered this long (s) without a sighting counts as gone once it
/// stays unseen: a player parked on a solved rune can't hold it forever.
pub const COVER_MAX_S: f64 = 30.0;
/// The gap (px) the bot aims for between its glyph and the rune's:
/// touching, side by side.
pub const TARGET_GAP: i32 = 0;
/// The widest gap (px) that still counts as at the rune.
pub const MAX_GAP: i32 = 0;
/// A rune stands on the drawn platform up to this far (px) under its box.
pub const ROW_REACH: f64 = 12.0;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Rune {
    pub bbox: BoxPx,
    pub seen_at: f64,
}

impl Rune {
    pub fn center(&self) -> (f64, f64) {
        let (x0, y0, x1, y1) = self.bbox;
        ((x0 + x1) as f64 / 2.0, (y0 + y1) as f64 / 2.0)
    }
}

#[derive(Debug, Clone, Default)]
pub struct RuneTracker {
    pub rune: Option<Rune>,
    missing: u32,
    covered_since: Option<f64>,
}

impl RuneTracker {
    /// One look: `seen` is the rune's box this frame, `player` the feet
    /// (None: unread). True while a rune is (still) there.
    pub fn observe(&mut self, seen: Option<BoxPx>, player: Option<(i32, i32)>, now: f64) -> bool {
        if let Some(b) = seen {
            // A partly covered sighting of the same rune keeps the full box.
            let bbox = match self.rune {
                Some(r) if overlaps(r.bbox, b, 0) && area(b) < area(r.bbox) => r.bbox,
                _ => b,
            };
            self.rune = Some(Rune { bbox, seen_at: now });
            (self.missing, self.covered_since) = (0, None);
            return true;
        }
        let Some(r) = self.rune else { return false };
        let Some(p) = player else { return true }; // covered or gone: can't tell
        let covered = overlaps(MinimapAnalyzer::glyph_box(p), r.bbox, 0);
        let since = if covered {
            *self.covered_since.get_or_insert(now)
        } else {
            self.covered_since = None;
            now
        };
        if covered && now - since < COVER_MAX_S {
            self.missing = 0;
            return true;
        }
        self.missing += 1;
        if self.missing >= GONE_READS {
            self.clear();
            return false;
        }
        true
    }

    pub fn clear(&mut self) {
        *self = RuneTracker::default();
    }
}

fn area(b: BoxPx) -> i32 {
    (b.2 - b.0 + 1) * (b.3 - b.1 + 1)
}

/// The boxes overlap, or come within `margin` px of it.
fn overlaps(a: BoxPx, b: BoxPx, margin: i32) -> bool {
    a.0 <= b.2 + margin && b.0 <= a.2 + margin && a.1 <= b.3 + margin && b.1 <= a.3 + margin
}

/// The player glyph's x-span with its feet at `x`.
fn glyph_x(x: i32) -> (i32, i32) {
    (x - DOT_W / 2, x + (DOT_W - 1) / 2)
}

/// The gap (px) between the player's glyph, feet at `x`, and the rune's;
/// None when they overlap.
pub fn gap(x: i32, rune: BoxPx) -> Option<i32> {
    let (p0, p1) = glyph_x(x);
    if p1 < rune.0 {
        Some(rune.0 - p1 - 1)
    } else if p0 > rune.2 {
        Some(p0 - rune.2 - 1)
    } else {
        None
    }
}

/// Standing on the rune: feet this close (px) to `on_x`.
pub const ON_TOL: i32 = 1;

/// Feet x that centre the player's glyph over the rune.
pub fn on_x(rune: BoxPx) -> i32 {
    // The glyph spans x - DOT_W/2 ..= x + (DOT_W-1)/2: its centre is x - 0.5.
    ((rune.0 + rune.2) as f64 / 2.0 + 0.5).round() as i32
}

/// Feet at `x` stand on the rune.
pub fn on_rune(x: i32, rune: BoxPx) -> bool {
    (x - on_x(rune)).abs() <= ON_TOL
}

/// Feet at `x` are at the rune: glyphs at most `MAX_GAP` apart, or
/// overlapping (on it — the tracker still remembers a covered rune).
pub fn at_rune(x: i32, rune: BoxPx) -> bool {
    gap(x, rune).is_none_or(|g| g <= MAX_GAP)
}

/// Feet x beside the rune, `gap` px from it, on its left or right.
pub fn slot_x(rune: BoxPx, left: bool, gap: i32) -> i32 {
    if left {
        rune.0 - 1 - gap - (DOT_W - 1) / 2
    } else {
        rune.2 + 1 + gap + DOT_W / 2
    }
}

/// The drawn platform the rune stands on: the nearest row at or up to
/// `ROW_REACH` under the bottom of its box.
pub fn platform_under(graph: &NavGraph, rune: BoxPx) -> Option<usize> {
    let (cx, bottom) = ((rune.0 + rune.2) as f64 / 2.0, rune.3 as f64);
    graph
        .platforms
        .iter()
        .enumerate()
        .filter(|(_, p)| p.spans(cx, 0.0))
        .map(|(i, p)| (i, p.y_at(cx) - bottom))
        .filter(|(_, d)| (-2.0..=ROW_REACH).contains(d))
        .min_by(|a, b| a.1.total_cmp(&b.1))
        .map(|(i, _)| i)
}

#[cfg(test)]
mod tests {
    use super::*;

    const RUNE: BoxPx = (50, 20, 55, 25);

    #[test]
    fn a_covered_rune_stays_and_an_uncovered_absence_ends_it() {
        let mut t = RuneTracker::default();
        assert!(!t.observe(None, Some((10, 25)), 0.0));
        assert!(t.observe(Some(RUNE), Some((10, 25)), 0.0));
        for i in 1..20 {
            assert!(t.observe(None, Some((52, 25)), i as f64)); // standing on it
        }
        for i in 0..GONE_READS - 1 {
            assert!(t.observe(None, Some((70, 25)), 20.0 + i as f64));
        }
        assert!(!t.observe(None, Some((70, 25)), 30.0));
        assert!(t.rune.is_none());
    }

    #[test]
    fn touching_the_rune_is_not_covering_it() {
        let mut t = RuneTracker::default();
        t.observe(Some(RUNE), None, 0.0);
        let beside = slot_x(RUNE, true, TARGET_GAP);
        for i in 0..GONE_READS - 1 {
            assert!(t.observe(None, Some((beside, 25)), 1.0 + i as f64));
        }
        assert!(!t.observe(None, Some((beside, 25)), 10.0)); // solved beside it
    }

    #[test]
    fn an_unread_player_proves_nothing() {
        let mut t = RuneTracker::default();
        t.observe(Some(RUNE), None, 0.0);
        for i in 0..20 {
            assert!(t.observe(None, None, i as f64));
        }
    }

    #[test]
    fn a_long_cover_without_a_sighting_lets_it_go() {
        let mut t = RuneTracker::default();
        t.observe(Some(RUNE), None, 0.0);
        let mut gone_at = None;
        for i in 1..60 {
            if !t.observe(None, Some((52, 25)), i as f64) {
                gone_at = Some(i);
                break;
            }
        }
        let at = gone_at.expect("let go");
        assert!(at as f64 >= COVER_MAX_S);
    }

    #[test]
    fn a_partly_covered_sighting_keeps_the_full_box() {
        let mut t = RuneTracker::default();
        t.observe(Some(RUNE), None, 0.0);
        t.observe(Some((53, 20, 55, 25)), None, 1.0);
        assert_eq!(t.rune.unwrap().bbox, RUNE);
        t.observe(Some((120, 20, 125, 25)), None, 2.0); // a new rune elsewhere
        assert_eq!(t.rune.unwrap().bbox, (120, 20, 125, 25));
    }

    #[test]
    fn slots_sit_the_given_gap_beside_the_rune() {
        for g in 0..=2 {
            assert_eq!(gap(slot_x(RUNE, true, g), RUNE), Some(g));
            assert_eq!(gap(slot_x(RUNE, false, g), RUNE), Some(g));
        }
        assert_eq!(gap(52, RUNE), None);
        assert!(at_rune(slot_x(RUNE, true, TARGET_GAP), RUNE));
        assert!(at_rune(52, RUNE)); // on it
        assert!(!at_rune(slot_x(RUNE, false, MAX_GAP + 1), RUNE));
        assert!(on_rune(on_x(RUNE), RUNE) && on_rune(53, RUNE));
        assert!(!on_rune(slot_x(RUNE, true, TARGET_GAP), RUNE));
        assert_eq!(gap(on_x(RUNE), RUNE), None); // covering it
    }

    #[test]
    fn the_rune_stands_on_the_row_under_it() {
        let g = NavGraph::new(
            &[[0.0, 40.0, 100.0, 40.0], [0.0, 30.0, 100.0, 30.0]],
            &[],
            &crate::reach::ReachModel::new(
                crate::reach::base_reach(&crate::config::BotConfig::default()),
                None,
            ),
            crate::navgraph::GraphOptions::default(),
        );
        assert_eq!(platform_under(&g, RUNE), Some(1)); // y 30, 5 under its bottom
        assert_eq!(platform_under(&g, (50, 50, 55, 60)), None);
    }
}
