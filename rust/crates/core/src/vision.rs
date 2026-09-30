//! Image algorithms on minimap and window captures — no OS dependencies.
//!
//! Captures are BGRA (what Windows' GDI returns), 4 bytes per pixel,
//! top-down. Colours are given as BGR, like the config's
//! `minimap_colors`.

/// A captured image: `width × height` BGRA pixels, rows top to bottom.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Image {
    pub width: usize,
    pub height: usize,
    pub bgra: Vec<u8>,
}

/// `(x, y, w, h)` in px.
pub type Region = (i32, i32, i32, i32);

impl Image {
    pub fn new(width: usize, height: usize) -> Self {
        Image {
            width,
            height,
            bgra: vec![0; width * height * 4],
        }
    }

    /// From tightly packed RGB rows (e.g. a decoded PNG).
    pub fn from_rgb(width: usize, height: usize, rgb: &[u8]) -> Self {
        let mut bgra = Vec::with_capacity(width * height * 4);
        for px in rgb.as_chunks::<3>().0 {
            bgra.extend_from_slice(&[px[2], px[1], px[0], 255]);
        }
        Image {
            width,
            height,
            bgra,
        }
    }

    /// BGR of pixel `(x, y)`.
    #[inline]
    pub fn bgr(&self, x: usize, y: usize) -> [u8; 3] {
        let i = (y * self.width + x) * 4;
        [self.bgra[i], self.bgra[i + 1], self.bgra[i + 2]]
    }

    pub fn set_bgr(&mut self, x: usize, y: usize, c: [u8; 3]) {
        let i = (y * self.width + x) * 4;
        self.bgra[i..i + 4].copy_from_slice(&[c[0], c[1], c[2], 255]);
    }

    /// The `(x, y, w, h)` part of the image, clamped to its bounds.
    /// Bilinear resize with pixel-centre alignment (OpenCV's
    /// `INTER_LINEAR`).
    pub fn resize(&self, w: usize, h: usize) -> Image {
        let mut out = Image::new(w, h);
        if self.width == 0 || self.height == 0 {
            return out;
        }
        let axis = |dst: usize, src_len: usize, dst_len: usize| {
            let f = ((dst as f64 + 0.5) * src_len as f64 / dst_len as f64 - 0.5).max(0.0);
            let i = (f.floor() as usize).min(src_len - 1);
            let j = (i + 1).min(src_len - 1);
            (i, j, f - i as f64)
        };
        for y in 0..h {
            let (y0, y1, fy) = axis(y, self.height, h);
            for x in 0..w {
                let (x0, x1, fx) = axis(x, self.width, w);
                let (a, b, c, d) = (
                    self.bgr(x0, y0),
                    self.bgr(x1, y0),
                    self.bgr(x0, y1),
                    self.bgr(x1, y1),
                );
                let px = std::array::from_fn(|k| {
                    let top = a[k] as f64 * (1.0 - fx) + b[k] as f64 * fx;
                    let bot = c[k] as f64 * (1.0 - fx) + d[k] as f64 * fx;
                    (top * (1.0 - fy) + bot * fy).round().clamp(0.0, 255.0) as u8
                });
                out.set_bgr(x, y, px);
            }
        }
        out
    }

    pub fn crop(&self, x: usize, y: usize, w: usize, h: usize) -> Image {
        let x1 = (x + w).min(self.width);
        let y1 = (y + h).min(self.height);
        let (x, y) = (x.min(x1), y.min(y1));
        let (cw, ch) = (x1 - x, y1 - y);
        let mut out = Vec::with_capacity(cw * ch * 4);
        for row in y..y1 {
            let start = (row * self.width + x) * 4;
            out.extend_from_slice(&self.bgra[start..start + cw * 4]);
        }
        Image {
            width: cw,
            height: ch,
            bgra: out,
        }
    }
}

/// A boolean mask over an image.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Mask {
    pub width: usize,
    pub height: usize,
    pub bits: Vec<bool>,
}

impl Mask {
    #[inline]
    pub fn get(&self, x: usize, y: usize) -> bool {
        self.bits[y * self.width + x]
    }

    pub fn count(&self) -> usize {
        self.bits.iter().filter(|b| **b).count()
    }
}

/// Pixels whose summed per-channel distance to `bgr` is below `tolerance * 3`.
pub fn color_mask(img: &Image, bgr: [u8; 3], tolerance: i32) -> Mask {
    let limit = tolerance * 3;
    let (tb, tg, tr) = (bgr[0] as i32, bgr[1] as i32, bgr[2] as i32);
    let bits = img
        .bgra
        .as_chunks::<4>()
        .0
        .iter()
        .map(|p| {
            (p[0] as i32 - tb).abs() + (p[1] as i32 - tg).abs() + (p[2] as i32 - tr).abs() < limit
        })
        .collect();
    Mask {
        width: img.width,
        height: img.height,
        bits,
    }
}

/// True when (nearly) every pixel is black: the 99th percentile of every
/// other row and column (all three channels) is at most `level`. Linear
/// interpolation between ranks, like numpy's default.
pub fn is_dark(img: &Image, level: f64) -> bool {
    if img.width == 0 || img.height == 0 {
        return false;
    }
    let mut hist = [0u64; 256];
    let mut n = 0u64;
    for y in (0..img.height).step_by(2) {
        for x in (0..img.width).step_by(2) {
            for c in img.bgr(x, y) {
                hist[c as usize] += 1;
                n += 1;
            }
        }
    }
    let pos = 0.99 * (n - 1) as f64;
    let (lo, frac) = (pos.floor() as u64, pos - pos.floor());
    let nth = |k: u64| -> f64 {
        let mut seen = 0;
        for (v, c) in hist.iter().enumerate() {
            seen += c;
            if seen > k {
                return v as f64;
            }
        }
        255.0
    };
    let a = nth(lo);
    let b = if frac > 0.0 { nth(lo + 1) } else { a };
    a + frac * (b - a) <= level
}

/// `[start, end)` spans of set bits in row `y`.
fn runs(m: &Mask, y: usize) -> Vec<(usize, usize)> {
    let row = &m.bits[y * m.width..(y + 1) * m.width];
    let mut out = Vec::new();
    let mut start = None;
    for (x, &b) in row.iter().enumerate() {
        match (b, start) {
            (true, None) => start = Some(x),
            (false, Some(s)) => {
                out.push((s, x));
                start = None;
            }
            _ => {}
        }
    }
    if let Some(s) = start {
        out.push((s, row.len()));
    }
    out
}

/// Outermost column in `[x_lo, x_hi]` covered (≥ `fill`) over rows `[lo, hi)`.
fn side(m: &Mask, lo: usize, hi: usize, x_lo: i64, x_hi: i64, leftmost: bool) -> Option<usize> {
    const FILL: f64 = 0.9;
    let a = x_lo.max(0) as usize;
    let b = (x_hi + 1).min(m.width as i64);
    if b <= a as i64 || hi <= lo {
        return None;
    }
    let covered = |x: usize| {
        let n = (lo..hi).filter(|&y| m.get(x, y)).count();
        n as f64 / (hi - lo) as f64 >= FILL
    };
    let mut xs = a..b as usize;
    if leftmost {
        xs.find(|&x| covered(x))
    } else {
        xs.rev().find(|&x| covered(x))
    }
}

/// Locate the minimap's frame in a window capture (top-left quadrant): a
/// thin border-coloured rectangle with rounded corners — top and bottom
/// edges with matching x-extent plus two solid sides. The largest wins.
/// Returns `(x, y, w, h)` measured edge to edge.
pub fn find_frame(img: &Image, border: [u8; 3], tolerance: i32) -> Option<Region> {
    const MIN_W: usize = 80;
    const MIN_H: usize = 40;
    const CORNER: usize = 6;
    let quad = img.crop(0, 0, img.width / 2, img.height / 2);
    let m = color_mask(&quad, border, tolerance);
    let mut edges: Vec<(usize, usize, usize)> = Vec::new();
    for y in 0..m.height {
        let row = &m.bits[y * m.width..(y + 1) * m.width];
        if row.iter().filter(|b| **b).count() < MIN_W {
            continue;
        }
        edges.extend(
            runs(&m, y)
                .into_iter()
                .filter(|(a, b)| b - a >= MIN_W)
                .map(|(a, b)| (y, a, b)),
        );
    }
    let mut best: Option<(i64, Region)> = None;
    for (i, &(y0, a0, b0)) in edges.iter().enumerate() {
        for &(y1, a1, b1) in &edges[i + 1..] {
            if y1 - y0 < MIN_H || a0.abs_diff(a1) > 3 || b0.abs_diff(b1) > 3 {
                continue;
            }
            let (lo, hi) = (y0 + CORNER, y1 - CORNER + 1);
            let (a, b) = (a0.min(a1) as i64, b0.max(b1) as i64);
            let c = CORNER as i64;
            let (Some(left), Some(right)) = (
                side(&m, lo, hi, a - c, a + 2, true),
                side(&m, lo, hi, b - 3, b + c, false),
            ) else {
                continue;
            };
            let area = (right as i64 - left as i64) * (y1 - y0) as i64;
            if best.is_none_or(|(ba, _)| area > ba) {
                best = Some((
                    area,
                    (
                        left as i32,
                        y0 as i32,
                        (right - left) as i32,
                        (y1 - y0) as i32,
                    ),
                ));
            }
        }
    }
    best.map(|(_, r)| r)
}

/// A marker candidate: pixels, centroid, and bounding box (with `y_max`,
/// the true bottom row).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Blob {
    pub pixels: usize,
    pub cx: f64,
    pub cy: f64,
    pub y_max: usize,
    pub x_min: usize,
    pub y_min: usize,
    pub x_max: usize,
}

/// Marker candidates, largest first. Pixels within 2px of each other form
/// one marker (a 1px rope or platform line through the dot splits it into
/// fragments, which a plain component would drop); sizes and positions
/// come from the real pixels only.
pub fn marker_blobs(mask: &Mask, min_px: usize) -> Vec<Blob> {
    let (w, h) = (mask.width, mask.height);
    if mask.bits.iter().all(|b| !b) {
        return Vec::new();
    }
    // 3x3 dilation: the "within 2px" bridge between fragments.
    let mut grown = mask.bits.clone();
    for y in 0..h {
        for x in 0..w {
            if !mask.get(x, y) {
                continue;
            }
            for ny in y.saturating_sub(1)..=(y + 1).min(h - 1) {
                for nx in x.saturating_sub(1)..=(x + 1).min(w - 1) {
                    grown[ny * w + nx] = true;
                }
            }
        }
    }
    let mut visited = vec![false; w * h];
    let mut out = Vec::new();
    let mut stack = Vec::new();
    for sy in 0..h {
        for sx in 0..w {
            if !mask.get(sx, sy) || visited[sy * w + sx] {
                continue;
            }
            visited[sy * w + sx] = true;
            stack.push((sx, sy));
            let (mut n, mut sum_x, mut sum_y) = (0usize, 0usize, 0usize);
            let (mut y_max, mut x_max, mut y_min, mut x_min) = (0, 0, h, w);
            while let Some((x, y)) = stack.pop() {
                if mask.get(x, y) {
                    n += 1;
                    sum_x += x;
                    sum_y += y;
                    y_max = y_max.max(y);
                    y_min = y_min.min(y);
                    x_max = x_max.max(x);
                    x_min = x_min.min(x);
                }
                for ny in y.saturating_sub(1)..=(y + 1).min(h - 1) {
                    for nx in x.saturating_sub(1)..=(x + 1).min(w - 1) {
                        let k = ny * w + nx;
                        if grown[k] && !visited[k] {
                            visited[k] = true;
                            stack.push((nx, ny));
                        }
                    }
                }
            }
            if n >= min_px {
                out.push(Blob {
                    pixels: n,
                    cx: sum_x as f64 / n as f64,
                    cy: sum_y as f64 / n as f64,
                    y_max,
                    x_min,
                    y_min,
                    x_max,
                });
            }
        }
    }
    // Largest first; the sort is stable, so ties keep scan order.
    out.sort_by_key(|b| std::cmp::Reverse(b.pixels));
    out
}

/// y of segment `s` at column `x` (lerped).
fn seg_y(s: &[f64; 4], x: f64) -> f64 {
    let [x0, y0, x1, y1] = *s;
    if x1 == x0 {
        return (y0 + y1) / 2.0;
    }
    y0 + (x - x0) / (x1 - x0) * (y1 - y0)
}

/// Row of the drawn segment under column `x` nearest `y`, within `max_snap`.
pub fn platform_row_at(segs: &[[f64; 4]], x: f64, y: f64, max_snap: f64) -> Option<i64> {
    const X_SLACK: f64 = 4.0;
    let mut best: Option<(f64, f64)> = None;
    for s in segs {
        if !(s[0].min(s[2]) - X_SLACK <= x && x <= s[0].max(s[2]) + X_SLACK) {
            continue;
        }
        let py = seg_y(s, x);
        let d = (py - y).abs();
        if d <= max_snap && best.is_none_or(|(b, _)| d < b) {
            best = Some((d, py));
        }
    }
    best.map(|(_, py)| crate::rotation::py_round(py))
}

/// `(x0, x1)` extent of the drawn segment under `(x, y)`. The point may sit
/// up to `band` px above the row (the glyph floats above the line) and 2px
/// below it.
pub fn platform_span_at(segs: &[[f64; 4]], x: f64, y: f64, band: f64) -> Option<(i64, i64)> {
    const X_SLACK: f64 = 3.0;
    let mut best: Option<(f64, f64, f64)> = None;
    for s in segs {
        let (lo, hi) = (s[0].min(s[2]), s[0].max(s[2]));
        if !(lo - X_SLACK <= x && x <= hi + X_SLACK) {
            continue;
        }
        let d = seg_y(s, x) - y;
        if (-2.0..=band).contains(&d) && best.is_none_or(|(b, _, _)| d < b) {
            best = Some((d, lo, hi));
        }
    }
    best.map(|(_, lo, hi)| (crate::rotation::py_round(lo), crate::rotation::py_round(hi)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn canvas(w: usize, h: usize) -> Image {
        let mut img = Image::new(w, h);
        for px in img.bgra.as_chunks_mut::<4>().0 {
            px.copy_from_slice(&[40, 30, 20, 255]);
        }
        img
    }

    fn rect(img: &mut Image, x0: usize, y0: usize, x1: usize, y1: usize, c: [u8; 3]) {
        for y in y0..=y1 {
            for x in x0..=x1 {
                img.set_bgr(x, y, c);
            }
        }
    }

    #[test]
    fn finds_the_frame_with_rounded_corners() {
        let mut img = canvas(800, 600);
        let white = [228, 228, 228];
        // Frame at x 20..220, y 30..130 with corners missing (rounded).
        rect(&mut img, 23, 30, 217, 30, white);
        rect(&mut img, 23, 130, 217, 130, white);
        rect(&mut img, 20, 33, 20, 127, white);
        rect(&mut img, 220, 33, 220, 127, white);
        assert_eq!(find_frame(&img, white, 10), Some((20, 30, 200, 100)));
        assert_eq!(find_frame(&canvas(800, 600), white, 10), None);
    }

    #[test]
    fn blobs_bridge_a_line_through_the_dot_and_drop_specks() {
        let mut m = Mask {
            width: 20,
            height: 20,
            bits: vec![false; 400],
        };
        for (x, y) in [
            (5, 5),
            (6, 5),
            (5, 6),
            (6, 6),
            (5, 8),
            (6, 8),
            (5, 9),
            (6, 9),
        ] {
            m.bits[y * 20 + x] = true; // a 2x5 dot split by an empty row
        }
        m.bits[15 * 20 + 15] = true; // a speck
        let blobs = marker_blobs(&m, 6);
        assert_eq!(blobs.len(), 1);
        assert_eq!((blobs[0].pixels, blobs[0].y_max, blobs[0].y_min), (8, 9, 5));
        assert_eq!(marker_blobs(&m, 1).len(), 2);
    }

    #[test]
    fn darkness_uses_the_99th_percentile() {
        let mut img = Image::new(100, 100);
        assert!(is_dark(&img, 12.0));
        rect(&mut img, 0, 0, 3, 3, [255, 255, 255]); // a few bright pixels
        assert!(is_dark(&img, 12.0));
        rect(&mut img, 0, 0, 99, 30, [200, 200, 200]);
        assert!(!is_dark(&img, 12.0));
    }

    #[test]
    fn platform_lookups() {
        let segs = [[10.0, 50.0, 90.0, 50.0], [10.0, 80.0, 90.0, 80.0]];
        assert_eq!(platform_row_at(&segs, 50.0, 46.0, 8.0), Some(50));
        assert_eq!(platform_row_at(&segs, 50.0, 65.0, 8.0), None);
        assert_eq!(platform_span_at(&segs, 50.0, 47.0, 6.0), Some((10, 90)));
        assert_eq!(platform_span_at(&segs, 50.0, 53.0, 6.0), None); // 3px below
    }
}
