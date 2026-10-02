//! Reading the rune's arrow puzzle from the game window.
//!
//! Activating a rune shows four arrows in a strip near the top of the
//! window; the strip and the arrows move from rune to rune. Each arrow is
//! a solid shape shaded green at its tail through yellow to orange-red at
//! its tip, so its direction is the line from its green end to its red
//! end. A read is accepted only when four clean arrows sit on one row;
//! anything less is a reason, never a guess.

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
/// Arrow pixels are strongly coloured, between orange and green.
const MIN_SAT: f64 = 0.55;
const MIN_VAL: f64 = 0.6;
const HUE_MIN: f64 = 12.0;
const HUE_MAX: f64 = 160.0;
/// Tip pixels are below this hue (orange-red); tail pixels above `TAIL_HUE`.
const TIP_HUE: f64 = 45.0;
const TAIL_HUE: f64 = 95.0;
/// An arrow is this many pixels (~250-470 at 1366x768): stray specks are
/// well under, coloured scenery well over.
const MIN_PX: usize = 150;
const MAX_PX: usize = 1000;
/// Each end needs this many pixels, this far apart (px), to give a direction.
const MIN_END_PX: usize = 5;
const MIN_SPLIT_PX: f64 = 3.0;
/// The direction's main axis must beat the other by this much.
const AXIS_RATIO: f64 = 1.5;
/// The four arrows' centres lie within this many px vertically, and the
/// largest is at most `SIZE_RATIO` times the smallest.
const ROW_SPREAD_PX: f64 = 40.0;
const SIZE_RATIO: f64 = 2.5;
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

#[derive(Clone, Copy, PartialEq)]
enum Px {
    None,
    Tip,
    Mid,
    Tail,
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

fn classify(bgr: [u8; 3]) -> Px {
    let (h, s, v) = hue_sv(bgr);
    if s < MIN_SAT || v < MIN_VAL || !(HUE_MIN..=HUE_MAX).contains(&h) {
        Px::None
    } else if h < TIP_HUE {
        Px::Tip
    } else if h > TAIL_HUE {
        Px::Tail
    } else {
        Px::Mid
    }
}

/// One candidate arrow: its pixel count, centre, and direction (if clear).
struct Candidate {
    px: usize,
    center: (f64, f64),
    arrow: Option<Arrow>,
}

fn direction(tip: (f64, f64, usize), tail: (f64, f64, usize)) -> Option<Arrow> {
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
fn candidates(mask: &[Px], w: usize, h: usize, off: (usize, usize)) -> Vec<Candidate> {
    let mut seen = vec![false; mask.len()];
    let mut out = Vec::new();
    let mut stack = Vec::new();
    for start in 0..mask.len() {
        if mask[start] == Px::None || seen[start] {
            continue;
        }
        seen[start] = true;
        stack.push(start);
        let (mut n, mut sx, mut sy) = (0usize, 0.0, 0.0);
        let (mut tip, mut tail) = ((0.0, 0.0, 0usize), (0.0, 0.0, 0usize));
        while let Some(i) = stack.pop() {
            let (x, y) = ((i % w) as i64, (i / w) as i64);
            n += 1;
            sx += x as f64;
            sy += y as f64;
            match mask[i] {
                Px::Tip => tip = (tip.0 + x as f64, tip.1 + y as f64, tip.2 + 1),
                Px::Tail => tail = (tail.0 + x as f64, tail.1 + y as f64, tail.2 + 1),
                _ => {}
            }
            for dy in -BRIDGE..=BRIDGE {
                for dx in -BRIDGE..=BRIDGE {
                    let (nx, ny) = (x + dx, y + dy);
                    if nx < 0 || ny < 0 || nx >= w as i64 || ny >= h as i64 {
                        continue;
                    }
                    let j = ny as usize * w + nx as usize;
                    if mask[j] != Px::None && !seen[j] {
                        seen[j] = true;
                        stack.push(j);
                    }
                }
            }
        }
        if (MIN_PX..=MAX_PX).contains(&n) {
            out.push(Candidate {
                px: n,
                center: (sx / n as f64 + off.0 as f64, sy / n as f64 + off.1 as f64),
                arrow: direction(tip, tail),
            });
        }
    }
    out
}

/// The four candidates that best form the strip: within `ROW_SPREAD_PX`
/// vertically and `SIZE_RATIO` in size, the tightest row first.
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
                        && best.is_none_or(|(_, s)| spread < s)
                    {
                        best = Some((pick, spread));
                    }
                }
            }
        }
    }
    let (pick, _) = best?;
    let mut c: Vec<Option<Candidate>> = c.into_iter().map(Some).collect();
    Some(pick.iter().filter_map(|&i| c[i].take()).collect())
}

/// The four arrows in `window` (the whole client area), left to right; or
/// why they can't be read.
pub fn read_arrows(window: &Image) -> Result<ArrowRead, String> {
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
    let mut mask = Vec::with_capacity(cw * ch);
    for y in by0..by1 {
        for x in bx0..bx1 {
            mask.push(classify(window.bgr(x, y)));
        }
    }
    let all = candidates(&mask, cw, ch, (bx0, by0));
    let mut clear: Vec<Candidate> = all.into_iter().filter(|c| c.arrow.is_some()).collect();
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

#[cfg(test)]
mod tests {
    use super::*;

    /// A solid arrow at `c` (window px) pointing `to`, shaded tail→tip.
    fn paint(img: &mut Image, c: (usize, usize), to: Arrow) {
        for i in 0..24usize {
            // Along the arrow: 0 tail (green) .. 23 tip (orange-red).
            let t = i as f64 / 23.0;
            let bgr = [0u8, (255.0 - 170.0 * t) as u8, (80.0 + 175.0 * t) as u8];
            for j in 0..12usize {
                let (a, b) = (i as i64 - 12, j as i64 - 6);
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
