//! Learned move reach and its file, `nav_reach[_<class>].json`:
//!
//! ```json
//! {
//!   "est":      {"jump": {"dx": 10.0, "rise": 4.0}, ...},
//!   "ceiling":  {"up_flash": {"dx": null, "rise": 12.6}},   // null = no cap
//!   "measured": {"flash": 1790646311.65, ...},              // epoch seconds
//!   "profiles": {"up_flash": {"at": 1790..., "rows": [...]}} // timing sweeps
//! }
//! ```
//!
//! Learning rules:
//! - a success grows the envelope to what was observed (never shrinks it);
//! - a miss that fell short of the plan caps further exploration there
//!   (a ceiling); a second one in a row shrinks the envelope, never below
//!   half the base;
//! - a miss that went at least as far as planned (overshot, or came down
//!   on another platform) says nothing about reach and is ignored;
//! - a deliberate measurement (`calibrate`) sets the envelope outright.
//!
//! The planner may explore up to `explore` x the envelope (capped by the
//! ceiling) at a cost penalty, so estimates grow from conservative starts.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

use crate::config::BotConfig;
use crate::error::Result;
use crate::fileio::write_text_atomic;
use crate::json::{as_f64, dumps};

/// The move kinds whose reach is learned, in file order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Move {
    Jump,
    Flash,
    DoubleFlash,
    UpFlash,
    UpSideFlash,
    RopeLift,
    Teleport,
}

impl Move {
    pub const ALL: [Move; 7] = [
        Move::Jump,
        Move::Flash,
        Move::DoubleFlash,
        Move::UpFlash,
        Move::UpSideFlash,
        Move::RopeLift,
        Move::Teleport,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Move::Jump => "jump",
            Move::Flash => "flash",
            Move::DoubleFlash => "double_flash",
            Move::UpFlash => "up_flash",
            Move::UpSideFlash => "up_side_flash",
            Move::RopeLift => "rope_lift",
            Move::Teleport => "teleport",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        Move::ALL.into_iter().find(|m| m.as_str() == s)
    }

    fn index(self) -> usize {
        self as usize
    }

    /// Sideways moves learn dx only; upward ones rise only; diagonal both.
    fn family(self) -> Family {
        match self {
            Move::Jump | Move::Flash | Move::DoubleFlash => Family::Horizontal,
            Move::UpFlash | Move::RopeLift => Family::Upward,
            Move::UpSideFlash | Move::Teleport => Family::Diagonal,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Family {
    Horizontal,
    Upward,
    Diagonal,
}

impl Family {
    fn learns_dx(self) -> bool {
        self != Family::Upward
    }

    fn learns_rise(self) -> bool {
        self != Family::Horizontal
    }
}

fn epoch_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

const SAVE_EVERY: Duration = Duration::from_secs(10);

/// One move in a [`ReachModel::snapshot`]: estimate dx and rise, and the
/// ceiling if any, in 0.1px units.
pub type SnapshotRow = (Move, i64, i64, Option<(i64, i64)>);

/// Sideways distance and rise a move covers (minimap px).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Reach {
    pub dx: f64,
    pub rise: f64,
}

/// Conservative starting envelopes from the config's `nav_*` keys.
pub fn base_reach(cfg: &BotConfig) -> [Reach; 7] {
    let r = |dx, rise| Reach { dx, rise };
    [
        r(cfg.nav_jump_px, 4.0),
        r(cfg.nav_gap_px, 4.0),
        r(cfg.nav_double_gap_px, 4.0),
        r(6.0, cfg.nav_up_flash_px),
        r(cfg.nav_up_side_dx_px, cfg.nav_up_flash_px * 0.8),
        r(3.0, cfg.nav_rope_lift_px),
        r(cfg.nav_teleport_dx, cfg.nav_teleport_rise),
    ]
}

#[derive(Debug, Clone, PartialEq)]
pub struct ReachModel {
    pub base: [Reach; 7],
    /// Current envelope per move (indexed by `Move as usize`).
    pub est: [Reach; 7],
    /// Failure caps on exploration, in the order they were set; infinite
    /// = no cap on that dimension.
    pub ceiling: Vec<(Move, Reach)>,
    /// Measurement name (e.g. `teleport_up`) → epoch seconds.
    pub measured: Vec<(String, f64)>,
    /// Timing sweeps, e.g. `up_flash` → `{"at": ..., "rows": [...]}`.
    pub profiles: Map<String, Value>,
    pub path: Option<PathBuf>,
    /// Exploration factor over the envelope.
    pub explore: f64,
    /// Envelope factor on a confirmed shortfall.
    pub shrink: f64,
    /// Bumped on every change: graph caches key on it.
    pub version: u64,
    fail_streak: [u32; 7],
    dirty: bool,
    saved_at: Option<Instant>,
}

impl ReachModel {
    pub fn new(base: [Reach; 7], path: Option<PathBuf>) -> Self {
        ReachModel {
            base,
            est: base,
            ceiling: Vec::new(),
            measured: Vec::new(),
            profiles: Map::new(),
            path,
            explore: 1.3,
            shrink: 0.95,
            version: 0,
            fail_streak: [0; 7],
            dirty: false,
            saved_at: None,
        }
    }

    /// Create from `base`, then load `path` if it exists and parses (a bad
    /// file keeps the base envelopes, as the Python host does).
    pub fn load(base: [Reach; 7], path: impl Into<PathBuf>) -> Self {
        let path = path.into();
        let mut model = ReachModel::new(base, Some(path.clone()));
        if let Ok(text) = std::fs::read_to_string(&path) {
            if let Ok(v) = serde_json::from_str::<Value>(&text) {
                model.apply_json(&v);
            }
        }
        model
    }

    pub fn get(&self, m: Move) -> Reach {
        self.est[m.index()]
    }

    pub fn ceiling_of(&self, m: Move) -> Option<Reach> {
        self.ceiling.iter().find(|(k, _)| *k == m).map(|(_, r)| *r)
    }

    /// The ceiling for `m`, created uncapped (infinite) if missing.
    fn ceiling_mut(&mut self, m: Move) -> &mut Reach {
        let i = match self.ceiling.iter().position(|(k, _)| *k == m) {
            Some(i) => i,
            None => {
                self.ceiling.push((
                    m,
                    Reach {
                        dx: f64::INFINITY,
                        rise: f64::INFINITY,
                    },
                ));
                self.ceiling.len() - 1
            }
        };
        &mut self.ceiling[i].1
    }

    /// Largest envelope the planner may attempt (exploration included).
    pub fn limit(&self, m: Move) -> Reach {
        let e = self.get(m);
        let mut lim = Reach {
            dx: e.dx * self.explore,
            rise: e.rise * self.explore,
        };
        if let Some(c) = self.ceiling_of(m) {
            lim.dx = lim.dx.min(e.dx.max(c.dx));
            lim.rise = lim.rise.min(e.rise.max(c.rise));
        }
        lim
    }

    /// None = out of reach; Some(true) = proven; Some(false) = exploratory.
    pub fn fits(&self, m: Move, dx: f64, rise: f64) -> Option<bool> {
        let lim = self.limit(m);
        if dx > lim.dx || rise > lim.rise {
            return None;
        }
        let e = self.get(m);
        Some(dx <= e.dx && rise <= e.rise)
    }

    /// Estimates and ceilings rounded to 0.1px: a cache key for graphs.
    pub fn snapshot(&self) -> Vec<SnapshotRow> {
        let q = |v: f64| {
            if v.is_finite() {
                (v * 10.0).round() as i64
            } else {
                i64::MAX
            }
        };
        Move::ALL
            .iter()
            .map(|&m| {
                let e = self.get(m);
                (
                    m,
                    q(e.dx),
                    q(e.rise),
                    self.ceiling_of(m).map(|c| (q(c.dx), q(c.rise))),
                )
            })
            .collect()
    }

    /// Learn from one executed move: `planned` and `observed` are
    /// (|dx|, rise) with rise positive upward.
    pub fn observe(&mut self, m: Move, planned: (f64, f64), observed: (f64, f64), ok: bool) {
        let fam = m.family();
        let i = m.index();
        let before = (self.est[i], self.ceiling_of(m));
        let (pdx, prise) = planned;
        let (odx, orise) = observed;
        if ok {
            self.fail_streak[i] = 0;
            let e = &mut self.est[i];
            if fam.learns_dx() {
                e.dx = e.dx.max(odx);
            }
            if fam.learns_rise() {
                e.rise = e.rise.max(orise);
            }
        } else {
            let short_dx = odx < pdx - 2.0;
            let short_rise = orise < prise - 2.0;
            let short = match fam {
                Family::Horizontal => short_dx,
                Family::Upward => short_rise,
                Family::Diagonal => short_dx || short_rise,
            };
            if !short {
                return;
            }
            self.fail_streak[i] += 1;
            let streak = self.fail_streak[i];
            let (base, shrink, est) = (self.base[i], self.shrink, self.est[i]);
            let c = self.ceiling_mut(m);
            if fam.learns_dx() {
                c.dx = c.dx.min(pdx * 0.97);
            }
            if fam.learns_rise() {
                c.rise = c.rise.min(prise * 0.97);
            }
            if streak >= 2 {
                // One miss may be input timing; a second in a row is a real
                // shortfall: shrink, but never below half the base.
                let e = &mut self.est[i];
                if fam.learns_dx() && pdx <= est.dx {
                    e.dx = (base.dx * 0.5).max(pdx * shrink);
                }
                if fam.learns_rise() && prise <= est.rise {
                    e.rise = (base.rise * 0.5).max(prise * shrink);
                }
            }
        }
        if (self.est[i], self.ceiling_of(m)) != before {
            self.version += 1;
            self.dirty = true;
        }
        let _ = self.save(false);
    }

    /// A deliberate measurement: set the given dimensions outright (lower
    /// included), drop their ceilings, and record it under `tag`.
    pub fn calibrate(&mut self, m: Move, dx: Option<f64>, rise: Option<f64>, tag: Option<&str>) {
        let i = m.index();
        if let Some(dx) = dx {
            self.est[i].dx = dx;
        }
        if let Some(rise) = rise {
            self.est[i].rise = rise;
        }
        if let Some(pos) = self.ceiling.iter().position(|(k, _)| *k == m) {
            let c = &mut self.ceiling[pos].1;
            if dx.is_some() {
                c.dx = f64::INFINITY;
            }
            if rise.is_some() {
                c.rise = f64::INFINITY;
            }
            if c.dx.is_infinite() && c.rise.is_infinite() {
                self.ceiling.remove(pos);
            }
        }
        self.fail_streak[i] = 0;
        let tag = tag.unwrap_or(m.as_str()).to_owned();
        let now = epoch_now();
        match self.measured.iter_mut().find(|(k, _)| *k == tag) {
            Some(slot) => slot.1 = now,
            None => self.measured.push((tag, now)),
        }
        self.version += 1;
        self.dirty = true;
    }

    pub fn is_measured(&self, tag: &str) -> bool {
        self.measured.iter().any(|(k, _)| k == tag)
    }

    /// Store a timing sweep (`rows` as the measurer produced them).
    pub fn set_profile(&mut self, name: &str, rows: Vec<Value>) {
        self.profiles
            .insert(name.to_owned(), json!({"at": epoch_now(), "rows": rows}));
        self.version += 1;
        self.dirty = true;
    }

    /// The calibrated walk taps, if a sweep has been saved.
    pub fn tap_table(&self) -> Option<crate::taps::TapTable> {
        let rows = self.profiles.get("walk_taps")?.get("rows")?.as_array()?;
        crate::taps::TapTable::from_rows(rows)
    }

    /// What each measured skill does to a flash jump (see `effects`).
    pub fn skill_effects(&self) -> Vec<crate::effects::SkillEffect> {
        self.profiles
            .get("skill_effects")
            .and_then(|p| p.get("rows"))
            .and_then(Value::as_array)
            .map(|rows| crate::effects::SkillEffect::from_rows(rows))
            .unwrap_or_default()
    }

    /// The measured walking pace and slide, if saved.
    pub fn walk_stats(&self) -> Option<crate::taps::WalkStats> {
        let rows = self.profiles.get("walk_speed")?.get("rows")?.as_array()?;
        crate::taps::WalkStats::from_rows(rows)
    }

    /// (lo, mid, hi) re-press delays around a sweep's highest peak: the
    /// contiguous run of delays within `slack` px of it.
    pub fn plateau(&self, name: &str, slack: f64) -> Option<(f64, f64, f64)> {
        let rows = self.profiles.get(name)?.get("rows")?.as_array()?;
        let mut pts: Vec<(f64, f64)> = rows
            .iter()
            .filter(|r| r.get("n").is_some_and(crate::json::truthy))
            .filter_map(|r| Some((r.get("delay")?.as_f64()?, r.get("rise")?.as_f64()?)))
            .collect();
        if pts.is_empty() {
            return None;
        }
        pts.sort_by(|a, b| a.0.total_cmp(&b.0));
        // Highest rise; the earliest delay wins a tie.
        let best = (0..pts.len())
            .max_by(|&a, &b| pts[a].1.total_cmp(&pts[b].1).then(b.cmp(&a)))
            .unwrap();
        let floor = pts[best].1 - slack;
        let (mut lo, mut hi) = (best, best);
        while lo > 0 && pts[lo - 1].1 >= floor {
            lo -= 1;
        }
        while hi + 1 < pts.len() && pts[hi + 1].1 >= floor {
            hi += 1;
        }
        let (a, b) = (pts[lo].0, pts[hi].0);
        Some((a, (a + b) / 2.0, b))
    }

    fn apply_json(&mut self, v: &Value) {
        let reach = |r: &Value, inf_for_null: bool| -> Option<Reach> {
            let side = |k: &str| match r.get(k) {
                Some(Value::Null) | None if inf_for_null => Some(f64::INFINITY),
                Some(x) => as_f64(x),
                None => None,
            };
            Some(Reach {
                dx: side("dx")?,
                rise: side("rise")?,
            })
        };
        if let Some(Value::Object(est)) = v.get("est") {
            for (k, r) in est {
                if let (Some(m), Some(r)) = (Move::parse(k), reach(r, false)) {
                    self.est[m.index()] = r;
                }
            }
        }
        if let Some(Value::Object(ceil)) = v.get("ceiling") {
            self.ceiling = ceil
                .iter()
                .filter_map(|(k, r)| Some((Move::parse(k)?, reach(r, true)?)))
                .collect();
        }
        if let Some(Value::Object(meas)) = v.get("measured") {
            self.measured = meas
                .iter()
                .filter_map(|(k, t)| Some((k.clone(), as_f64(t)?)))
                .collect();
        }
        if let Some(Value::Object(profiles)) = v.get("profiles") {
            self.profiles = profiles
                .iter()
                .filter(|(_, p)| p.get("rows").is_some_and(Value::is_array))
                .map(|(k, p)| (k.clone(), p.clone()))
                .collect();
        }
    }

    pub fn to_json(&self) -> Value {
        let finite = |f: f64| if f.is_finite() { json!(f) } else { Value::Null };
        let est: Map<String, Value> = Move::ALL
            .iter()
            .map(|m| {
                let r = self.est[m.index()];
                (m.as_str().to_owned(), json!({"dx": r.dx, "rise": r.rise}))
            })
            .collect();
        let ceiling: Map<String, Value> = self
            .ceiling
            .iter()
            .filter(|(_, r)| r.dx.is_finite() || r.rise.is_finite())
            .map(|(m, r)| {
                (
                    m.as_str().to_owned(),
                    json!({"dx": finite(r.dx), "rise": finite(r.rise)}),
                )
            })
            .collect();
        let measured: Map<String, Value> = self
            .measured
            .iter()
            .map(|(k, t)| (k.clone(), json!(t)))
            .collect();
        let mut doc = Map::new();
        doc.insert("est".into(), Value::Object(est));
        doc.insert("ceiling".into(), Value::Object(ceiling));
        doc.insert("measured".into(), Value::Object(measured));
        if !self.profiles.is_empty() {
            doc.insert("profiles".into(), Value::Object(self.profiles.clone()));
        }
        Value::Object(doc)
    }

    /// Write to the model's path (2-space indents, like the Python host)
    /// when something changed; at most every 10s unless `force`d.
    pub fn save(&mut self, force: bool) -> Result<()> {
        let Some(path) = self.path.clone() else {
            return Ok(());
        };
        if !self.dirty {
            return Ok(());
        }
        if !force && self.saved_at.is_some_and(|t| t.elapsed() < SAVE_EVERY) {
            return Ok(());
        }
        write_to(&path, &self.to_json())?;
        self.dirty = false;
        self.saved_at = Some(Instant::now());
        Ok(())
    }
}

fn write_to(path: &Path, v: &Value) -> Result<()> {
    write_text_atomic(path, &dumps(v, 2))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nulls_in_a_ceiling_mean_no_cap_and_round_trip() {
        let doc = json!({
            "est": {"up_flash": {"dx": 6.0, "rise": 25.0}},
            "ceiling": {"rope_lift": {"dx": null, "rise": 12.6}},
            "measured": {"flash": 1790646311.6497967},
            "profiles": {"up_flash": {"at": 1.0, "rows": []}, "bad": {"at": 1.0}}
        });
        let mut m = ReachModel::new(base_reach(&BotConfig::default()), None);
        m.apply_json(&doc);
        assert_eq!(m.get(Move::UpFlash).rise, 25.0);
        assert_eq!(m.ceiling_of(Move::RopeLift).unwrap().dx, f64::INFINITY);
        assert!(!m.profiles.contains_key("bad"));
        let out = m.to_json();
        assert_eq!(
            out["ceiling"],
            json!({"rope_lift": {"dx": null, "rise": 12.6}})
        );
        assert_eq!(out["measured"]["flash"], json!(1790646311.6497967));
        assert_eq!(out["est"].as_object().unwrap().len(), 7);
    }
}

#[cfg(test)]
mod learning_tests {
    use super::*;

    /// The Python suite's small envelopes: flash 20, up flash 14 rise,
    /// up-side flash 16 x 11.
    fn model() -> ReachModel {
        let mut base = base_reach(&BotConfig::default());
        base[Move::Flash.index()] = Reach {
            dx: 20.0,
            rise: 4.0,
        };
        base[Move::UpFlash.index()] = Reach {
            dx: 6.0,
            rise: 14.0,
        };
        base[Move::UpSideFlash.index()] = Reach {
            dx: 16.0,
            rise: 11.0,
        };
        ReachModel::new(base, None)
    }

    #[test]
    fn defaults_come_from_config() {
        let b = base_reach(&BotConfig::default());
        assert_eq!(b[Move::Flash.index()].dx, 30.0);
        assert_eq!(b[Move::UpFlash.index()].rise, 26.0);
        assert_eq!(b[Move::RopeLift.index()].rise, 90.0);
    }

    #[test]
    fn fits_proven_exploratory_and_out() {
        let m = model();
        assert_eq!(m.fits(Move::Flash, 18.0, 0.0), Some(true));
        assert_eq!(m.fits(Move::Flash, 22.0, 0.0), Some(false));
        assert_eq!(m.fits(Move::Flash, 30.0, 0.0), None);
    }

    #[test]
    fn success_grows_to_the_observation() {
        let mut m = model();
        m.observe(Move::Flash, (22.0, 0.0), (26.0, 0.0), true);
        assert_eq!(m.get(Move::Flash).dx, 26.0);
        m.observe(Move::UpFlash, (0.0, 13.0), (1.0, 17.0), true);
        assert_eq!(m.get(Move::UpFlash).rise, 17.0);
        assert_eq!(m.get(Move::UpFlash).dx, 6.0); // drift not learned
    }

    #[test]
    fn second_consecutive_shortfall_shrinks_to_a_floor() {
        let mut m = model();
        m.observe(Move::Flash, (18.0, 0.0), (10.0, -5.0), false);
        assert_eq!(m.get(Move::Flash).dx, 20.0); // one miss: no shrink
        m.observe(Move::Flash, (18.0, 0.0), (10.0, -5.0), false);
        assert!((m.get(Move::Flash).dx - 18.0 * 0.95).abs() < 1e-9);
        m.observe(Move::Flash, (10.0, 0.0), (14.0, 0.0), true);
        assert!((m.get(Move::Flash).dx - 18.0 * 0.95).abs() < 1e-9); // stays shrunk
        m.observe(Move::Flash, (16.0, 0.0), (9.0, -5.0), false);
        assert!((m.get(Move::Flash).dx - 18.0 * 0.95).abs() < 1e-9); // streak restarted
        m.observe(Move::Flash, (16.0, 0.0), (9.0, -5.0), false);
        assert!((m.get(Move::Flash).dx - 15.2).abs() < 1e-9);
    }

    #[test]
    fn exploratory_failure_only_sets_a_ceiling() {
        let mut m = model();
        m.observe(Move::Flash, (22.0, 0.0), (12.0, -8.0), false);
        assert_eq!(m.get(Move::Flash).dx, 20.0);
        assert_eq!(m.fits(Move::Flash, 22.0, 0.0), None); // no retry at 22
        assert_eq!(m.fits(Move::Flash, 21.0, 0.0), Some(false));
    }

    #[test]
    fn shrinking_never_goes_below_half_the_base() {
        let mut m = model();
        for _ in 0..20 {
            let e = m.get(Move::UpFlash).rise;
            m.observe(Move::UpFlash, (0.0, e), (0.0, 0.0), false);
        }
        assert!(m.get(Move::UpFlash).rise >= 7.0);
    }

    #[test]
    fn overshoot_is_not_a_reach_failure() {
        let mut m = model();
        let before = m.get(Move::UpFlash).rise;
        for _ in 0..3 {
            m.observe(Move::UpFlash, (0.0, 16.0), (0.0, 23.0), false);
        }
        assert_eq!(m.get(Move::UpFlash).rise, before);
        assert!(m.ceiling_of(Move::UpFlash).is_none());
    }

    #[test]
    fn calibrate_sets_even_below_the_guess_and_clears_the_ceiling() {
        let mut m = model();
        m.observe(Move::Flash, (18.0, 0.0), (0.0, 0.0), false);
        m.observe(Move::Flash, (18.0, 0.0), (0.0, 0.0), false);
        assert!(m.ceiling_of(Move::Flash).is_some());
        m.calibrate(Move::Flash, Some(12.0), None, None);
        assert_eq!(
            m.get(Move::Flash),
            Reach {
                dx: 12.0,
                rise: 4.0
            }
        );
        assert!(m.ceiling_of(Move::Flash).is_none());
        assert!(m.is_measured("flash"));
    }

    #[test]
    fn version_moves_only_on_change() {
        let mut m = model();
        let v = m.version;
        m.observe(Move::Flash, (10.0, 0.0), (12.0, 0.0), true); // within envelope
        assert_eq!(m.version, v);
        m.observe(Move::Flash, (10.0, 0.0), (25.0, 0.0), true);
        assert!(m.version > v);
    }

    #[test]
    fn plateau_is_the_flat_top_around_the_best_peak() {
        let mut m = model();
        assert!(m.plateau("up_flash", 1.0).is_none());
        m.set_profile(
            "up_flash",
            vec![
                json!({"delay": null, "n": 3, "rise": 7.3}),
                json!({"delay": 0.08, "n": 3, "rise": 23.0}),
                json!({"delay": 0.16, "n": 3, "rise": 24.7}),
                json!({"delay": 0.20, "n": 3, "rise": 25.7}),
                json!({"delay": 0.25, "n": 3, "rise": 26.0}),
                json!({"delay": 0.30, "n": 3, "rise": 26.0}),
                json!({"delay": 0.36, "n": 3, "rise": 24.3}),
                json!({"delay": 0.44, "n": 0, "rise": null}),
            ],
        );
        let (lo, mid, hi) = m.plateau("up_flash", 1.0).unwrap();
        assert_eq!((lo, hi), (0.20, 0.30));
        assert!((mid - 0.25).abs() < 1e-12);
    }

    #[test]
    fn saves_and_reloads_learning_and_profiles() {
        let dir = std::env::temp_dir().join(format!("picobot-reach-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("reach.json");
        let mut m = model();
        m.path = Some(path.clone());
        m.observe(Move::Flash, (22.0, 0.0), (27.0, 0.0), true);
        m.observe(Move::UpFlash, (0.0, 16.0), (0.0, 2.0), false);
        m.observe(Move::UpFlash, (0.0, 16.0), (0.0, 2.0), false);
        m.set_profile("up_flash", vec![json!({"delay": 0.2, "rise": 18.0})]);
        m.save(true).unwrap();
        let data: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert!(data["ceiling"]["up_flash"]["dx"].is_null()); // inf -> null
        let again = ReachModel::load(m.base, &path);
        assert_eq!(again.get(Move::Flash).dx, 27.0);
        assert_eq!(again.snapshot(), m.snapshot());
        assert_eq!(
            again.profiles["up_flash"]["rows"],
            json!([{"delay": 0.2, "rise": 18.0}])
        );
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
