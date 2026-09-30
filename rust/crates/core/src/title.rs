//! The map title above the minimap: where the band is, and which rows and
//! columns of it hold the title text (what OCR reads, and the panel view
//! draws).

use crate::vision::Image;
use crate::vision::Region;

/// Lowercase letters and digits only: OCR adds spacing and punctuation
/// noise (`"Limina : 1-5"`), so comparisons ignore everything else.
pub fn normalize_name(text: &str) -> String {
    text.chars()
        .flat_map(char::to_lowercase)
        .filter(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        .collect()
}

/// The title band: the panel's width (± `pad`), from the window top to
/// `header_px` into the minimap frame. The client clips the title at the
/// panel edge, so nothing useful lies outside it.
pub fn name_strip_region(minimap: Region, header_px: i32, pad: i32) -> Region {
    let (x, y, w, h) = minimap;
    let bx = (x - pad).max(0);
    (bx, 0, x + w + pad - bx, y + header_px.min(h))
}

/// A text line's box `(x0, y0, x1, y1)`, band coordinates, end-exclusive.
pub type LineBox = (usize, usize, usize, usize);
/// The panel's separator: `(row, x0, x1)`.
pub type Divider = (usize, usize, usize);

/// Tuning for [`title_scan`] (the Python defaults).
#[derive(Debug, Clone, Copy)]
pub struct ScanOptions {
    pub white_thresh: u8,
    pub min_row_px: usize,
    pub gap_rows: usize,
    pub min_line_h: usize,
    pub min_line_px: usize,
    pub col_gap: usize,
    pub icon_fill: f64,
    pub divider_run: usize,
    pub tail_thresh: u8,
    pub tail_gap: usize,
}

impl Default for ScanOptions {
    fn default() -> Self {
        ScanOptions {
            white_thresh: 170,
            min_row_px: 2,
            gap_rows: 3,
            min_line_h: 4,
            min_line_px: 15,
            col_gap: 4,
            icon_fill: 0.5,
            divider_run: 120,
            tail_thresh: 80,
            tail_gap: 12,
        }
    }
}

/// Row-major boolean grid.
struct Grid {
    w: usize,
    h: usize,
    px: Vec<bool>,
}

impl Grid {
    fn from(img: &Image, above: u8) -> Grid {
        let (w, h) = (img.width, img.height);
        let mut px = Vec::with_capacity(w * h);
        for y in 0..h {
            for x in 0..w {
                px.push(img.bgr(x, y).into_iter().min().unwrap_or(0) > above);
            }
        }
        Grid { w, h, px }
    }
    fn get(&self, x: usize, y: usize) -> bool {
        self.px[y * self.w + x]
    }
    fn row_count(&self, y: usize) -> usize {
        self.px[y * self.w..(y + 1) * self.w]
            .iter()
            .filter(|b| **b)
            .count()
    }
}

/// Contiguous row runs carrying text, merging gaps of at most `gap` rows.
fn row_groups(g: &Grid, rows: usize, min_row_px: usize, gap: usize) -> Vec<(usize, usize)> {
    let mut lines = Vec::new();
    let (mut start, mut last, mut blank) = (None, 0, 0);
    for y in 0..rows {
        if g.row_count(y) >= min_row_px {
            start.get_or_insert(y);
            last = y;
            blank = 0;
        } else if let Some(s) = start {
            blank += 1;
            if blank > gap {
                lines.push((s, last));
                start = None;
                blank = 0;
            }
        }
    }
    if let Some(s) = start {
        lines.push((s, last));
    }
    lines
}

/// Right edge of the region-icon tile left of the title: its sides are
/// near-full-height unbroken bright columns, taller than any glyph.
fn icon_tile_end(g: &Grid, y0: usize, y1: usize) -> Option<usize> {
    let h = y1 - y0 + 1;
    if h < 20 {
        return None;
    }
    let tall: Vec<usize> = (0..g.w)
        .filter(|&x| {
            let (mut run, mut best) = (0usize, 0usize);
            for y in y0..=y1 {
                run = if g.get(x, y) { run + 1 } else { 0 };
                best = best.max(run);
            }
            best as f64 >= 0.7 * h as f64
        })
        .collect();
    let first = *tall.first()?;
    if first >= g.w / 2 {
        return None;
    }
    let reach = first + (1.6 * h as f64) as usize;
    tall.iter().rev().find(|&&x| x <= reach).map(|x| x + 1)
}

/// Segment the header band into title text lines (top to bottom) and the
/// panel's divider, if found.
///
/// Near-white pixels are text; rows group into lines (the divider and map
/// lines are too thin); the icon tile and dense column runs (icons are
/// filled, text is sparse) are cut; scanning stops at the divider — the
/// first row with a long contiguous bright run — and a faded tail that
/// the client clipped is followed on dimmer columns.
pub fn title_scan(band: &Image, o: &ScanOptions) -> (Vec<LineBox>, Option<Divider>) {
    if band.width == 0 || band.height == 0 {
        return (Vec::new(), None);
    }
    let mut white = Grid::from(band, o.white_thresh);
    let w = white.w;
    let mut div = None;
    let mut cut = white.h;
    for y in 0..white.h {
        let (mut best, mut best_at, mut run) = (0, 0, 0);
        for x in 0..w {
            if white.get(x, y) {
                run += 1;
                if run > best {
                    best = run;
                    best_at = x + 1 - run;
                }
            } else {
                run = 0;
            }
        }
        if best >= o.divider_run {
            div = Some((y, best_at, best_at + best));
            cut = y;
            break;
        }
    }
    if cut == 0 {
        return (Vec::new(), div);
    }
    let groups_of = |g: &Grid| -> Vec<(usize, usize)> {
        row_groups(g, cut, o.min_row_px, o.gap_rows)
            .into_iter()
            .filter(|&(a, b)| {
                b - a + 1 >= o.min_line_h
                    && (a..=b).map(|y| g.row_count(y)).sum::<usize>() >= o.min_line_px
            })
            .collect()
    };
    let mut groups = groups_of(&white);
    if groups.is_empty() {
        return (Vec::new(), div);
    }
    if let Some(end) = icon_tile_end(&white, groups[0].0, groups[groups.len() - 1].1) {
        for y in 0..white.h {
            for x in 0..=end.min(w - 1) {
                white.px[y * w + x] = false;
            }
        }
        groups = groups_of(&white);
        if groups.is_empty() {
            return (Vec::new(), div);
        }
    }
    let (z0, z1) = (groups[0].0, groups[groups.len() - 1].1);
    let zone_h = z1 - z0 + 1;
    let colc: Vec<usize> = (0..w)
        .map(|x| (z0..=z1).filter(|&y| white.get(x, y)).count())
        .collect();
    let mut runs: Vec<(usize, usize)> = Vec::new();
    let mut rs = None;
    for (x, &c) in colc.iter().chain([0].iter()).enumerate() {
        if c > 0 {
            rs.get_or_insert(x);
        } else if let Some(s) = rs.take() {
            runs.push((s, x));
        }
    }
    let mut merged: Vec<(usize, usize)> = Vec::new();
    for (a, b) in runs {
        match merged.last_mut() {
            Some(m) if a - m.1 < o.col_gap => m.1 = b,
            _ => merged.push((a, b)),
        }
    }
    let mut keep: Vec<(usize, usize)> = merged
        .iter()
        .copied()
        .filter(|&(a, b)| {
            colc[a..b].iter().sum::<usize>() as f64 / ((b - a) * zone_h) as f64 <= o.icon_fill
        })
        .collect();
    if keep.is_empty() {
        keep = merged;
    }
    let mut x0 = keep.iter().map(|k| k.0).min().unwrap_or(0);
    let mut x1 = keep.iter().map(|k| k.1).max().unwrap_or(0);
    let dim = Grid::from(band, o.tail_thresh);
    for x in (0..w).filter(|&x| (z0..=z1).any(|y| dim.get(x, y))) {
        if x < x1 {
            continue;
        }
        if x - x1 > o.tail_gap {
            break;
        }
        x1 = x;
    }
    x0 = x0.saturating_sub(2);
    x1 = (x1 + 2).min(w);
    let lines = groups
        .iter()
        .map(|&(a, b)| (x0, a.saturating_sub(1), x1, (b + 2).min(cut)))
        .collect();
    (lines, div)
}

/// What the recogniser reads: the title lines (and the faded tail up to
/// the divider's end) framed by `margin` px of the crop's median colour —
/// glyphs touching the edge are misread, and padding from the band itself
/// would pull the region icon back in. Returns the image and each line's
/// row range in it; None without text lines.
pub fn title_crop(
    band: &Image,
    vpad: usize,
    margin: usize,
) -> Option<(Image, Vec<(usize, usize)>)> {
    let (lines, div) = title_scan(band, &ScanOptions::default());
    if lines.is_empty() {
        return None;
    }
    let y0 = lines.iter().map(|l| l.1).min()?.saturating_sub(vpad);
    let y1 = (lines.iter().map(|l| l.3).max()? + vpad).min(band.height);
    let x0 = lines.iter().map(|l| l.0).min()?;
    let mut x1 = lines.iter().map(|l| l.2).max()?;
    if let Some(d) = div {
        x1 = x1.max(d.2);
    }
    let x1 = x1.min(band.width);
    let (cw, ch) = (x1.saturating_sub(x0), y1.saturating_sub(y0));
    if cw == 0 || ch == 0 {
        return None;
    }
    let crop = band.crop(x0, y0, cw, ch);
    let bg: [u8; 3] = std::array::from_fn(|c| {
        let mut v: Vec<u8> = (0..ch)
            .flat_map(|y| (0..cw).map(move |x| (x, y)))
            .map(|(x, y)| crop.bgr(x, y)[c])
            .collect();
        v.sort_unstable();
        let n = v.len();
        if n % 2 == 1 {
            v[n / 2]
        } else {
            ((v[n / 2 - 1] as f64 + v[n / 2] as f64) / 2.0) as u8
        }
    });
    let mut out = Image::new(cw + 2 * margin, ch + 2 * margin);
    for y in 0..out.height {
        for x in 0..out.width {
            out.set_bgr(x, y, bg);
        }
    }
    for y in 0..ch {
        for x in 0..cw {
            out.set_bgr(x + margin, y + margin, crop.bgr(x, y));
        }
    }
    let rows = lines
        .iter()
        .map(|l| (l.1 - y0 + margin, l.3 - y0 + margin))
        .collect();
    Some((out, rows))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_normalise_to_letters_and_digits() {
        assert_eq!(normalize_name("Limina : 1-5"), "limina15");
        assert_eq!(normalize_name(""), "");
    }

    #[test]
    fn the_band_spans_the_panel_width() {
        assert_eq!(name_strip_region((2, 40, 100, 80), 90, 4), (0, 0, 106, 120));
        assert_eq!(
            name_strip_region((20, 40, 100, 200), 90, 4),
            (16, 0, 108, 130)
        );
    }

    #[test]
    fn an_empty_band_has_no_lines() {
        assert_eq!(
            title_scan(&Image::new(0, 0), &ScanOptions::default()),
            (Vec::new(), None)
        );
    }
}
