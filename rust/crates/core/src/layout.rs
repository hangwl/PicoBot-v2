//! Drawn-geometry edits: tidying hand-drawn lines, moving anchors with
//! the platform they stand on, and finding the line under the feet.
//! Lines are stored normalised (0–1); thresholds here are minimap px.

use crate::platform_fit::{tidy_segments, Seg};
use crate::rotation::Anchor;

/// Round to 4 places, as map files store coordinates.
pub fn r4(v: f64) -> f64 {
    (v * 1e4).round() / 1e4
}

/// An anchor an edit moved: (name, before, after), normalised.
pub type Moved = Vec<(String, (f64, f64), (f64, f64))>;

/// What a click with the erase tool hits.
#[derive(Debug, PartialEq)]
pub enum Erase {
    Anchor(usize),
    Rope(usize),
    Nothing,
}

/// The anchor (within `ANCHOR_HIT_PX`), else the rope (within
/// `ROPE_HIT_PX` of its column and span), a click at (x, y) minimap px
/// lands on.
pub fn erase_target(anchors: &[Anchor], ropes: &[Seg], x: f64, y: f64, w: f64, h: f64) -> Erase {
    const ANCHOR_HIT_PX: f64 = 12.0;
    const ROPE_HIT_PX: f64 = 6.0;
    let near = anchors
        .iter()
        .enumerate()
        .map(|(i, a)| (i, ((a.x * w - x).powi(2) + (a.y * h - y).powi(2)).sqrt()))
        .filter(|(_, d)| *d <= ANCHOR_HIT_PX)
        .min_by(|a, b| a.1.total_cmp(&b.1));
    if let Some((i, _)) = near {
        return Erase::Anchor(i);
    }
    let rope = ropes
        .iter()
        .enumerate()
        .filter(|(_, r)| {
            let (top, bottom) = ((r[1] * h).min(r[3] * h), (r[1] * h).max(r[3] * h));
            y >= top - ROPE_HIT_PX && y <= bottom + ROPE_HIT_PX
        })
        .map(|(i, r)| (i, ((r[0] + r[2]) / 2.0 * w - x).abs()))
        .filter(|(_, d)| *d <= ROPE_HIT_PX)
        .min_by(|a, b| a.1.total_cmp(&b.1));
    rope.map_or(Erase::Nothing, |(i, _)| Erase::Rope(i))
}

/// Straighten and merge normalised segments (thresholds in px).
pub fn tidy(segs: &[Seg], w: f64, h: f64) -> Vec<Seg> {
    let px: Vec<Seg> = segs
        .iter()
        .map(|s| [s[0] * w, s[1] * h, s[2] * w, s[3] * h])
        .collect();
    tidy_segments(&px)
        .into_iter()
        .map(|s| [r4(s[0] / w), r4(s[1] / h), r4(s[2] / w), r4(s[3] / h)])
        .collect()
}

/// The same set of lines, whichever way each was dragged.
pub fn same_lines(a: &[Seg], b: &[Seg]) -> bool {
    let norm = |segs: &[Seg]| {
        let mut out: Vec<[i64; 4]> = segs
            .iter()
            .map(|s| {
                let q = s.map(|v| (v * 1e4).round() as i64);
                if q[0] <= q[2] {
                    q
                } else {
                    [q[2], q[3], q[0], q[1]]
                }
            })
            .collect();
        out.sort();
        out
    };
    norm(a) == norm(b)
}

/// Left-to-right px segment.
fn px_rows(segs: &[Seg], w: f64, h: f64) -> Vec<Seg> {
    segs.iter()
        .map(|s| {
            let (x0, y0, x1, y1) = (s[0] * w, s[1] * h, s[2] * w, s[3] * h);
            if x1 < x0 {
                [x1, y1, x0, y0]
            } else {
                [x0, y0, x1, y1]
            }
        })
        .collect()
}

fn row_at(p: &Seg, x: f64) -> f64 {
    let t = if p[2] == p[0] {
        0.0
    } else {
        ((x - p[0]) / (p[2] - p[0])).clamp(0.0, 1.0)
    };
    p[1] + t * (p[3] - p[1])
}

/// Move anchors with the platform they stand on when its line moves
/// (levelled, merged, redrawn): each keeps its x and its own float above
/// the line. An anchor stands on a line up to 8px above it or 2px below
/// (the planner's rule); it follows a new line within `follow_px`.
pub fn resnap_anchors(
    anchors: &mut [Anchor],
    old: &[Seg],
    new: &[Seg],
    w: f64,
    h: f64,
    follow_px: f64,
) -> Moved {
    let (old, new) = (px_rows(old, w, h), px_rows(new, w, h));
    let mut moved = Vec::new();
    for a in anchors.iter_mut() {
        let (ax, ay) = (a.x * w, a.y * h);
        let mut best: Option<(f64, f64)> = None;
        for p in old.iter().filter(|p| p[0] - 3.0 <= ax && ax <= p[2] + 3.0) {
            let d = row_at(p, ax) - ay;
            if (-2.0..=8.0).contains(&d) && best.is_none_or(|b| d < b.0) {
                best = Some((d, row_at(p, ax)));
            }
        }
        let Some((float_px, old_row)) = best else {
            continue;
        };
        let new_row = new
            .iter()
            .filter(|p| p[0] - 3.0 <= ax && ax <= p[2] + 3.0)
            .map(|p| row_at(p, ax))
            .filter(|r| (r - old_row).abs() <= follow_px)
            .min_by(|x, y| (x - old_row).abs().total_cmp(&(y - old_row).abs()));
        let Some(new_row) = new_row else { continue };
        if (new_row - old_row).abs() < 0.05 {
            continue;
        }
        let before = (a.x, a.y);
        a.y = r4((new_row - float_px) / h);
        moved.push((a.name.clone(), before, (a.x, a.y)));
    }
    moved
}

/// Undo re-snaps: put back anchors an edit moved, only while they are
/// still where it left them (a replaced anchor can reuse the name).
pub fn restore_anchors(anchors: &mut [Anchor], moved: &Moved) {
    for a in anchors.iter_mut() {
        if let Some((_, before, after)) = moved.iter().find(|m| m.0 == a.name) {
            if (a.x, a.y) == *after {
                (a.x, a.y) = *before;
            }
        }
    }
}

/// The drawn line under `feet` (px) within `reach` px of them: its index
/// and how far the feet sit below it (+ = drawn too high).
pub fn line_under_feet(
    segs: &[Seg],
    w: f64,
    h: f64,
    feet: (f64, f64),
    reach: f64,
) -> Option<(usize, f64)> {
    let (fx, fy) = feet;
    let mut best: Option<(usize, f64)> = None;
    for (i, p) in px_rows(segs, w, h).iter().enumerate() {
        if !(p[0] - 3.0 <= fx && fx <= p[2] + 3.0) {
            continue;
        }
        let dy = fy - row_at(p, fx);
        if dy.abs() <= reach && best.is_none_or(|b| dy.abs() < b.1.abs()) {
            best = Some((i, dy));
        }
    }
    best
}

/// `seg` moved `dy` px down (+) or up.
pub fn shift_line(seg: &Seg, dy: f64, h: f64) -> Seg {
    [seg[0], r4(seg[1] + dy / h), seg[2], r4(seg[3] + dy / h)]
}

/// A new anchor's name: the first free `a<n>`.
pub fn next_anchor_name(anchors: &[Anchor]) -> String {
    (0..)
        .map(|n| format!("a{n}"))
        .find(|n| !anchors.iter().any(|a| &a.name == n))
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    #[test]
    fn erase_hits_an_anchor_then_a_rope_and_ignores_stray_clicks() {
        use super::*;
        let anchors = [Anchor::new("A", 0.5, 0.5)]; // (100, 75) on 200x150
        let ropes: [Seg; 1] = [[0.25, 0.2, 0.25, 0.8]]; // x 50, y 30..120
        let hit = |x, y| erase_target(&anchors, &ropes, x, y, 200.0, 150.0);
        assert_eq!(hit(105.0, 80.0), Erase::Anchor(0));
        assert_eq!(hit(52.0, 90.0), Erase::Rope(0));
        assert_eq!(hit(52.0, 130.0), Erase::Nothing); // below the rope
        assert_eq!(hit(150.0, 40.0), Erase::Nothing);
    }

    use super::*;

    const W: f64 = 200.0;
    const H: f64 = 100.0;

    fn anchor(name: &str, x: f64, y: f64) -> Anchor {
        Anchor::new(name, x / W, y / H)
    }

    #[test]
    fn same_lines_ignores_drag_direction_and_order() {
        let a = [[0.1, 0.5, 0.4, 0.5], [0.6, 0.2, 0.9, 0.2]];
        let b = [[0.9, 0.2, 0.6, 0.2], [0.4, 0.5, 0.1, 0.5]];
        assert!(same_lines(&a, &b));
        assert!(!same_lines(&a, &a[..1]));
    }

    #[test]
    fn anchors_follow_their_line_and_undo_puts_them_back() {
        let old = [[0.1, 0.5, 0.5, 0.5]]; // y 50px
        let new = [[0.1, 0.53, 0.5, 0.53]]; // y 53px
        let mut anchors = vec![
            anchor("a0", 40.0, 46.0),
            anchor("far", 150.0, 46.0),
            anchor("high", 40.0, 30.0),
        ];
        let moved = resnap_anchors(&mut anchors, &old, &new, W, H, 4.0);
        assert_eq!(moved.len(), 1);
        assert!((anchors[0].y * H - 49.0).abs() < 1e-9); // kept its 4px float
        assert_eq!(anchors[1].y * H, 46.0); // not on the line
        assert_eq!(anchors[2].y * H, 30.0); // too far above it
        restore_anchors(&mut anchors, &moved);
        assert!((anchors[0].y * H - 46.0).abs() < 1e-9);
    }

    #[test]
    fn a_line_moved_too_far_leaves_its_anchors() {
        let mut anchors = vec![anchor("a0", 40.0, 46.0)];
        let moved = resnap_anchors(
            &mut anchors,
            &[[0.1, 0.5, 0.5, 0.5]],
            &[[0.1, 0.6, 0.5, 0.6]],
            W,
            H,
            4.0,
        );
        assert!(moved.is_empty());
    }

    #[test]
    fn the_line_under_the_feet_is_the_nearest_within_reach() {
        let segs = [
            [0.1, 0.5, 0.5, 0.5],
            [0.1, 0.56, 0.5, 0.56],
            [0.7, 0.5, 0.9, 0.5],
        ];
        let (i, dy) = line_under_feet(&segs, W, H, (40.0, 55.0), 10.0).unwrap();
        assert_eq!(i, 1);
        assert!((dy + 1.0).abs() < 1e-9);
        assert_eq!(line_under_feet(&segs, W, H, (40.0, 80.0), 10.0), None);
        assert_eq!(line_under_feet(&segs, W, H, (125.0, 50.0), 10.0), None); // between spans
    }

    #[test]
    fn shifting_and_naming() {
        assert_eq!(
            shift_line(&[0.1, 0.5, 0.4, 0.52], 2.0, H),
            [0.1, 0.52, 0.4, 0.54]
        );
        let anchors = vec![anchor("a0", 1.0, 1.0), anchor("a2", 1.0, 1.0)];
        assert_eq!(next_anchor_name(&anchors), "a1");
    }

    #[test]
    fn tidy_levels_a_hand_drag() {
        let t = tidy(&[[0.1, 0.5, 0.4, 0.51]], W, H);
        assert_eq!(t.len(), 1);
        assert_eq!(t[0][1], t[0][3]);
    }
}
