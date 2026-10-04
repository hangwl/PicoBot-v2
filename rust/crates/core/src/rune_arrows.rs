//! Reading the rune's arrow puzzle from the game window.
//!
//! Activating a rune shows four arrows in a strip near the top of the
//! window; the strip and the arrows move from rune to rune. Each arrow is
//! a solid shape shaded along its length down the hue circle — some
//! green to orange-red, others magenta to cyan, blue to green, or magenta
//! through red to orange (hues are measured around the circle, so a shading
//! may cross red). Its
//! direction is read twice: by its shading (highest-hue end = tail,
//! lowest = tip) and by its silhouette (a wide head narrowing to a tip
//! over a narrower shaft, fitted at 15° steps). They must not disagree;
//! when one can't tell, the other decides — so a new shading still reads
//! by shape. A read is four clear arrows on one row; anything less is a
//! reason, never a guess.
//!
//! [`ArrowWatch`] follows the puzzle over frames: still arrows answer
//! once two frames agree; an arrow whose reads keep changing is spinning,
//! and is answered after `SPIN_WATCH_S` by the direction it read most —
//! a spinning arrow pauses on, or wiggles across, its answer.

use crate::vision::Image;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Arrow {
    Up,
    Down,
    Left,
    Right,
}

impl Arrow {
    /// The key that answers it.
    pub fn key(self) -> &'static str {
        match self {
            Arrow::Up => "up",
            Arrow::Down => "down",
            Arrow::Left => "left",
            Arrow::Right => "right",
        }
    }
}

/// Where the strip can be: (x0, y0, x1, y1) as fractions of the window.
pub const BAND: (f64, f64, f64, f64) = (0.28, 0.22, 0.72, 0.38);
/// Arrow pixels are strongly coloured, from orange round to magenta.
const MIN_SAT: f64 = 0.55;
const MIN_VAL: f64 = 0.6;
const HUE_MIN: f64 = 12.0;
const HUE_MAX: f64 = 330.0;
/// Past green (cyan, blue, magenta) the scenery is coloured too — glows,
/// the strip's own tint — but less purely: arrow pixels there are near
/// full saturation.
const COOL_HUE: f64 = 160.0;
const COOL_MIN_SAT: f64 = 0.85;
const COOL_MIN_VAL: f64 = 0.8;
/// An arrow's shading spans at least this much hue; its tip is the lowest
/// `END_SHARE` of that span, its tail the highest.
const MIN_HUE_SPAN: f64 = 30.0;
const END_SHARE: f64 = 0.3;
/// An arrow is this many pixels (~250-470 at 1366x768, ~100 half hidden
/// behind a monster): stray specks are under, coloured scenery over.
const MIN_PX: usize = 80;
const MAX_PX: usize = 1000;
/// An arrow's box (px each side, ~24-31 at 1366x768) and how far from
/// square it may be: glows and scenery are larger or longer.
const MIN_SIDE: f64 = 10.0;
const MAX_SIDE: f64 = 36.0;
const MAX_ASPECT: f64 = 1.8;
/// Near-pure pixels: an arrow over coloured scenery separates from it at
/// this saturation and value, where the looser rule joins them.
const PURE_SAT: f64 = 0.85;
const PURE_VAL: f64 = 0.8;
/// A pure blob within this many px of a looser arrow is part of it.
const SAME_PX: f64 = 12.0;
/// Each end needs this many pixels, this far apart (px), to give a direction.
const MIN_END_PX: usize = 5;
const MIN_SPLIT_PX: f64 = 3.0;
/// The direction's main axis must beat the other by this much.
const AXIS_RATIO: f64 = 1.5;
/// The silhouette, looked at every `SHAPE_STEP` degrees in `SHAPE_BINS`
/// slices: an arrow's head spans the front `HEAD_SHARE` of it, its shaft
/// `SHAFT_WIDTH` of the head's width, its tip `TIP_WIDTH` (measured on
/// the recorded strips).
const SHAPE_STEP: f64 = 15.0;
const SHAPE_BINS: usize = 12;
const HEAD_SHARE: f64 = 0.55;
const SHAFT_WIDTH: f64 = 0.48;
const TIP_WIDTH: f64 = 0.1;
/// A fit this close is arrow-shaped; the reverse must fit this much worse.
const SHAPE_MAX_ERR: f64 = 0.3;
const SHAPE_MARGIN: f64 = 0.04;
/// An angle this close (degrees) to an axis points along it.
const AXIS_SLACK: f64 = 20.0;
/// The four arrows' centres lie within this many px vertically, and the
/// largest is at most `SIZE_RATIO` times the smallest.
const ROW_SPREAD_PX: f64 = 40.0;
const SIZE_RATIO: f64 = 3.5;
/// How much the size ratio's log weighs against vertical spread (px).
const SIZE_WEIGHT: f64 = 25.0;
/// Only the largest this many clear candidates are tried as the row.
const MAX_CANDIDATES: usize = 12;
/// Pixels this close (px) belong to one arrow.
const BRIDGE: i64 = 2;

/// A clean read: the arrows left to right, and their centres (window px).
#[derive(Debug, Clone, PartialEq)]
pub struct ArrowRead {
    pub arrows: Vec<Arrow>,
    pub centers: Vec<(f64, f64)>,
}

impl ArrowRead {
    pub fn keys(&self) -> Vec<&'static str> {
        self.arrows.iter().map(|a| a.key()).collect()
    }
}

fn hue_sv(bgr: [u8; 3]) -> (f64, f64, f64) {
    let [b, g, r] = bgr.map(|v| v as f64 / 255.0);
    let (mx, mn) = (r.max(g).max(b), r.min(g).min(b));
    if mx <= 0.0 || mx == mn {
        return (0.0, 0.0, mx);
    }
    let d = mx - mn;
    let h = if mx == r {
        60.0 * ((g - b) / d).rem_euclid(6.0)
    } else if mx == g {
        60.0 * ((b - r) / d + 2.0)
    } else {
        60.0 * ((r - g) / d + 4.0)
    };
    (h, d / mx, mx)
}

/// The hue of an arrow-coloured pixel. Red and pink (past `HUE_MAX`, under
/// `HUE_MIN`) are where scenery and glows live, so an arrow shaded through
/// them is taken there only at near-full saturation, like the cool hues.
fn classify(bgr: [u8; 3]) -> Option<f64> {
    let (h, s, v) = hue_sv(bgr);
    let red = !(HUE_MIN..=HUE_MAX).contains(&h);
    let (min_s, min_v) = if red || h > COOL_HUE {
        (COOL_MIN_SAT, COOL_MIN_VAL)
    } else {
        (MIN_SAT, MIN_VAL)
    };
    (s >= min_s && v >= min_v).then_some(h)
}

/// Only near-pure arrow colours.
fn classify_pure(bgr: [u8; 3]) -> Option<f64> {
    let (h, s, v) = hue_sv(bgr);
    (s >= PURE_SAT && v >= PURE_VAL).then_some(h)
}

/// `px`'s hues as offsets (degrees) from their circular mean, so a shading
/// that runs through the red wrap-around stays in order.
fn hue_offsets(px: &[(f64, f64, f64)]) -> Vec<f64> {
    let (sin, cos) = px.iter().fold((0.0, 0.0), |a, p| {
        (a.0 + p.2.to_radians().sin(), a.1 + p.2.to_radians().cos())
    });
    let mean = sin.atan2(cos).to_degrees();
    px.iter()
        .map(|p| (p.2 - mean + 540.0).rem_euclid(360.0) - 180.0)
        .collect()
}

/// One candidate arrow: its pixel count, centre, box, and direction (if
/// clear).
#[derive(Clone)]
struct Candidate {
    px: usize,
    center: (f64, f64),
    size: (f64, f64),
    arrow: Option<Arrow>,
}

impl Candidate {
    fn arrow_shaped(&self) -> bool {
        let (w, h) = self.size;
        (MIN_SIDE..=MAX_SIDE).contains(&w)
            && (MIN_SIDE..=MAX_SIDE).contains(&h)
            && w.max(h) / w.min(h) <= MAX_ASPECT
    }
}

/// The way `px` (x, y, hue) points by its shape and by its shading,
/// when they agree — or the one that can tell, when the other can't.
fn direction(px: &[(f64, f64, f64)]) -> Option<Arrow> {
    match (
        shape_direction(px).and_then(|a| a.axis()),
        colour_direction(px),
    ) {
        (Some(a), Some(b)) if a != b => None,
        (a, b) => a.or(b),
    }
}

/// An arrow's width along its length, back to front (`SHAPE_BINS` bins,
/// widest = 1): a shaft about half as wide as the head, then the head
/// narrowing to its tip over the front `HEAD_SHARE`.
fn arrow_profile(x: f64) -> f64 {
    if x < 1.0 - HEAD_SHARE {
        SHAFT_WIDTH
    } else {
        1.0 - (x - (1.0 - HEAD_SHARE)) / HEAD_SHARE * (1.0 - TIP_WIDTH)
    }
}

/// How far `px`'s width profile looking along `angle` (degrees, screen
/// coordinates: 0 right, 90 down) is from an arrow's (mean abs error).
fn shape_error(px: &[(f64, f64, f64)], angle: f64) -> f64 {
    let (sin, cos) = angle.to_radians().sin_cos();
    let along: Vec<(f64, f64)> = px
        .iter()
        .map(|p| (p.0 * cos + p.1 * sin, -p.0 * sin + p.1 * cos))
        .collect();
    let (t0, t1, s0, s1) = along
        .iter()
        .fold((f64::MAX, f64::MIN, f64::MAX, f64::MIN), |a, p| {
            (a.0.min(p.0), a.1.max(p.0), a.2.min(p.1), a.3.max(p.1))
        });
    let (len, wid) = (t1 - t0 + 1.0, s1 - s0 + 1.0);
    // The blob on a grid laid along `angle`, each cell on when it's mostly
    // filled, against an ideal arrow of the same length and width.
    let n = SHAPE_BINS;
    let mut fill = vec![0.0; n * n];
    for &(t, s) in &along {
        let i = (((t - t0) / len * n as f64) as usize).min(n - 1);
        let j = (((s - s0) / wid * n as f64) as usize).min(n - 1);
        fill[i * n + j] += 1.0;
    }
    let cell = len * wid / (n * n) as f64;
    let wrong = (0..n * n)
        .filter(|&k| {
            let (x, y) = ((k / n) as f64 + 0.5, (k % n) as f64 + 0.5);
            let half = arrow_profile(x / n as f64) / 2.0;
            let inside = (y / n as f64 - 0.5).abs() <= half;
            inside != (fill[k] >= cell * 0.5)
        })
        .count();
    wrong as f64 / (n * n) as f64
}

/// The angle (degrees, a multiple of `SHAPE_STEP`) `px` points at by its
/// silhouette: the best fit to an arrow's profile, clearly better than the
/// reverse (head and tail told apart); None for a shape that's no arrow.
fn shape_direction(px: &[(f64, f64, f64)]) -> Option<Angle> {
    let solid = opened(px);
    if solid.len() < MIN_PX / 2 {
        return None;
    }
    let steps = (360.0 / SHAPE_STEP) as usize;
    let err: Vec<f64> = (0..steps)
        .map(|k| shape_error(&solid, k as f64 * SHAPE_STEP))
        .collect();
    let best = (0..steps).min_by(|&a, &b| err[a].total_cmp(&err[b]))?;
    let reverse = err[(best + steps / 2) % steps];
    (err[best] <= SHAPE_MAX_ERR && reverse - err[best] >= SHAPE_MARGIN)
        .then_some(Angle(best as f64 * SHAPE_STEP))
}

/// `px` without its specks and one-pixel fringes (a morphological
/// opening: shrink by a pixel, grow back): the sparkle around an arrow
/// and its antialiased edge would blur its silhouette.
fn opened(px: &[(f64, f64, f64)]) -> Vec<(f64, f64, f64)> {
    let (x0, y0) = px
        .iter()
        .fold((f64::MAX, f64::MAX), |a, p| (a.0.min(p.0), a.1.min(p.1)));
    let (w, h) = px.iter().fold((0usize, 0usize), |a, p| {
        (
            a.0.max((p.0 - x0) as usize + 1),
            a.1.max((p.1 - y0) as usize + 1),
        )
    });
    let mut on = vec![false; w * h];
    for p in px {
        on[(p.1 - y0) as usize * w + (p.0 - x0) as usize] = true;
    }
    let at = |g: &[bool], x: i64, y: i64| {
        x >= 0 && y >= 0 && (x as usize) < w && (y as usize) < h && g[y as usize * w + x as usize]
    };
    let ring = |g: &[bool], x: i64, y: i64, all: bool| {
        let mut n = (-1..=1).flat_map(|dy| (-1..=1).map(move |dx| (dx, dy)));
        if all {
            n.all(|(dx, dy)| at(g, x + dx, y + dy))
        } else {
            n.any(|(dx, dy)| at(g, x + dx, y + dy))
        }
    };
    let core: Vec<bool> = (0..w * h)
        .map(|i| ring(&on, (i % w) as i64, (i / w) as i64, true))
        .collect();
    px.iter()
        .filter(|p| ring(&core, (p.0 - x0) as i64, (p.1 - y0) as i64, false))
        .copied()
        .collect()
}

/// Which way an arrow faces, in degrees (screen coordinates: 0 right, 90
/// down, 180 left, 270 up).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Angle(pub f64);

impl Angle {
    /// The key it stands for, when it's within `AXIS_SLACK` of one.
    pub fn axis(self) -> Option<Arrow> {
        let a = self.0.rem_euclid(360.0);
        [
            (0.0, Arrow::Right),
            (90.0, Arrow::Down),
            (180.0, Arrow::Left),
            (270.0, Arrow::Up),
            (360.0, Arrow::Right),
        ]
        .into_iter()
        .find(|(at, _)| (a - at).abs() <= AXIS_SLACK)
        .map(|(_, k)| k)
    }
}

/// The way `px` (x, y, hue) points by its shading: from its high-hue end
/// to its low.
fn colour_direction(px: &[(f64, f64, f64)]) -> Option<Arrow> {
    let offs = hue_offsets(px);
    let mut hues = offs.clone();
    hues.sort_by(f64::total_cmp);
    // Percentiles, so a few stray pixels don't stretch the span.
    let (lo, hi) = (hues[hues.len() / 20], hues[hues.len() * 19 / 20]);
    let span = hi - lo;
    if span < MIN_HUE_SPAN {
        return None;
    }
    let end = |keep: &dyn Fn(f64) -> bool| {
        px.iter()
            .zip(&offs)
            .filter(|(_, h)| keep(**h))
            .fold((0.0, 0.0, 0usize), |a, (p, _)| {
                (a.0 + p.0, a.1 + p.1, a.2 + 1)
            })
    };
    let tip = end(&|h| h <= lo + span * END_SHARE);
    let tail = end(&|h| h >= hi - span * END_SHARE);
    if tip.2 < MIN_END_PX || tail.2 < MIN_END_PX {
        return None;
    }
    let (dx, dy) = (
        tip.0 / tip.2 as f64 - tail.0 / tail.2 as f64,
        tip.1 / tip.2 as f64 - tail.1 / tail.2 as f64,
    );
    if dx.hypot(dy) < MIN_SPLIT_PX {
        return None;
    }
    if dx.abs() >= AXIS_RATIO * dy.abs() {
        Some(if dx > 0.0 { Arrow::Right } else { Arrow::Left })
    } else if dy.abs() >= AXIS_RATIO * dx.abs() {
        Some(if dy > 0.0 { Arrow::Down } else { Arrow::Up })
    } else {
        None
    }
}

/// Group the classified pixels into candidates (`BRIDGE` px joins them).
fn candidates(mask: &[Option<f64>], w: usize, h: usize, off: (usize, usize)) -> Vec<Candidate> {
    let mut seen = vec![false; mask.len()];
    let mut out = Vec::new();
    let mut stack = Vec::new();
    for start in 0..mask.len() {
        if mask[start].is_none() || seen[start] {
            continue;
        }
        seen[start] = true;
        stack.push(start);
        let mut px: Vec<(f64, f64, f64)> = Vec::new();
        while let Some(i) = stack.pop() {
            let (x, y) = ((i % w) as i64, (i / w) as i64);
            px.push((x as f64, y as f64, mask[i].unwrap_or(0.0)));
            for dy in -BRIDGE..=BRIDGE {
                for dx in -BRIDGE..=BRIDGE {
                    let (nx, ny) = (x + dx, y + dy);
                    if nx < 0 || ny < 0 || nx >= w as i64 || ny >= h as i64 {
                        continue;
                    }
                    let j = ny as usize * w + nx as usize;
                    if mask[j].is_some() && !seen[j] {
                        seen[j] = true;
                        stack.push(j);
                    }
                }
            }
        }
        let n = px.len();
        if (MIN_PX..=MAX_PX).contains(&n) {
            let (sx, sy) = px.iter().fold((0.0, 0.0), |a, p| (a.0 + p.0, a.1 + p.1));
            let (x0, x1, y0, y1) = px
                .iter()
                .fold((f64::MAX, f64::MIN, f64::MAX, f64::MIN), |a, p| {
                    (a.0.min(p.0), a.1.max(p.0), a.2.min(p.1), a.3.max(p.1))
                });
            out.push(Candidate {
                px: n,
                center: (sx / n as f64 + off.0 as f64, sy / n as f64 + off.1 as f64),
                size: (x1 - x0 + 1.0, y1 - y0 + 1.0),
                arrow: direction(&px),
            });
        }
    }
    out
}

/// The four candidates that best form the strip: within `ROW_SPREAD_PX`
/// vertically and `SIZE_RATIO` in size, the tightest row of the most
/// even sizes first (a glow beside the strip can sit level with it, but
/// it's rarely an arrow's size).
fn best_row(c: Vec<Candidate>) -> Option<Vec<Candidate>> {
    let n = c.len();
    let mut best: Option<([usize; 4], f64)> = None;
    for a in 0..n {
        for b in a + 1..n {
            for d in b + 1..n {
                for e in d + 1..n {
                    let pick = [a, b, d, e];
                    let ys = pick.map(|i| c[i].center.1);
                    let px = pick.map(|i| c[i].px as f64);
                    let spread = ys.iter().cloned().fold(f64::MIN, f64::max)
                        - ys.iter().cloned().fold(f64::MAX, f64::min);
                    let ratio = px.iter().cloned().fold(f64::MIN, f64::max)
                        / px.iter().cloned().fold(f64::MAX, f64::min);
                    if spread <= ROW_SPREAD_PX
                        && ratio <= SIZE_RATIO
                        && best.is_none_or(|(_, s)| spread + SIZE_WEIGHT * ratio.ln() < s)
                    {
                        best = Some((pick, spread + SIZE_WEIGHT * ratio.ln()));
                    }
                }
            }
        }
    }
    let (pick, _) = best?;
    let mut c: Vec<Option<Candidate>> = c.into_iter().map(Some).collect();
    Some(pick.iter().filter_map(|&i| c[i].take()).collect())
}

/// Every arrow-shaped blob in `window` (the whole client area), its
/// direction if it reads clearly: a static arrow, or a spinning one caught
/// near an axis.
fn sightings(window: &Image) -> Result<Vec<Candidate>, String> {
    let (w, h) = (window.width as f64, window.height as f64);
    let (bx0, by0) = ((BAND.0 * w) as usize, (BAND.1 * h) as usize);
    let (bx1, by1) = (
        ((BAND.2 * w) as usize).min(window.width),
        ((BAND.3 * h) as usize).min(window.height),
    );
    if bx1 <= bx0 || by1 <= by0 {
        return Err("window too small".into());
    }
    let (cw, ch) = (bx1 - bx0, by1 - by0);
    let mask = |f: fn([u8; 3]) -> Option<f64>| -> Vec<Option<f64>> {
        (by0..by1)
            .flat_map(|y| (bx0..bx1).map(move |x| (x, y)))
            .map(|(x, y)| f(window.bgr(x, y)))
            .collect()
    };
    // The looser rule finds whole arrows, dimmed ones too; where it ran an
    // arrow into coloured scenery, the pure pixels alone still make one.
    let mut found: Vec<Candidate> = candidates(&mask(classify), cw, ch, (bx0, by0))
        .into_iter()
        .filter(Candidate::arrow_shaped)
        .collect();
    for c in candidates(&mask(classify_pure), cw, ch, (bx0, by0)) {
        if !c.arrow_shaped() {
            continue;
        }
        let near =
            |o: &Candidate| (o.center.0 - c.center.0).hypot(o.center.1 - c.center.1) < SAME_PX;
        match found.iter().position(near) {
            Some(k) if found[k].arrow.is_none() && c.arrow.is_some() => found[k] = c,
            Some(_) => {}
            None => found.push(c),
        }
    }
    Ok(found)
}

/// Every candidate blob the reader considers in `window`, one line each:
/// pixel count, box, centre, whether it is arrow-shaped, and the direction
/// it reads — for working out why a strip didn't read.
pub fn diagnose(window: &Image) -> Vec<String> {
    let (w, h) = (window.width as f64, window.height as f64);
    let (bx0, by0) = ((BAND.0 * w) as usize, (BAND.1 * h) as usize);
    let (bx1, by1) = (
        ((BAND.2 * w) as usize).min(window.width),
        ((BAND.3 * h) as usize).min(window.height),
    );
    if bx1 <= bx0 || by1 <= by0 {
        return vec!["window too small".into()];
    }
    let (cw, ch) = (bx1 - bx0, by1 - by0);
    let mut out = Vec::new();
    for (name, f) in [
        ("loose", classify as fn([u8; 3]) -> Option<f64>),
        ("pure", classify_pure),
    ] {
        let mask: Vec<Option<f64>> = (by0..by1)
            .flat_map(|y| (bx0..bx1).map(move |x| (x, y)))
            .map(|(x, y)| f(window.bgr(x, y)))
            .collect();
        let lit = mask.iter().filter(|m| m.is_some()).count();
        out.push(format!(
            "{name}: {lit} px in the band {bx0},{by0}..{bx1},{by1}"
        ));
        for c in candidates(&mask, cw, ch, (bx0, by0)) {
            out.push(format!(
                "  {name} blob {} px, box {:.0}x{:.0}, at ({:.0},{:.0}), arrow-shaped {}, reads {:?}",
                c.px,
                c.size.0,
                c.size.1,
                c.center.0,
                c.center.1,
                c.arrow_shaped(),
                c.arrow
            ));
        }
    }
    out
}

/// The four arrows in `window` (the whole client area), left to right; or
/// why they can't be read. One frame: static arrows only.
pub fn read_arrows(window: &Image) -> Result<ArrowRead, String> {
    let mut clear: Vec<Candidate> = sightings(window)?
        .into_iter()
        .filter(|c| c.arrow.is_some())
        .collect();
    if clear.len() < 4 {
        return Err(format!("found {} clear arrows", clear.len()));
    }
    clear.sort_by_key(|c| std::cmp::Reverse(c.px));
    clear.truncate(MAX_CANDIDATES);
    let Some(mut found) = best_row(clear) else {
        return Err("no four arrows of a size on one row".into());
    };
    found.sort_by(|a, b| a.center.0.total_cmp(&b.center.0));
    Ok(ArrowRead {
        arrows: found.iter().filter_map(|c| c.arrow).collect(),
        centers: found.iter().map(|c| c.center).collect(),
    })
}

/// One arrow's place in the strip, followed across frames.
#[derive(Debug, Clone)]
struct Slot {
    center: (f64, f64),
    px: f64,
    seen: usize,
    /// Frames it read up, down, left, right (`ARROWS` order); frames it
    /// showed but didn't read.
    votes: [usize; 4],
    unclear: usize,
}

impl Slot {
    fn clear(&self) -> usize {
        self.votes.iter().sum()
    }

    /// Its most frequent direction and that direction's share of the reads.
    fn mode(&self) -> Option<(Arrow, f64)> {
        let (k, &n) = self.votes.iter().enumerate().max_by_key(|(_, n)| **n)?;
        (n > 0).then(|| (ARROWS[k], n as f64 / self.clear() as f64))
    }

    /// Reads one way, frame after frame: a static arrow.
    fn steady(&self) -> bool {
        self.mode().is_some_and(|(_, share)| share >= STEADY_SHARE)
            && self.unclear as f64 <= self.seen as f64 * (1.0 - STEADY_SHARE)
    }
}

const ARROWS: [Arrow; 4] = [Arrow::Up, Arrow::Down, Arrow::Left, Arrow::Right];
/// A slot reading one direction in this share of frames is static.
const STEADY_SHARE: f64 = 0.85;
/// A spinning arrow is watched this long (s): it lingers on its answer —
/// pauses there or wiggles back across it — so the direction it reads
/// most often is the one to press.
pub const SPIN_WATCH_S: f64 = 1.5;

/// What the frames so far say.
#[derive(Debug, Clone, PartialEq)]
pub enum Watch {
    /// Not sure yet: look again.
    Wait,
    /// The answer, and which arrows were spinning.
    Read(ArrowRead, Vec<bool>),
    /// No four arrows to answer.
    Fail(String),
}

/// The puzzle over successive frames: each arrow's place and the
/// directions it read in. Static arrows answer once two frames agree; a
/// spinning arrow is answered by where it lingers.
#[derive(Debug, Clone, Default)]
pub struct ArrowWatch {
    slots: Vec<Slot>,
    frames: usize,
    since: Option<f64>,
    last_err: Option<String>,
}

impl ArrowWatch {
    pub fn add(&mut self, window: &Image, now: f64) {
        self.since.get_or_insert(now);
        let found = match sightings(window) {
            Ok(f) => f,
            Err(e) => {
                self.last_err = Some(e);
                return;
            }
        };
        self.frames += 1;
        for c in found {
            let near = self
                .slots
                .iter()
                .position(|s| (s.center.0 - c.center.0).hypot(s.center.1 - c.center.1) < SAME_PX);
            let k = near.unwrap_or_else(|| {
                self.slots.push(Slot {
                    center: c.center,
                    px: 0.0,
                    seen: 0,
                    votes: [0; 4],
                    unclear: 0,
                });
                self.slots.len() - 1
            });
            let s = &mut self.slots[k];
            let n = s.seen as f64;
            s.center = (
                (s.center.0 * n + c.center.0) / (n + 1.0),
                (s.center.1 * n + c.center.1) / (n + 1.0),
            );
            s.px = (s.px * n + c.px as f64) / (n + 1.0);
            s.seen += 1;
            match c.arrow.and_then(|a| ARROWS.iter().position(|&b| b == a)) {
                Some(i) => s.votes[i] += 1,
                None => s.unclear += 1,
            }
        }
    }

    /// The verdict at `now`; `last` when no more frames will come.
    pub fn verdict(&self, now: f64, last: bool) -> Watch {
        let need = if last { 1 } else { 2 };
        let lasting: Vec<(usize, Candidate)> = self
            .slots
            .iter()
            .enumerate()
            .filter(|(_, s)| s.seen >= need.max(self.frames / 2) && s.clear() > 0)
            .map(|(k, s)| {
                let c = Candidate {
                    px: s.px as usize,
                    center: s.center,
                    size: (0.0, 0.0),
                    arrow: s.mode().map(|m| m.0),
                };
                (k, c)
            })
            .collect();
        let pick = |c: &Candidate| lasting.iter().find(|l| l.1.center == c.center).map(|l| l.0);
        let row = if lasting.len() >= 4 {
            best_row(lasting.iter().map(|l| l.1.clone()).collect())
        } else {
            None
        };
        let Some(mut row) = row else {
            return if last {
                let why = self.last_err.clone();
                Watch::Fail(why.unwrap_or_else(|| "no four arrows on one row".into()))
            } else {
                Watch::Wait
            };
        };
        row.sort_by(|a, b| a.center.0.total_cmp(&b.center.0));
        let slots: Vec<&Slot> = row
            .iter()
            .filter_map(pick)
            .map(|k| &self.slots[k])
            .collect();
        let spinning: Vec<bool> = slots.iter().map(|s| !s.steady()).collect();
        let watched = self.since.map_or(0.0, |t| now - t);
        let settled = if spinning.iter().any(|&x| x) {
            watched >= SPIN_WATCH_S
        } else {
            slots.iter().all(|s| s.clear() >= need)
        };
        if !settled && !last {
            return Watch::Wait;
        }
        Watch::Read(
            ArrowRead {
                arrows: row.iter().filter_map(|c| c.arrow).collect(),
                centers: row.iter().map(|c| c.center).collect(),
            },
            spinning,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A solid arrow-sized block at `c` (window px) pointing `to`, shaded
    /// tail→tip.
    fn paint(img: &mut Image, c: (usize, usize), to: Arrow) {
        for i in 0..24usize {
            // Along the arrow: 0 tail (green) .. 23 tip (orange-red).
            let t = i as f64 / 23.0;
            let bgr = [0u8, (255.0 - 170.0 * t) as u8, (80.0 + 175.0 * t) as u8];
            for j in 0..18usize {
                let (a, b) = (i as i64 - 12, j as i64 - 9);
                let (x, y) = match to {
                    Arrow::Right => (a, b),
                    Arrow::Left => (-a, b),
                    Arrow::Down => (b, a),
                    Arrow::Up => (b, -a),
                };
                img.set_bgr((c.0 as i64 + x) as usize, (c.1 as i64 + y) as usize, bgr);
            }
        }
    }

    fn window() -> Image {
        let mut img = Image::new(1366, 768);
        for y in 0..768 {
            for x in 0..1366 {
                img.set_bgr(x, y, [190, 160, 160]); // a pale blue-grey backdrop
            }
        }
        img
    }

    /// The block of `paint` turned to `deg` (0 right, 90 down), on a
    /// cleared patch so frames can repaint it.
    fn paint_at(img: &mut Image, c: (usize, usize), deg: f64) {
        let (sin, cos) = deg.to_radians().sin_cos();
        for y in c.1 - 20..c.1 + 20 {
            for x in c.0 - 20..c.0 + 20 {
                let (dx, dy) = (x as f64 - c.0 as f64, y as f64 - c.1 as f64);
                let (a, b) = (dx * cos + dy * sin, -dx * sin + dy * cos);
                let bgr = if a.abs() < 12.0 && b.abs() < 9.0 {
                    let t = (a + 12.0) / 24.0;
                    [0u8, (255.0 - 170.0 * t) as u8, (80.0 + 175.0 * t) as u8]
                } else {
                    [190, 160, 160]
                };
                img.set_bgr(x, y, bgr);
            }
        }
    }

    /// Frames 0.05s apart: arrows 0, 1 and 3 still (right, up, left), arrow
    /// 2 at `spin(frame)` degrees.
    fn watch_frames(n: usize, spin: impl Fn(usize) -> f64) -> (ArrowWatch, f64) {
        let mut w = ArrowWatch::default();
        let mut img = window();
        let mut t = 0.0;
        for f in 0..n {
            t = f as f64 * 0.05;
            for (i, deg) in [0.0, 270.0, spin(f), 180.0].into_iter().enumerate() {
                paint_at(&mut img, (520 + i * 100, 210), deg);
            }
            w.add(&img, t);
        }
        (w, t)
    }

    #[test]
    fn still_arrows_answer_once_two_frames_agree() {
        let (w, t) = watch_frames(1, |_| 90.0);
        assert_eq!(w.verdict(t, false), Watch::Wait);
        let (w, t) = watch_frames(2, |_| 90.0);
        let Watch::Read(r, spinning) = w.verdict(t, false) else {
            panic!("{:?}", w.verdict(t, false));
        };
        assert_eq!(
            r.arrows,
            [Arrow::Right, Arrow::Up, Arrow::Down, Arrow::Left]
        );
        assert_eq!(spinning, [false; 4]);
    }

    #[test]
    fn a_spinning_arrow_is_answered_where_it_pauses() {
        // 30 degrees a frame, pausing 4 frames at down (90) each turn.
        let spin = |f: usize| {
            let k = f % 16;
            if (3..7).contains(&k) {
                90.0
            } else {
                (if k < 3 { k } else { k - 3 }) as f64 * 30.0
            }
        };
        let (w, t) = watch_frames(10, spin);
        assert_eq!(w.verdict(t, false), Watch::Wait, "watched {t}s");
        let (w, t) = watch_frames(32, spin);
        let Watch::Read(r, spinning) = w.verdict(t, false) else {
            panic!("{:?}", w.verdict(t, false));
        };
        assert_eq!(
            r.arrows,
            [Arrow::Right, Arrow::Up, Arrow::Down, Arrow::Left]
        );
        assert_eq!(spinning, [false, false, true, false]);
    }

    #[test]
    fn a_wiggling_arrow_is_answered_by_the_way_it_wiggles_across() {
        // Spinning, then rocking back and forth across left (180).
        let rock = [120.0, 150.0, 180.0, 210.0, 180.0, 150.0, 180.0, 210.0];
        let spin = |f: usize| {
            if f < 12 {
                f as f64 * 30.0
            } else {
                rock[f % rock.len()]
            }
        };
        let (w, t) = watch_frames(34, spin);
        let Watch::Read(r, spinning) = w.verdict(t, false) else {
            panic!("{:?}", w.verdict(t, false));
        };
        assert_eq!(r.arrows[2], Arrow::Left);
        assert!(spinning[2]);
    }

    #[test]
    fn the_last_look_answers_what_it_has() {
        let (w, t) = watch_frames(1, |_| 90.0);
        assert!(matches!(w.verdict(t, true), Watch::Read(..)));
        assert!(matches!(
            ArrowWatch::default().verdict(0.0, true),
            Watch::Fail(_)
        ));
    }

    #[test]
    fn four_painted_arrows_read_left_to_right() {
        let mut img = window();
        let want = [Arrow::Left, Arrow::Up, Arrow::Down, Arrow::Right];
        for (i, a) in want.iter().enumerate() {
            paint(&mut img, (520 + i * 100, 210), *a);
        }
        let read = read_arrows(&img).expect("read");
        assert_eq!(read.arrows, want);
    }

    #[test]
    fn fewer_than_four_is_not_a_read() {
        let mut img = window();
        paint(&mut img, (520, 210), Arrow::Up);
        paint(&mut img, (620, 210), Arrow::Up);
        assert_eq!(read_arrows(&img).unwrap_err(), "found 2 clear arrows");
        assert!(read_arrows(&window()).is_err());
    }
}
