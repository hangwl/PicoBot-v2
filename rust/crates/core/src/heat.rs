//! Where attacks land: a decaying grid over the minimap, so the dashboard
//! can show which stretches a rotation works hard and which it neglects.

use std::collections::HashMap;

/// Cell size (minimap px).
pub const CELL_PX: f64 = 4.0;
/// An attack counts half as much after this long (s): the map shows the
/// recent rotation, not the whole run.
pub const HALF_LIFE_S: f64 = 300.0;

#[derive(Debug, Clone, Default)]
pub struct AttackHeat {
    /// Cell → (weight, when it was last brought up to date).
    cells: HashMap<(i32, i32), (f64, f64)>,
}

fn decayed(w: f64, since: f64, now: f64) -> f64 {
    w * 0.5f64.powf((now - since).max(0.0) / HALF_LIFE_S)
}

impl AttackHeat {
    pub fn add(&mut self, pos: (f64, f64), now: f64) {
        let key = (
            (pos.0 / CELL_PX).floor() as i32,
            (pos.1 / CELL_PX).floor() as i32,
        );
        let c = self.cells.entry(key).or_insert((0.0, now));
        *c = (decayed(c.0, c.1, now) + 1.0, now);
    }

    pub fn clear(&mut self) {
        self.cells.clear();
    }

    /// Cells as (x, y, weight 0–1 of the hottest), corners in minimap px;
    /// cells faded below 2% are left out.
    pub fn snapshot(&self, now: f64) -> Vec<(f64, f64, f64)> {
        let now_w: Vec<((i32, i32), f64)> = self
            .cells
            .iter()
            .map(|(&k, &(w, t))| (k, decayed(w, t, now)))
            .collect();
        let top = now_w.iter().map(|c| c.1).fold(0.0, f64::max);
        if top <= 0.0 {
            return Vec::new();
        }
        let mut out: Vec<(f64, f64, f64)> = now_w
            .into_iter()
            .map(|((x, y), w)| (x as f64 * CELL_PX, y as f64 * CELL_PX, w / top))
            .filter(|c| c.2 >= 0.02)
            .collect();
        out.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.total_cmp(&b.1)));
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn attacks_pile_up_per_cell_and_fade() {
        let mut h = AttackHeat::default();
        for _ in 0..4 {
            h.add((10.0, 41.0), 0.0);
        }
        h.add((30.0, 41.0), 0.0);
        let s = h.snapshot(0.0);
        assert_eq!(s, [(8.0, 40.0, 1.0), (28.0, 40.0, 0.25)]);
        // Fresh attacks outweigh old ones.
        h.add((30.0, 41.0), 2.0 * HALF_LIFE_S);
        let s = h.snapshot(2.0 * HALF_LIFE_S);
        assert_eq!(s[1].2, 1.0);
        assert!((s[0].2 - 4.0 / 4.0 / (0.25 + 1.0)).abs() < 1e-9);
    }
}
