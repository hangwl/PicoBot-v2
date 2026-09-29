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
//! This milestone covers the data and the file; learning comes with M2.

use std::path::{Path, PathBuf};

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
}

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

    /// Write to the model's path (2-space indents, like the Python host).
    pub fn save(&self) -> Result<()> {
        if let Some(path) = &self.path {
            write_to(path, &self.to_json())?;
        }
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
