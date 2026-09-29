//! Skill definitions as stored in config and map files.
//!
//! ```json
//! "skills": {
//!   "burst": {"key": "s", "kind": "attack", "cooldown": 28},
//!   "orb":   {"key": "d", "kind": "summon", "cooldown": 60, "charges": 2, "duration": 90}
//! }
//! ```

use serde_json::{Map, Value};

use crate::error::{bad, Result};
use crate::json::{as_f64, as_i64, as_string};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkillKind {
    Attack,
    Buff,
    Summon,
    Movement,
}

impl SkillKind {
    pub fn as_str(self) -> &'static str {
        match self {
            SkillKind::Attack => "attack",
            SkillKind::Buff => "buff",
            SkillKind::Summon => "summon",
            SkillKind::Movement => "movement",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "attack" => SkillKind::Attack,
            "buff" => SkillKind::Buff,
            "summon" => SkillKind::Summon,
            "movement" => SkillKind::Movement,
            _ => return None,
        })
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Skill {
    pub name: String,
    pub key: String,
    /// Seconds between uses; 0 = always ready.
    pub cooldown: f64,
    pub kind: SkillKind,
    /// Kept for file compatibility; anchors are pass-through.
    pub wait_on_arrival: f64,
    /// Key-hold override (None = humanised tap).
    pub hold: Option<f64>,
    /// Stored uses; one returns per cooldown.
    pub charges: u32,
    /// Summon uptime in seconds (0 = until its cooldown ends).
    pub duration: f64,
}

impl Skill {
    pub fn new(name: &str, key: &str) -> Self {
        Skill {
            name: name.into(),
            key: key.into(),
            cooldown: 0.0,
            kind: SkillKind::Attack,
            wait_on_arrival: 0.0,
            hold: None,
            charges: 1,
            duration: 0.0,
        }
    }

    /// How long one cast stays out: its duration, else its cooldown.
    pub fn uptime(&self) -> f64 {
        if self.duration != 0.0 {
            self.duration
        } else {
            self.cooldown
        }
    }

    pub fn from_json(name: &str, data: &Value) -> Result<Self> {
        let obj = match data.as_object() {
            Some(o) => o,
            None => return bad(format!("skill {name:?}: must be an object")),
        };
        let kind_str = obj
            .get("kind")
            .map(as_string)
            .unwrap_or_else(|| "attack".into());
        let Some(kind) = SkillKind::parse(&kind_str) else {
            return bad(format!(
                "skill {name:?}: kind must be one of attack, buff, summon, movement"
            ));
        };
        let Some(key) = obj.get("key") else {
            return bad(format!("skill {name:?}: missing 'key'"));
        };
        let num = |field: &str, default: f64| -> Result<f64> {
            match obj.get(field) {
                None => Ok(default),
                Some(v) => as_f64(v).ok_or_else(|| {
                    crate::Error::Format(format!("skill {name:?}: {field} must be a number"))
                }),
            }
        };
        let charges = match obj.get("charges") {
            None => 1,
            Some(v) => as_i64(v).ok_or_else(|| {
                crate::Error::Format(format!("skill {name:?}: charges must be a number"))
            })?,
        };
        let hold = match obj.get("hold") {
            None | Some(Value::Null) => None,
            Some(v) => Some(as_f64(v).ok_or_else(|| {
                crate::Error::Format(format!("skill {name:?}: hold must be a number"))
            })?),
        };
        Ok(Skill {
            name: name.into(),
            key: as_string(key),
            cooldown: num("cooldown", 0.0)?.max(0.0),
            kind,
            wait_on_arrival: num("wait_on_arrival", 0.0)?.max(0.0),
            hold,
            charges: charges.max(1) as u32,
            duration: num("duration", 0.0)?.max(0.0),
        })
    }

    /// The file form; defaults are left out, as the Python host does.
    pub fn to_json(&self) -> Value {
        let mut out = Map::new();
        out.insert("key".into(), self.key.clone().into());
        out.insert("kind".into(), self.kind.as_str().into());
        if self.cooldown != 0.0 {
            out.insert("cooldown".into(), self.cooldown.into());
        }
        if self.wait_on_arrival != 0.0 {
            out.insert("wait_on_arrival".into(), self.wait_on_arrival.into());
        }
        if let Some(h) = self.hold {
            out.insert("hold".into(), h.into());
        }
        if self.charges != 1 {
            out.insert("charges".into(), self.charges.into());
        }
        if self.duration != 0.0 {
            out.insert("duration".into(), self.duration.into());
        }
        Value::Object(out)
    }
}

/// A `{"name": {...}, ...}` object → skills in file order.
pub fn skills_from_json(data: &Value) -> Result<Vec<Skill>> {
    match data {
        Value::Object(map) => map
            .iter()
            .map(|(n, spec)| Skill::from_json(n, spec))
            .collect(),
        Value::Null => Ok(Vec::new()),
        _ => bad("skills must be an object"),
    }
}

pub fn skills_to_json(skills: &[Skill]) -> Value {
    Value::Object(
        skills
            .iter()
            .map(|s| (s.name.clone(), s.to_json()))
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn round_trip_leaves_defaults_out() {
        let spec =
            json!({"key": "d", "kind": "summon", "cooldown": 60.0, "charges": 2, "duration": 90.0});
        let s = Skill::from_json("orb", &spec).unwrap();
        assert_eq!(s.charges, 2);
        assert_eq!(s.uptime(), 90.0);
        assert_eq!(s.to_json(), spec);
        assert_eq!(
            Skill::new("a", "a").to_json(),
            json!({"key": "a", "kind": "attack"})
        );
    }

    #[test]
    fn rejects_unknown_kind_and_missing_key() {
        assert!(Skill::from_json("x", &json!({"key": "a", "kind": "dance"})).is_err());
        assert!(Skill::from_json("x", &json!({"kind": "attack"})).is_err());
    }
}
