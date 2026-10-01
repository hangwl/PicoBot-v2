//! Movement graph over hand-drawn platforms, and route planning.
//!
//! Nodes are points on platforms: segment ends plus the x positions where
//! a move to another platform is possible. Edges:
//!
//! - `walk` along one platform;
//! - `down_jump` onto the next platform below; `drop` off an end;
//! - upward: `up_flash` / `rope_lift` onto a platform above; `up_side_flash`
//!   (up, then sideways) up and over onto a higher platform across a gap;
//! - horizontal gaps: `jump`, `flash`, `double_flash`; `teleport`;
//! - learned ropes: `climb_up` (a moving grab) and `climb_down`.
//!
//! Rope lift grabs the **highest** platform within its range, and an up
//! flash comes down on the highest platform its peak clears — so neither
//! is planned onto a lower tier it would pass. Platform ends are the
//! boundaries. Whether a jump-type edge exists comes from the learned
//! [`ReachModel`]; edges beyond the proven envelope but within its
//! exploration limit cost more. Coordinates are minimap px; costs are rough
//! seconds.

use std::cmp::{Ordering, Reverse};
use std::collections::{BinaryHeap, HashMap};

use rand::Rng;

use crate::reach::{Move, ReachModel};

pub const CLIMB_SPEED: f64 = 30.0;
/// A rope's bottom end within this rise of a platform can be jump-grabbed.
pub const JUMP_GRAB_REACH: f64 = 24.0;
/// Sideways drift a jump-grab can cover.
pub const DRIFT_REACH: f64 = 8.0;
/// Take off this far beside a rope: the grab is a hop toward it.
pub const GRAB_HOP_PX: f64 = 6.0;
/// A flash jump off a platform end grabs a rope this far out.
pub const FLASH_GRAB_REACH: f64 = 20.0;
/// A rope between stacked platforms stops this far above the lower one.
pub const ROPE_BOTTOM_GAP: f64 = 5.0;
/// A learned rope's top stands this far above its platform, so the climb
/// ends on the platform rather than a hair under it.
pub const ROPE_TOP_OVERSHOOT: f64 = 3.0;
/// A stored rope top this far below its platform (older maps) still
/// belongs to it: it is lifted onto the row when the graph is built.
pub const ROPE_TOP_SLACK: f64 = 6.0;
/// A down jump takes off more than this far from a rope hanging off its
/// platform (the default `rope_clear_px`): Down on a rope's top grabs the
/// rope instead of dropping.
pub const ROPE_CLEAR_PX: f64 = 8.0;
pub const EXPLORE_PENALTY: f64 = 1.6;
/// Max rise for a "horizontal" gap move.
pub const LEVEL_PX: f64 = 4.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum MoveKind {
    Walk,
    DownJump,
    Drop,
    Jump,
    Flash,
    DoubleFlash,
    UpFlash,
    UpSideFlash,
    RopeLift,
    Teleport,
    ClimbUp,
    ClimbDown,
}

impl MoveKind {
    pub fn as_str(self) -> &'static str {
        match self {
            MoveKind::Walk => "walk",
            MoveKind::DownJump => "down_jump",
            MoveKind::Drop => "drop",
            MoveKind::Jump => "jump",
            MoveKind::Flash => "flash",
            MoveKind::DoubleFlash => "double_flash",
            MoveKind::UpFlash => "up_flash",
            MoveKind::UpSideFlash => "up_side_flash",
            MoveKind::RopeLift => "rope_lift",
            MoveKind::Teleport => "teleport",
            MoveKind::ClimbUp => "climb_up",
            MoveKind::ClimbDown => "climb_down",
        }
    }

    /// Base cost (seconds) of the fixed-cost move kinds.
    fn cost(self) -> f64 {
        match self {
            MoveKind::DownJump => 0.7,
            MoveKind::Drop => 0.6,
            MoveKind::Jump => 0.6,
            MoveKind::Flash => 0.8,
            MoveKind::DoubleFlash => 1.1,
            MoveKind::UpFlash => 1.0,
            MoveKind::UpSideFlash => 1.3,
            MoveKind::RopeLift => 0.5,
            MoveKind::Teleport => 0.5,
            MoveKind::Walk | MoveKind::ClimbUp | MoveKind::ClimbDown => 0.0,
        }
    }

    /// The learned-reach entry for jump-type moves.
    pub fn reach_move(self) -> Option<Move> {
        Some(match self {
            MoveKind::Jump => Move::Jump,
            MoveKind::Flash => Move::Flash,
            MoveKind::DoubleFlash => Move::DoubleFlash,
            MoveKind::UpFlash => Move::UpFlash,
            MoveKind::UpSideFlash => Move::UpSideFlash,
            MoveKind::RopeLift => Move::RopeLift,
            MoveKind::Teleport => Move::Teleport,
            _ => return None,
        })
    }

    fn needs_flash(self) -> bool {
        matches!(
            self,
            MoveKind::Flash | MoveKind::DoubleFlash | MoveKind::UpFlash | MoveKind::UpSideFlash
        )
    }
}

/// A drawn platform, always stored left to right.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Platform {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
}

impl Platform {
    pub fn from_segment(s: [f64; 4]) -> Self {
        let [x0, y0, x1, y1] = s;
        if x0 <= x1 {
            Platform { x0, y0, x1, y1 }
        } else {
            Platform {
                x0: x1,
                y0: y1,
                x1: x0,
                y1: y0,
            }
        }
    }

    pub fn spans(&self, x: f64, slack: f64) -> bool {
        self.x0 - slack <= x && x <= self.x1 + slack
    }

    pub fn y_at(&self, x: f64) -> f64 {
        if self.x1 == self.x0 {
            return (self.y0 + self.y1) / 2.0;
        }
        let t = ((x - self.x0) / (self.x1 - self.x0)).clamp(0.0, 1.0);
        self.y0 + t * (self.y1 - self.y0)
    }
}

/// One planned movement step.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Leg {
    pub kind: MoveKind,
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
    pub cost: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    Left,
    Right,
}

#[derive(Debug, Clone, Copy)]
struct Edge {
    to: usize,
    kind: MoveKind,
    cost: f64,
}

/// Build settings; the defaults match the Python host.
#[derive(Debug, Clone, Copy)]
pub struct GraphOptions {
    pub walk_speed: f64,
    /// Multiplies every walking leg's cost (above 1 prefers hops and ropes
    /// over walking).
    pub walk_factor: f64,
    pub snap_px: f64,
    pub edge_inset_px: f64,
    pub takeoff_inset_px: f64,
    /// Rope-lift grab range; None = the rope lift's base reach.
    pub rope_max_px: Option<f64>,
    pub rope_penalty: f64,
    pub allow_flash: bool,
    pub allow_double_flash: bool,
    pub allow_teleport: bool,
    /// How far a down jump's takeoff keeps from a rope off its platform.
    pub rope_clear_px: f64,
}

impl Default for GraphOptions {
    fn default() -> Self {
        GraphOptions {
            walk_speed: 40.0,
            walk_factor: 1.0,
            snap_px: 8.0,
            edge_inset_px: 4.0,
            takeoff_inset_px: 3.0,
            rope_max_px: None,
            rope_penalty: 5.0,
            allow_flash: true,
            allow_double_flash: true,
            allow_teleport: false,
            rope_clear_px: ROPE_CLEAR_PX,
        }
    }
}

/// Python's `round(x, 1)`: exact decimal rounding, ties to even (Rust's
/// fixed-precision formatting rounds the same way).
fn round1(x: f64) -> f64 {
    format!("{x:.1}").parse().unwrap()
}

pub struct NavGraph {
    pub platforms: Vec<Platform>,
    pub ropes: Vec<[f64; 4]>,
    pub snap_px: f64,
    pub walk_speed: f64,
    opts: GraphOptions,
    rope_max_px: f64,
    /// (platform, x) per node; x is rounded to 0.1px.
    nodes: Vec<(usize, f64)>,
    index: HashMap<(usize, i64), usize>,
    edges: Vec<Vec<Edge>>,
}

impl NavGraph {
    pub fn new(
        segments: &[[f64; 4]],
        ropes: &[[f64; 4]],
        reach: &ReachModel,
        opts: GraphOptions,
    ) -> Self {
        let platforms = segments
            .iter()
            .filter(|s| (s[2] - s[0]).abs() >= 1.0)
            .map(|s| Platform::from_segment(*s))
            .collect();
        let mut g = NavGraph {
            platforms,
            ropes: ropes
                .iter()
                .filter(|s| (s[2] - s[0]).abs() < 20.0)
                .copied()
                .collect(),
            snap_px: opts.snap_px,
            walk_speed: opts.walk_speed / opts.walk_factor.max(0.01),
            rope_max_px: opts
                .rope_max_px
                .unwrap_or(reach.base[Move::RopeLift as usize].rise),
            opts,
            nodes: Vec::new(),
            index: HashMap::new(),
            edges: Vec::new(),
        };
        g.build(reach);
        g
    }

    // -- Geometry -------------------------------------------------------------

    /// Nearest platform strictly below `y` at column `x`.
    pub fn below(&self, x: f64, y: f64, exclude: Option<usize>) -> Option<usize> {
        let mut best: Option<(f64, usize)> = None;
        for (i, p) in self.platforms.iter().enumerate() {
            if Some(i) == exclude || !p.spans(x, 0.0) {
                continue;
            }
            let py = p.y_at(x);
            if py > y + 1.0 && best.is_none_or(|(b, _)| py < b) {
                best = Some((py, i));
            }
        }
        best.map(|(_, i)| i)
    }

    /// Nearest platform strictly above `y` at column `x`.
    pub fn above(&self, x: f64, y: f64, exclude: Option<usize>) -> Option<usize> {
        let mut best: Option<(f64, usize)> = None;
        for (i, p) in self.platforms.iter().enumerate() {
            if Some(i) == exclude || !p.spans(x, 0.0) {
                continue;
            }
            let py = p.y_at(x);
            if py < y - 1.0 && best.is_none_or(|(b, _)| py > b) {
                best = Some((py, i));
            }
        }
        best.map(|(_, i)| i)
    }

    /// (platform, rise) of the highest platform above `y` at column `x`
    /// within `max_rise` — where a rope lift grabs, or an up flash lands.
    pub fn highest_above(
        &self,
        x: f64,
        y: f64,
        max_rise: f64,
        exclude: Option<usize>,
    ) -> Option<(usize, f64)> {
        let mut best: Option<(usize, f64)> = None;
        for (j, q) in self.platforms.iter().enumerate() {
            if Some(j) == exclude || !q.spans(x, 0.0) {
                continue;
            }
            let rise = y - q.y_at(x);
            if 1.0 < rise && rise <= max_rise && best.is_none_or(|(_, b)| rise > b) {
                best = Some((j, rise));
            }
        }
        best
    }

    /// The platform a point stands on. Asymmetric: the point may sit up to
    /// `snap_px` above the drawn row (the glyph floats above the line) and
    /// 2px below it, so stacked tiers stay unambiguous.
    pub fn locate(&self, x: f64, y: f64) -> Option<usize> {
        let mut best: Option<(f64, usize)> = None;
        for (i, p) in self.platforms.iter().enumerate() {
            if !p.spans(x, 3.0) {
                continue;
            }
            let d = p.y_at(x) - y; // +: the point floats above the row
            if (-2.0..=self.snap_px).contains(&d) && best.is_none_or(|(b, _)| d < b) {
                best = Some((d, i));
            }
        }
        best.map(|(_, i)| i)
    }

    /// Which way to leap off a rope (or undrawn ground): toward the nearest
    /// platform it can land on — at or below, never above.
    pub fn exit_direction(&self, x: f64, y: f64) -> Direction {
        let dir = |right: bool| {
            if right {
                Direction::Right
            } else {
                Direction::Left
            }
        };
        let mut best: Option<((f64, f64), Direction)> = None;
        for p in &self.platforms {
            let near_x = x.clamp(p.x0, p.x1);
            let row = p.y_at(near_x);
            if row < y - 4.0 {
                continue; // above: a leap can't reach it
            }
            let gap = (near_x - x).abs();
            let d = if gap == 0.0 {
                dir((p.x0 + p.x1) / 2.0 >= x)
            } else {
                dir(near_x > x)
            };
            let key = (gap, row - y);
            if best.is_none_or(|(b, _)| key.partial_cmp(&b) == Some(Ordering::Less)) {
                best = Some((key, d));
            }
        }
        if let Some((_, d)) = best {
            return d;
        }
        let dist = |p: &Platform| (p.x0 - x).abs().min((p.x1 - x).abs());
        let Some(near) = self
            .platforms
            .iter()
            .min_by(|a, b| dist(a).total_cmp(&dist(b)))
        else {
            return Direction::Right;
        };
        let a = ((near.x0 - x).abs(), near.x0);
        let b = ((near.x1 - x).abs(), near.x1);
        let cx = if b.partial_cmp(&a) == Some(Ordering::Less) {
            b.1
        } else {
            a.1
        };
        dir(cx > x)
    }

    // -- Build ----------------------------------------------------------------

    fn node(&mut self, plat: usize, x: f64) -> usize {
        let p = self.platforms[plat];
        let rx = round1(x.clamp(p.x0, p.x1));
        let key = (plat, (rx * 10.0).round() as i64);
        if let Some(&i) = self.index.get(&key) {
            return i;
        }
        let i = self.nodes.len();
        self.index.insert(key, i);
        self.nodes.push((plat, rx));
        self.edges.push(Vec::new());
        i
    }

    fn link(&mut self, a: usize, b: usize, kind: MoveKind, cost: f64) {
        if a != b {
            self.edges[a].push(Edge { to: b, kind, cost });
        }
    }

    /// Add a jump-type edge if `kind`'s learned reach covers (dx, rise).
    #[allow(clippy::too_many_arguments)]
    fn mv(
        &mut self,
        reach: &ReachModel,
        i: usize,
        xa: f64,
        j: usize,
        xb: f64,
        kind: MoveKind,
        dx: f64,
        rise: f64,
    ) {
        if kind.needs_flash() && !self.opts.allow_flash {
            return;
        }
        if kind == MoveKind::DoubleFlash && !self.opts.allow_double_flash {
            return;
        }
        if kind == MoveKind::Teleport && !self.opts.allow_teleport {
            return;
        }
        let Some(m) = kind.reach_move() else { return };
        let Some(proven) = reach.fits(m, dx, rise.max(0.0)) else {
            return;
        };
        let cost = kind.cost() * if proven { 1.0 } else { EXPLORE_PENALTY };
        let (a, b) = (self.node(i, xa), self.node(j, xb));
        self.link(a, b, kind, cost);
    }

    fn build(&mut self, reach: &ReachModel) {
        let n = self.platforms.len();
        for i in 0..n {
            let p = self.platforms[i];
            self.node(i, p.x0);
            self.node(i, p.x1);
        }
        for i in 0..n {
            for j in 0..n {
                if i != j {
                    self.link_stacked(reach, i, j);
                }
            }
            let p = self.platforms[i];
            self.link_off_end(reach, i, p.x0, -1.0);
            self.link_off_end(reach, i, p.x1, 1.0);
        }
        for i in 0..n {
            self.link_rope_tiers(reach, i);
        }
        self.link_ropes();
        for i in 0..n {
            let mut pts: Vec<(f64, usize)> = self
                .nodes
                .iter()
                .enumerate()
                .filter(|(_, (pi, _))| *pi == i)
                .map(|(k, (_, x))| (*x, k))
                .collect();
            pts.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
            for w in pts.windows(2) {
                let ((xa, na), (xb, nb)) = (w[0], w[1]);
                let cost = (xb - xa) / self.walk_speed;
                self.link(na, nb, MoveKind::Walk, cost);
                self.link(nb, na, MoveKind::Walk, cost);
            }
        }
    }

    fn link_stacked(&mut self, reach: &ReachModel, i: usize, j: usize) {
        let (p, q) = (self.platforms[i], self.platforms[j]);
        let (lo, hi) = (p.x0.max(q.x0), p.x1.min(q.x1));
        if lo > hi {
            return;
        }
        let inset = self.opts.edge_inset_px.min((hi - lo) / 4.0);
        // Distinct columns (a narrow overlap can make them coincide).
        let mut xs: Vec<f64> = Vec::with_capacity(3);
        for x in [lo + inset, hi - inset, (lo + hi) / 2.0] {
            if !xs.contains(&x) {
                xs.push(x);
            }
        }
        for x in xs {
            let yp = p.y_at(x);
            if self.below(x, yp, Some(i)) == Some(j) {
                let clear = self
                    .clear_of_ropes(i, x, lo, hi)
                    .filter(|&cx| self.below(cx, p.y_at(cx), Some(i)) == Some(j));
                if let Some(cx) = clear {
                    let (a, b) = (self.node(i, cx), self.node(j, cx));
                    self.link(a, b, MoveKind::DownJump, MoveKind::DownJump.cost());
                }
            } else if self.above(x, yp, Some(i)) == Some(j) {
                self.mv(reach, i, x, j, x, MoveKind::Teleport, 0.0, yp - q.y_at(x));
            }
            if self.up_lands_on(reach, j, x, yp, Some(i)) {
                self.mv(reach, i, x, j, x, MoveKind::UpFlash, 0.0, yp - q.y_at(x));
            }
        }
    }

    /// `x`, or the nearest spot in `lo..=hi` clear of every rope hanging off
    /// platform `i` (`rope_clear_px`); None when there's no such spot.
    fn clear_of_ropes(&self, i: usize, x: f64, lo: f64, hi: f64) -> Option<f64> {
        let gap = self.opts.rope_clear_px;
        if gap <= 0.0 {
            return Some(x);
        }
        let tops: Vec<f64> = self
            .ropes
            .iter()
            .map(|r| ((r[0] + r[2]) / 2.0, r[1].min(r[3])))
            .filter(|&(rx, top)| self.locate(rx, top) == Some(i))
            .map(|(rx, _)| rx)
            .collect();
        let clear = |x: f64| tops.iter().all(|rx| (x - rx).abs() > gap);
        if clear(x) {
            return Some(x);
        }
        tops.iter()
            .flat_map(|rx| [rx - gap - 1.0, rx + gap + 1.0])
            .filter(|&c| (lo..=hi).contains(&c) && clear(c))
            .min_by(|a, b| (a - x).abs().total_cmp(&(b - x).abs()))
    }

    /// Whether an up flash rising from `y` at column `x` comes down on
    /// platform `j`: the highest platform its peak clears — or, with none
    /// that low, the nearest above (an exploratory reach).
    fn up_lands_on(
        &self,
        reach: &ReachModel,
        j: usize,
        x: f64,
        y: f64,
        exclude: Option<usize>,
    ) -> bool {
        let peak = reach.get(Move::UpFlash).rise;
        match self.highest_above(x, y, peak, exclude) {
            Some((hit, _)) => hit == j,
            None => self.above(x, y, exclude) == Some(j),
        }
    }

    fn link_rope_tiers(&mut self, reach: &ReachModel, i: usize) {
        let p = self.platforms[i];
        let xs: Vec<f64> = self
            .nodes
            .iter()
            .filter(|(pi, _)| *pi == i)
            .map(|(_, x)| *x)
            .collect();
        for x in xs {
            if let Some((j, rise)) = self.highest_above(x, p.y_at(x), self.rope_max_px, Some(i)) {
                self.mv(reach, i, x, j, x, MoveKind::RopeLift, 0.0, rise);
            }
        }
    }

    fn link_ropes(&mut self) {
        let ropes = self.ropes.clone();
        for [x0, y0, x1, y1] in ropes {
            let rx = (x0 + x1) / 2.0;
            let (mut bottom, top) = (y0.max(y1), y0.min(y1));
            let Some(hi) = self.locate(rx, top) else {
                continue;
            };
            if let Some(under) = self.below(rx, top, Some(hi)) {
                bottom = bottom.min(self.platforms[under].y_at(rx) - ROPE_BOTTOM_GAP);
            }
            if bottom <= top + 1.0 {
                continue;
            }
            // Down: grab at the top, descend past the bottom end, land.
            let land = self
                .below(rx, bottom - 1.0, None)
                .or_else(|| self.locate(rx, bottom));
            if let Some(land) = land.filter(|l| *l != hi) {
                let cost = 0.5 + (bottom - top) / CLIMB_SPEED + self.opts.rope_penalty;
                let (a, b) = (self.node(hi, rx), self.node(land, rx));
                self.link(a, b, MoveKind::ClimbDown, cost);
            }
            // Up: a moving grab from every platform in reach below the end.
            for i in 0..self.platforms.len() {
                if i == hi {
                    continue;
                }
                let p = self.platforms[i];
                let row = p.y_at(rx.clamp(p.x0, p.x1));
                let rise = row - bottom; // +: platform below the rope's end
                if !(0.0..=JUMP_GRAB_REACH).contains(&rise) {
                    continue;
                }
                let Some(tx) = self.grab_takeoff(&p, rx) else {
                    continue;
                };
                let cost = 0.7 + (bottom - top) / CLIMB_SPEED + self.opts.rope_penalty;
                let (a, b) = (self.node(i, tx), self.node(hi, rx));
                self.link(a, b, MoveKind::ClimbUp, cost);
            }
        }
    }

    /// Where to take off on `p` to grab a rope at `rx`: beside it (the side
    /// with more platform left) so the grab is a hop toward it; a platform
    /// end for a flash grab past the edge; straight under it only when the
    /// platform is too narrow to step aside.
    fn grab_takeoff(&self, p: &Platform, rx: f64) -> Option<f64> {
        if p.x0 + 1.0 <= rx && rx <= p.x1 - 1.0 {
            let mut sides = [(rx - p.x0, rx - GRAB_HOP_PX), (p.x1 - rx, rx + GRAB_HOP_PX)];
            // Most room first (ties: the larger takeoff x), as Python's
            // `sorted(..., reverse=True)` orders the tuples.
            sides.sort_by(|a, b| b.partial_cmp(a).unwrap_or(Ordering::Equal));
            return Some(
                sides
                    .iter()
                    .map(|(_, tx)| *tx)
                    .find(|tx| p.x0 + 1.0 <= *tx && *tx <= p.x1 - 1.0)
                    .unwrap_or(rx),
            );
        }
        let end = if rx > p.x1 { p.x1 - 1.0 } else { p.x0 + 1.0 };
        let gap = (rx - end).abs();
        if gap <= DRIFT_REACH || (gap <= FLASH_GRAB_REACH && self.opts.allow_flash) {
            return Some(end);
        }
        None
    }

    fn link_off_end(&mut self, reach: &ReachModel, i: usize, end: f64, step: f64) {
        let p = self.platforms[i];
        let ey = p.y_at(end);
        let src = self.node(i, end);
        if let Some(land) = self.below(end + 2.0 * step, ey, Some(i)) {
            let b = self.node(land, end + 2.0 * step);
            self.link(src, b, MoveKind::Drop, MoveKind::Drop.cost());
        }
        let takeoff = end - step * self.opts.takeoff_inset_px.min((p.x1 - p.x0) / 4.0);
        for j in 0..self.platforms.len() {
            if j == i {
                continue;
            }
            let q = self.platforms[j];
            let near = if step > 0.0 { q.x0 } else { q.x1 };
            if (near - end) * step <= 0.0 {
                continue;
            }
            let land_x = near + step * self.opts.edge_inset_px.min((q.x1 - q.x0) / 4.0);
            let dx = (land_x - takeoff).abs();
            let rise = ey - q.y_at(near);
            if rise <= LEVEL_PX {
                for kind in [
                    MoveKind::Jump,
                    MoveKind::Flash,
                    MoveKind::DoubleFlash,
                    MoveKind::Teleport,
                ] {
                    self.mv(reach, i, takeoff, j, land_x, kind, dx, rise);
                }
            } else {
                self.mv(
                    reach,
                    i,
                    takeoff,
                    j,
                    land_x,
                    MoveKind::UpSideFlash,
                    dx,
                    rise,
                );
                if self.up_lands_on(reach, j, land_x, ey, Some(i)) {
                    self.mv(reach, i, takeoff, j, land_x, MoveKind::UpFlash, dx, rise);
                }
                self.mv(reach, i, takeoff, j, land_x, MoveKind::Teleport, dx, rise);
            }
        }
    }

    // -- Queries --------------------------------------------------------------

    pub fn point(&self, node: usize) -> (f64, f64) {
        let (plat, x) = self.nodes[node];
        (x, self.platforms[plat].y_at(x))
    }

    /// Every non-walk edge (for the dashboard overlay).
    pub fn transfer_legs(&self) -> Vec<Leg> {
        let mut out = Vec::new();
        for (a, edges) in self.edges.iter().enumerate() {
            for e in edges.iter().filter(|e| e.kind != MoveKind::Walk) {
                let ((x0, y0), (x1, y1)) = (self.point(a), self.point(e.to));
                out.push(Leg {
                    kind: e.kind,
                    x0,
                    y0,
                    x1,
                    y1,
                    cost: e.cost,
                });
            }
        }
        out
    }

    /// Cheapest legs from `start` to `goal`, avoiding the `exclude`d move
    /// kinds; None when either point is off the drawn platforms or no route
    /// exists.
    pub fn route(
        &self,
        start: (f64, f64),
        goal: (f64, f64),
        exclude: &[MoveKind],
    ) -> Option<Vec<Leg>> {
        self.route_jittered(start, goal, 0.0, &mut rand::rng(), exclude)
    }

    /// As [`route`](Self::route), with each edge cost scaled by a random
    /// factor in `1 ± jitter` so near-equal routes vary.
    pub fn route_jittered<R: Rng + ?Sized>(
        &self,
        start: (f64, f64),
        goal: (f64, f64),
        jitter: f64,
        rng: &mut R,
        exclude: &[MoveKind],
    ) -> Option<Vec<Leg>> {
        let sp = self.locate(start.0, start.1)?;
        let gp = self.locate(goal.0, goal.1)?;
        let n = self.nodes.len();
        let (s, g) = (n, n + 1);
        let mut extra: HashMap<usize, Vec<Edge>> = HashMap::new();
        for (k, (pi, x)) in self.nodes.iter().enumerate() {
            if *pi == sp {
                let cost = (x - start.0).abs() / self.walk_speed;
                extra.entry(s).or_default().push(Edge {
                    to: k,
                    kind: MoveKind::Walk,
                    cost,
                });
            }
        }
        for (k, (pi, x)) in self.nodes.iter().enumerate() {
            if *pi == gp {
                let cost = (x - goal.0).abs() / self.walk_speed;
                extra.entry(k).or_default().push(Edge {
                    to: g,
                    kind: MoveKind::Walk,
                    cost,
                });
            }
        }
        if sp == gp {
            let cost = (goal.0 - start.0).abs() / self.walk_speed;
            extra.entry(s).or_default().push(Edge {
                to: g,
                kind: MoveKind::Walk,
                cost,
            });
        }

        let mut dist: HashMap<usize, f64> = HashMap::from([(s, 0.0)]);
        let mut prev: HashMap<usize, (usize, MoveKind, f64)> = HashMap::new();
        let mut heap = BinaryHeap::from([Reverse((Dist(0.0), s))]);
        while let Some(Reverse((Dist(d), u))) = heap.pop() {
            if u == g {
                break;
            }
            if d > dist.get(&u).copied().unwrap_or(f64::INFINITY) {
                continue;
            }
            let base = if u < n { self.edges[u].as_slice() } else { &[] };
            let more = extra.get(&u).map(Vec::as_slice).unwrap_or(&[]);
            for e in base
                .iter()
                .chain(more)
                .filter(|e| !exclude.contains(&e.kind))
            {
                let c = if jitter != 0.0 {
                    e.cost * (1.0 + rng.random_range(-jitter..=jitter))
                } else {
                    e.cost
                };
                let nd = d + c.max(1e-6);
                if nd < dist.get(&e.to).copied().unwrap_or(f64::INFINITY) {
                    dist.insert(e.to, nd);
                    prev.insert(e.to, (u, e.kind, e.cost));
                    heap.push(Reverse((Dist(nd), e.to)));
                }
            }
        }
        if !prev.contains_key(&g) {
            return None;
        }
        // Endpoints keep their given y: anchors float above the row like
        // the player icon, so plans connect anchors, not lines.
        let pos = |u: usize| {
            if u == s {
                start
            } else if u == g {
                goal
            } else {
                self.point(u)
            }
        };
        let mut legs = Vec::new();
        let mut u = g;
        while u != s {
            let (p, kind, cost) = prev[&u];
            let ((x0, y0), (x1, y1)) = (pos(p), pos(u));
            legs.push(Leg {
                kind,
                x0,
                y0,
                x1,
                y1,
                cost,
            });
            u = p;
        }
        legs.reverse();
        Some(merge_walks(legs))
    }

    pub fn route_cost(&self, start: (f64, f64), goal: (f64, f64), exclude: &[MoveKind]) -> f64 {
        self.route(start, goal, exclude)
            .map_or(f64::INFINITY, |legs| legs.iter().map(|l| l.cost).sum())
    }
}

/// A heap key ordering floats totally (Dijkstra's distances).
#[derive(Debug, Clone, Copy, PartialEq)]
struct Dist(f64);

impl Eq for Dist {}

impl PartialOrd for Dist {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Dist {
    fn cmp(&self, other: &Self) -> Ordering {
        self.0.total_cmp(&other.0)
    }
}

/// Drop zero-length legs and join consecutive walks.
fn merge_walks(legs: Vec<Leg>) -> Vec<Leg> {
    let mut out: Vec<Leg> = Vec::new();
    for leg in legs {
        if (leg.x1 - leg.x0).abs() < 0.5 && (leg.y1 - leg.y0).abs() < 0.5 {
            continue;
        }
        match out.last_mut() {
            Some(last) if leg.kind == MoveKind::Walk && last.kind == MoveKind::Walk => {
                last.x1 = leg.x1;
                last.y1 = leg.y1;
                last.cost += leg.cost;
            }
            _ => out.push(leg),
        }
    }
    out
}

/// The movement graph of a map entry's drawn platforms and learned ropes,
/// scaled to a minimap region of `(w, h)` px. None without platforms.
pub fn graph_for(
    entry: &crate::maps::MapEntry,
    region_wh: (f64, f64),
    reach: &ReachModel,
    opts: GraphOptions,
) -> Option<NavGraph> {
    let platforms = entry.platforms.as_ref().filter(|p| !p.is_empty())?;
    let (w, h) = region_wh;
    let scale = |s: &[f64; 4]| [s[0] * w, s[1] * h, s[2] * w, s[3] * h];
    let segs: Vec<[f64; 4]> = platforms.iter().map(scale).collect();
    let ropes: Vec<[f64; 4]> = entry
        .ropes
        .iter()
        .flatten()
        .map(|r| lift_rope_top(&segs, scale(r)))
        .collect();
    Some(NavGraph::new(&segs, &ropes, reach, opts))
}

/// A rope whose top ends a few px under its platform (older learned ropes
/// stopped at the last hang) is lifted onto that platform's row.
pub fn lift_rope_top(platforms: &[[f64; 4]], mut rope: [f64; 4]) -> [f64; 4] {
    let rx = (rope[0] + rope[2]) / 2.0;
    let top = rope[1].min(rope[3]);
    let row = platforms
        .iter()
        .map(|s| Platform::from_segment(*s))
        .filter(|p| p.spans(rx, 3.0))
        .map(|p| p.y_at(rx.clamp(p.x0, p.x1)))
        .filter(|row| (0.0..=ROPE_TOP_SLACK).contains(&(top - row)))
        .max_by(|a, b| a.total_cmp(b));
    if let Some(row) = row {
        let i = if rope[1] <= rope[3] { 1 } else { 3 };
        rope[i] = row;
    }
    rope
}

/// What a cached graph was built from.
#[derive(Debug, Clone, PartialEq)]
struct CacheKey {
    map: String,
    platforms: Vec<[u64; 4]>,
    ropes: Vec<[u64; 4]>,
    rope_penalty: u64,
    walk_speed: u64,
    walk_factor: u64,
    allow_flash: bool,
    allow_double_flash: bool,
    allow_teleport: bool,
    region: (u64, u64),
    reach: Vec<crate::reach::SnapshotRow>,
}

fn bits(segs: Option<&Vec<[f64; 4]>>) -> Vec<[u64; 4]> {
    segs.into_iter()
        .flatten()
        .map(|s| s.map(f64::to_bits))
        .collect()
}

/// One graph, rebuilt only when its map, geometry, region size, kit or
/// learned reach changes. Shared out as an `Arc` so other threads can hold
/// it while the owner rebuilds.
#[derive(Default)]
pub struct GraphCache {
    key: Option<CacheKey>,
    graph: Option<std::sync::Arc<NavGraph>>,
}

impl GraphCache {
    pub fn get(
        &mut self,
        entry: Option<&crate::maps::MapEntry>,
        region_wh: Option<(f64, f64)>,
        reach: &ReachModel,
        opts: GraphOptions,
    ) -> Option<std::sync::Arc<NavGraph>> {
        let (entry, wh) = (entry?, region_wh?);
        entry.platforms.as_ref().filter(|p| !p.is_empty())?;
        let key = CacheKey {
            map: entry.name.clone(),
            platforms: bits(entry.platforms.as_ref()),
            ropes: bits(entry.ropes.as_ref()),
            rope_penalty: opts.rope_penalty.to_bits(),
            walk_speed: opts.walk_speed.to_bits(),
            walk_factor: opts.walk_factor.to_bits(),
            allow_flash: opts.allow_flash,
            allow_double_flash: opts.allow_double_flash,
            allow_teleport: opts.allow_teleport,
            region: (wh.0.to_bits(), wh.1.to_bits()),
            reach: reach.snapshot(),
        };
        if self.key.as_ref() != Some(&key) {
            self.graph = graph_for(entry, wh, reach, opts).map(std::sync::Arc::new);
            self.key = Some(key);
        }
        self.graph.clone()
    }
}
