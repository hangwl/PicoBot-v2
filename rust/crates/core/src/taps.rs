//! Calibrated walk taps: how far a keypress of a given length carries,
//! so a final approach can be made in measured steps instead of a hold
//! that overshoots.
//!
//! Stored as reach-file profiles: `walk_taps` (rows `{ms, n, dx, sd}`, the
//! mean px one tap moves) and `walk_speed` (one row `{speed, slide, n}`:
//! px/s while a key is held, and px carried after release).

use serde_json::Value;

/// One tap length and the distance it carries.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TapRow {
    pub secs: f64,
    pub dx: f64,
    pub sd: f64,
    pub n: u64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TapTable {
    rows: Vec<TapRow>,
}

impl TapTable {
    /// The usable rows (a positive mean, at least one sample), shortest
    /// tap first; None when there are none.
    pub fn from_rows(rows: &[Value]) -> Option<Self> {
        let mut rows: Vec<TapRow> = rows
            .iter()
            .filter_map(|r| {
                Some(TapRow {
                    secs: r.get("ms")?.as_f64()? / 1000.0,
                    dx: r.get("dx")?.as_f64()?,
                    sd: r.get("sd").and_then(Value::as_f64).unwrap_or(0.0),
                    n: r.get("n").and_then(Value::as_u64).unwrap_or(0),
                })
            })
            .filter(|r| r.n > 0 && r.dx > 0.0)
            .collect();
        rows.sort_by(|a, b| a.secs.total_cmp(&b.secs));
        (!rows.is_empty()).then_some(TapTable { rows })
    }

    pub fn rows(&self) -> &[TapRow] {
        &self.rows
    }

    /// The longest tap that doesn't overshoot `need` px (a 15% allowance);
    /// the shortest when `need` is at least half its reach; None when the
    /// distance is already smaller than a tap can resolve.
    pub fn choose(&self, need: f64) -> Option<TapRow> {
        if let Some(r) = self.rows.iter().rev().find(|r| r.dx <= need * 1.15) {
            return Some(*r);
        }
        let shortest = self.rows[0];
        (need >= shortest.dx * 0.5).then_some(shortest)
    }

    /// The smallest distance the table can resolve.
    pub fn resolution(&self) -> f64 {
        self.rows[0].dx
    }
}

/// Walking pace while a key is held, and the carry after release.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct WalkStats {
    pub speed: f64,
    pub slide: f64,
}

impl WalkStats {
    pub fn from_rows(rows: &[Value]) -> Option<Self> {
        let r = rows.first()?;
        let speed = r.get("speed")?.as_f64()?;
        (speed > 0.0).then(|| WalkStats {
            speed,
            slide: r.get("slide").and_then(Value::as_f64).unwrap_or(0.0),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn table() -> TapTable {
        let rows: Vec<Value> = [(30, 2.0), (80, 8.0), (50, 4.0), (180, 22.0), (120, 14.0)]
            .iter()
            .map(|(ms, dx)| json!({"ms": ms, "n": 4, "dx": dx, "sd": 0.5}))
            .collect();
        TapTable::from_rows(&rows).unwrap()
    }

    #[test]
    fn rows_are_sorted_and_empty_ones_dropped() {
        let t = table();
        assert_eq!(t.rows().first().unwrap().secs, 0.03);
        assert_eq!(t.rows().last().unwrap().secs, 0.18);
        let none = [
            json!({"ms": 50, "n": 0, "dx": 3.0}),
            json!({"ms": 60, "n": 2, "dx": 0.0}),
        ];
        assert!(TapTable::from_rows(&none).is_none());
    }

    #[test]
    fn choose_takes_the_longest_tap_that_fits() {
        let t = table();
        assert_eq!(t.choose(10.0).unwrap().dx, 8.0);
        assert_eq!(t.choose(30.0).unwrap().dx, 22.0);
        assert_eq!(t.choose(4.4).unwrap().dx, 4.0);
        assert_eq!(t.choose(9.0).unwrap().dx, 8.0); // 15% allowance keeps 8 for 9
    }

    #[test]
    fn choose_gives_up_below_what_a_tap_resolves() {
        let t = table();
        assert_eq!(t.choose(1.2).unwrap().dx, 2.0); // over half a shortest tap
        assert!(t.choose(0.8).is_none());
        assert_eq!(t.resolution(), 2.0);
    }

    #[test]
    fn walk_stats_need_a_speed() {
        assert_eq!(
            WalkStats::from_rows(&[json!({"speed": 42.0, "slide": 1.5, "n": 2})]),
            Some(WalkStats {
                speed: 42.0,
                slide: 1.5
            })
        );
        assert!(WalkStats::from_rows(&[json!({"speed": 0.0})]).is_none());
        assert!(WalkStats::from_rows(&[]).is_none());
    }
}
