//! Drawn-platform hygiene and a standing-position fit diagnostic.
//!
//! `tidy_segments` straightens near-flat drags (hand drags are rarely
//! level) and merges overlapping pieces on one row. [`PlatformFit`] records
//! where the player's feet actually settle on each drawn platform, so a line
//! drawn too high or too low shows up as a consistent offset. It only
//! reports — drawn geometry stays authoritative.

use std::collections::HashMap;

use serde::Serialize;

use crate::rotation::py_round;

/// A segment in minimap px: `[x0, y0, x1, y1]`.
pub type Seg = [f64; 4];

/// Level only up to this much wobble (px): the planner tolerates feet 2px
/// below a line, and levelling moves each end by half the difference.
pub const FLAT_PX: f64 = 4.0;
/// Samples before a platform's offset is trusted.
pub const FIT_MIN: usize = 5;
/// Rows this close are the same ledge ...
pub const MERGE_PX: f64 = 2.0;
/// ... when the pieces overlap or meet (a gap is real).
pub const TOUCH_PX: f64 = 0.5;

/// Level a near-flat segment at its mean height; keep real slopes.
/// Always returned left-to-right.
pub fn straighten(seg: Seg) -> Seg {
    let [mut x0, mut y0, mut x1, mut y1] = seg;
    if x1 < x0 {
        std::mem::swap(&mut x0, &mut x1);
        std::mem::swap(&mut y0, &mut y1);
    }
    if (y1 - y0).abs() <= FLAT_PX {
        let y = (y0 + y1) / 2.0;
        return [x0, y, x1, y];
    }
    [x0, y0, x1, y1]
}

fn is_level(s: &Seg) -> bool {
    (s[1] - s[3]).abs() < 1e-6
}

fn touch(a: &Seg, b: &Seg) -> bool {
    (a[1] - b[1]).abs() <= MERGE_PX && b[0] <= a[2] + TOUCH_PX && a[0] <= b[2] + TOUCH_PX
}

/// Merge level segments on one row whose spans overlap or meet; the row is
/// the length-weighted mean. Sloped segments pass through untouched (and
/// come first, as in the Python host).
pub fn merge_level(segs: &[Seg]) -> Vec<Seg> {
    let mut out: Vec<Seg> = segs.iter().filter(|s| !is_level(s)).copied().collect();
    let mut level: Vec<Seg> = segs.iter().filter(|s| is_level(s)).copied().collect();
    // One merge can bring two others into contact: repeat until stable.
    'again: loop {
        for i in 0..level.len() {
            for j in i + 1..level.len() {
                let (a, b) = (level[i], level[j]);
                if !touch(&a, &b) {
                    continue;
                }
                let (la, lb) = ((a[2] - a[0]).max(1e-6), (b[2] - b[0]).max(1e-6));
                let y = (a[1] * la + b[1] * lb) / (la + lb);
                level[i] = [a[0].min(b[0]), y, a[2].max(b[2]), y];
                level.remove(j);
                continue 'again;
            }
        }
        break;
    }
    out.extend(level);
    out
}

/// Straighten every segment, then merge overlapping level ones.
pub fn tidy_segments(segs: &[Seg]) -> Vec<Seg> {
    let straight: Vec<Seg> = segs.iter().map(|s| straighten(*s)).collect();
    merge_level(&straight)
}

/// A stored line, orientation-free and rounded to 4 decimals (as the map
/// file stores it) — in 1e-4 units so it can be a hash key.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct SegKey([i64; 4]);

impl SegKey {
    pub fn of(seg: &[f64; 4]) -> Self {
        let q = |v: f64| (v * 1e4).round() as i64;
        let [x0, y0, x1, y1] = seg.map(q);
        SegKey(if x0 <= x1 {
            [x0, y0, x1, y1]
        } else {
            [x1, y1, x0, y0]
        })
    }

    /// `"x0,y0,x1,y1"` — how commands name a stored line.
    pub fn text(&self) -> String {
        self.0.map(|v| format_g(v as f64 / 1e4)).join(",")
    }
}

/// Python's `f"{v:g}"` for the 0–1.5 values lines hold.
fn format_g(v: f64) -> String {
    let s = format!("{v}");
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').to_owned()
    } else {
        s
    }
}

fn median(values: &mut [f64]) -> f64 {
    values.sort_by(f64::total_cmp);
    let n = values.len();
    if n % 2 == 1 {
        values[n / 2]
    } else {
        (values[n / 2 - 1] + values[n / 2]) / 2.0
    }
}

fn round1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0
}

/// One drawn platform's fit, as the dashboard shows it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct FitRow {
    pub x0: i64,
    pub x1: i64,
    pub row: f64,
    pub n: usize,
    pub key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub offset: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub spread: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub coverage: Option<f64>,
}

/// Feet positions where the player settled, per drawn platform.
///
/// A sample is taken when the dot has held still (±1px) for `settle_n`
/// reads over at least `settle_s` — standing, not mid-jump — and exactly
/// one drawn platform is within `window_px` of the feet. Residual = feet y
/// − drawn row: positive means the feet sit below the line (drawn too
/// high), negative above it (drawn too low).
#[derive(Debug, Clone)]
pub struct PlatformFit {
    pub window_px: f64,
    pub settle_n: usize,
    pub settle_s: f64,
    pub max_samples: usize,
    /// map → line → [(x, residual)]
    samples: HashMap<String, HashMap<SegKey, Vec<(f64, f64)>>>,
    run: Vec<(f64, f64)>,
    run_t0: f64,
    sampled_run: bool,
}

impl Default for PlatformFit {
    fn default() -> Self {
        PlatformFit {
            window_px: 8.0,
            settle_n: 3,
            settle_s: 0.3,
            max_samples: 200,
            samples: HashMap::new(),
            run: Vec::new(),
            run_t0: 0.0,
            sampled_run: false,
        }
    }
}

impl PlatformFit {
    /// Feed one player position (minimap px) with the map's drawn lines
    /// (normalised) and the minimap region size. True when a sample was
    /// recorded.
    pub fn observe(
        &mut self,
        map: Option<&str>,
        segs: &[[f64; 4]],
        region_wh: Option<(f64, f64)>,
        pos: Option<(f64, f64)>,
        now: f64,
    ) -> bool {
        let (Some(map), Some((w, h)), Some(pos)) = (map.filter(|m| !m.is_empty()), region_wh, pos)
        else {
            self.run.clear();
            return false;
        };
        if segs.is_empty() {
            self.run.clear();
            return false;
        }
        if let Some(first) = self.run.first() {
            if (pos.0 - first.0).abs() > 1.0 || (pos.1 - first.1).abs() > 1.0 {
                self.run.clear();
            }
        }
        if self.run.is_empty() {
            self.run_t0 = now;
            self.sampled_run = false;
        }
        self.run.push(pos);
        if self.sampled_run || self.run.len() < self.settle_n || now - self.run_t0 < self.settle_s {
            return false;
        }
        self.sampled_run = true; // one sample per standstill
        let (x, y) = pos;
        let mut hits = Vec::new();
        for s in segs {
            let [mut x0, mut y0, mut x1, mut y1] = [s[0] * w, s[1] * h, s[2] * w, s[3] * h];
            if x1 < x0 {
                std::mem::swap(&mut x0, &mut x1);
                std::mem::swap(&mut y0, &mut y1);
            }
            if !(x0 - 3.0..=x1 + 3.0).contains(&x) {
                continue;
            }
            let t = if x1 == x0 {
                0.0
            } else {
                ((x - x0) / (x1 - x0)).clamp(0.0, 1.0)
            };
            let row = y0 + t * (y1 - y0);
            if (y - row).abs() <= self.window_px {
                hits.push((s, y - row));
            }
        }
        let [(seg, residual)] = hits.as_slice() else {
            return false; // nothing near, or stacked tiers
        };
        let list = self
            .samples
            .entry(map.to_owned())
            .or_default()
            .entry(SegKey::of(seg))
            .or_default();
        list.push((x, *residual));
        if list.len() > self.max_samples {
            let drop = list.len() - self.max_samples;
            list.drain(..drop);
        }
        true
    }

    /// Per drawn platform: where it is, sample count, median offset and
    /// spread (px), and how much of its length was stood on.
    pub fn summary(
        &self,
        map: Option<&str>,
        segs: &[[f64; 4]],
        region_wh: Option<(f64, f64)>,
    ) -> Vec<FitRow> {
        let (Some(map), Some((w, h))) = (map.filter(|m| !m.is_empty()), region_wh) else {
            return Vec::new();
        };
        let per = self.samples.get(map);
        let mut out: Vec<FitRow> = segs
            .iter()
            .map(|s| {
                let (x0, x1) = {
                    let (a, b) = (s[0] * w, s[2] * w);
                    (a.min(b), a.max(b))
                };
                let row = (s[1] + s[3]) / 2.0 * h;
                let key = SegKey::of(s);
                let samples = per.and_then(|p| p.get(&key)).cloned().unwrap_or_default();
                let mut item = FitRow {
                    x0: py_round(x0),
                    x1: py_round(x1),
                    row: round1(row),
                    n: samples.len(),
                    key: key.text(),
                    offset: None,
                    spread: None,
                    coverage: None,
                };
                if !samples.is_empty() {
                    let mut res: Vec<f64> = samples.iter().map(|(_, r)| *r).collect();
                    let med = median(&mut res);
                    let mut dev: Vec<f64> = res.iter().map(|r| (r - med).abs()).collect();
                    item.offset = Some(round1(med));
                    item.spread = Some(round1(median(&mut dev)));
                    let (lo, hi) = samples.iter().fold((f64::MAX, f64::MIN), |(a, b), (x, _)| {
                        (a.min(*x), b.max(*x))
                    });
                    item.coverage = Some(((hi - lo) / (x1 - x0).max(1.0) * 100.0).round() / 100.0);
                }
                item
            })
            .collect();
        out.sort_by(|a, b| a.row.total_cmp(&b.row).then(a.x0.cmp(&b.x0)));
        out
    }

    /// Median feet offset (px) for one platform, once trusted.
    pub fn offset(&self, map: &str, seg: &[f64; 4]) -> Option<f64> {
        let samples = self.samples.get(map)?.get(&SegKey::of(seg))?;
        if samples.len() < FIT_MIN {
            return None;
        }
        let mut res: Vec<f64> = samples.iter().map(|(_, r)| *r).collect();
        Some(median(&mut res))
    }

    /// A platform's line moved by `dy_px`: keep its samples, with their
    /// offsets measured from the new line.
    pub fn moved(&mut self, map: &str, old: &[f64; 4], new: &[f64; 4], dy_px: f64) {
        if let Some(per) = self.samples.get_mut(map) {
            if let Some(samples) = per.remove(&SegKey::of(old)) {
                per.insert(
                    SegKey::of(new),
                    samples.into_iter().map(|(x, r)| (x, r - dy_px)).collect(),
                );
            }
        }
    }

    pub fn forget(&mut self, map: &str) {
        self.samples.remove(map);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn straighten_levels_wobble_but_keeps_slopes() {
        assert_eq!(
            straighten([10.0, 50.0, 90.0, 52.0]),
            [10.0, 51.0, 90.0, 51.0]
        );
        assert_eq!(
            straighten([0.0, 40.0, 100.0, 44.0]),
            [0.0, 42.0, 100.0, 42.0]
        );
        assert_eq!(
            straighten([0.0, 40.0, 200.0, 52.0]),
            [0.0, 40.0, 200.0, 52.0]
        );
        assert_eq!(straighten([0.0, 40.0, 40.0, 60.0]), [0.0, 40.0, 40.0, 60.0]);
        assert_eq!(
            straighten([90.0, 50.0, 10.0, 50.0]),
            [10.0, 50.0, 90.0, 50.0]
        );
    }

    #[test]
    fn merges_overlaps_and_meets_but_not_gaps_or_rows_or_slopes() {
        let got = merge_level(&[[10.0, 50.0, 60.0, 50.0], [50.0, 51.0, 120.0, 51.0]]);
        assert_eq!(got.len(), 1);
        assert_eq!((got[0][0], got[0][2]), (10.0, 120.0));
        assert!(got[0][1] > 50.0 && got[0][1] < 51.0 && got[0][1] == got[0][3]);
        assert_eq!(
            merge_level(&[[10.0, 50.0, 60.0, 50.0], [62.0, 50.0, 120.0, 50.0]]).len(),
            2
        );
        assert_eq!(
            merge_level(&[[10.0, 50.0, 60.0, 50.0], [60.0, 51.0, 120.0, 51.0]]).len(),
            1
        );
        assert_eq!(
            merge_level(&[[10.0, 50.0, 60.0, 50.0], [20.0, 56.0, 70.0, 56.0]]).len(),
            2
        );
        assert_eq!(
            merge_level(&[
                [0.0, 50.0, 30.0, 50.0],
                [60.0, 50.0, 90.0, 50.0],
                [28.0, 50.0, 62.0, 50.0]
            ]),
            [[0.0, 50.0, 90.0, 50.0]]
        );
        assert_eq!(
            tidy_segments(&[[0.0, 40.0, 40.0, 60.0], [10.0, 42.0, 50.0, 62.0]]).len(),
            2
        );
    }

    const WH: Option<(f64, f64)> = Some((200.0, 100.0));
    const SEGS: [[f64; 4]; 2] = [[0.1, 0.5, 0.6, 0.5], [0.1, 0.8, 0.9, 0.8]]; // rows 50, 80

    fn stand(fit: &mut PlatformFit, t: &mut f64, segs: &[[f64; 4]], pos: (f64, f64), reads: usize) {
        for _ in 0..reads {
            fit.observe(Some("m"), segs, WH, Some(pos), *t);
            *t += 0.15;
        }
    }

    #[test]
    fn one_sample_per_standstill_and_moving_is_not_sampled() {
        let (mut fit, mut t) = (PlatformFit::default(), 0.0);
        stand(&mut fit, &mut t, &SEGS, (60.0, 53.0), 10);
        let top = &fit.summary(Some("m"), &SEGS, WH)[0];
        assert_eq!((top.n, top.offset), (1, Some(3.0)));
        let (mut fit, mut t) = (PlatformFit::default(), 0.0);
        for x in (40..80).step_by(3) {
            fit.observe(Some("m"), &SEGS, WH, Some((x as f64, 50.0)), t);
            t += 0.15;
        }
        assert_eq!(fit.summary(Some("m"), &SEGS, WH)[0].n, 0);
    }

    #[test]
    fn offsets_report_too_high_and_too_low() {
        let (mut fit, mut t) = (PlatformFit::default(), 0.0);
        for x in [30.0, 60.0, 90.0] {
            stand(&mut fit, &mut t, &SEGS, (x, 53.0), 4); // row 50 drawn high
            stand(&mut fit, &mut t, &SEGS, (x + 40.0, 76.0), 4); // row 80 drawn low
        }
        let rows = fit.summary(Some("m"), &SEGS, WH);
        assert_eq!((rows[0].n, rows[0].offset), (3, Some(3.0)));
        assert_eq!((rows[1].n, rows[1].offset), (3, Some(-4.0)));
        assert!(rows[0].coverage.unwrap() > 0.5);
    }

    #[test]
    fn stacked_rows_far_lines_and_drawing_direction() {
        let stacked = [[0.1, 0.5, 0.6, 0.5], [0.1, 0.55, 0.6, 0.55]];
        let (mut fit, mut t) = (PlatformFit::default(), 0.0);
        stand(&mut fit, &mut t, &stacked, (60.0, 52.0), 4);
        assert!(fit
            .summary(Some("m"), &stacked, WH)
            .iter()
            .all(|r| r.n == 0));

        let (mut fit, mut t) = (PlatformFit::default(), 0.0);
        stand(&mut fit, &mut t, &SEGS, (60.0, 30.0), 4);
        assert!(fit.summary(Some("m"), &SEGS, WH).iter().all(|r| r.n == 0));

        let (mut fit, mut t) = (PlatformFit::default(), 0.0);
        stand(&mut fit, &mut t, &SEGS, (60.0, 53.0), 4);
        let flipped = [[0.6, 0.5, 0.1, 0.5], SEGS[1]];
        assert_eq!(fit.summary(Some("m"), &flipped, WH)[0].n, 1);
    }

    #[test]
    fn moving_a_line_carries_its_samples_and_keys_print_like_python() {
        let (mut fit, mut t) = (PlatformFit::default(), 0.0);
        for x in [30.0, 40.0, 50.0, 60.0, 70.0] {
            stand(&mut fit, &mut t, &SEGS, (x, 56.0), 4);
        }
        assert_eq!(fit.offset("m", &SEGS[0]), Some(6.0));
        let moved = [0.1, 0.56, 0.6, 0.56];
        fit.moved("m", &SEGS[0], &moved, 6.0);
        assert_eq!(fit.offset("m", &moved), Some(0.0));
        assert_eq!(SegKey::of(&[0.6, 0.5, 0.1, 0.5]).text(), "0.1,0.5,0.6,0.5");
        assert_eq!(
            SegKey::of(&[1.0, 0.1824, 0.0, 0.1824]).text(),
            "0,0.1824,1,0.1824"
        );
    }
}
