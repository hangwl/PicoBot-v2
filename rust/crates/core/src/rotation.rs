//! A map's farming route: anchors (positions to pass through) and
//! optional hand-recorded legs between them.
//!
//! Coordinates are normalised 0–1 fractions of the minimap region; values
//! above 1 are absolute minimap pixels (hand-tuned configs).

use std::collections::{BTreeMap, HashMap, VecDeque};

use serde_json::{json, Map, Value};

use crate::error::{bad, Result};
use crate::json::{as_f64, as_i64, as_string};

/// Normalised 0–1 → `value * span` px; above 1 is already pixels.
pub fn resolve_coord(value: f64, span: i64) -> i64 {
    if (0.0..=1.0).contains(&value) {
        py_round(value * span as f64)
    } else {
        py_round(value)
    }
}

/// Python's `round()`: halves go to the even neighbour.
pub fn py_round(v: f64) -> i64 {
    let r = v.round();
    if (v - v.trunc()).abs() == 0.5 && (r as i64) % 2 != 0 {
        (r - v.signum()) as i64
    } else {
        r as i64
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TravelStyle {
    Walk,
    Flash,
    Mixed,
}

impl TravelStyle {
    pub fn as_str(self) -> &'static str {
        match self {
            TravelStyle::Walk => "walk",
            TravelStyle::Flash => "flash",
            TravelStyle::Mixed => "mixed",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "walk" => TravelStyle::Walk,
            "flash" => TravelStyle::Flash,
            "mixed" => TravelStyle::Mixed,
            _ => return None,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClimbDir {
    Up,
    Down,
}

/// One micro-step of a recorded leg.
#[derive(Debug, Clone, PartialEq)]
pub enum Step {
    WalkTo {
        x: f64,
        y: f64,
        style: Option<TravelStyle>,
    },
    Climb {
        dir: ClimbDir,
        until_y: f64,
        x: Option<f64>,
    },
    UpJump,
    DownJump,
    Wait {
        seconds: f64,
    },
}

fn number(v: Option<&Value>, what: &str) -> Result<f64> {
    v.and_then(as_f64)
        .ok_or_else(|| crate::Error::Format(format!("{what} must be a number")))
}

impl Step {
    pub fn from_json(data: &Value) -> Result<Self> {
        let Some(obj) = data.as_object() else {
            return bad(format!("leg step must be an object, got {data}"));
        };
        if let Some(to) = obj.get("walk_to") {
            let pair = to.as_array().filter(|a| a.len() == 2);
            let Some(pair) = pair else {
                return bad("walk_to must be [x, y]");
            };
            let style = match obj.get("style") {
                None | Some(Value::Null) => None,
                Some(s) => Some(TravelStyle::parse(&as_string(s)).ok_or_else(|| {
                    crate::Error::Format("walk_to style must be one of walk, flash, mixed".into())
                })?),
            };
            return Ok(Step::WalkTo {
                x: number(pair.first(), "walk_to x")?,
                y: number(pair.get(1), "walk_to y")?,
                style,
            });
        }
        if let Some(spec) = obj.get("climb") {
            let empty = Map::new();
            let spec = spec.as_object().unwrap_or(&empty);
            let dir = match spec.get("dir").map(as_string).as_deref().unwrap_or("up") {
                "up" => ClimbDir::Up,
                "down" => ClimbDir::Down,
                _ => return bad("climb dir must be one of up, down"),
            };
            if !spec.contains_key("until_y") {
                return bad("climb step requires 'until_y'");
            }
            let x = match spec.get("x") {
                None | Some(Value::Null) => None,
                v => Some(number(v, "climb x")?),
            };
            return Ok(Step::Climb {
                dir,
                until_y: number(spec.get("until_y"), "until_y")?,
                x,
            });
        }
        if obj.contains_key("up_jump") {
            return Ok(Step::UpJump);
        }
        if obj.contains_key("down_jump") {
            return Ok(Step::DownJump);
        }
        if let Some(w) = obj.get("wait") {
            return Ok(Step::Wait {
                seconds: number(Some(w), "wait")?.max(0.0),
            });
        }
        bad(format!("unknown leg step: {data}"))
    }

    pub fn to_json(&self) -> Value {
        match self {
            Step::WalkTo { x, y, style } => {
                let mut out = Map::new();
                out.insert("walk_to".into(), json!([x, y]));
                if let Some(s) = style {
                    out.insert("style".into(), s.as_str().into());
                }
                Value::Object(out)
            }
            Step::Climb { dir, until_y, x } => {
                let mut spec = Map::new();
                let d = if *dir == ClimbDir::Up { "up" } else { "down" };
                spec.insert("dir".into(), d.into());
                spec.insert("until_y".into(), (*until_y).into());
                if let Some(x) = x {
                    spec.insert("x".into(), (*x).into());
                }
                json!({ "climb": spec })
            }
            Step::UpJump => json!({"up_jump": true}),
            Step::DownJump => json!({"down_jump": true}),
            Step::Wait { seconds } => json!({ "wait": seconds }),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Face {
    Left,
    Right,
}

/// A farming spot the patrol passes through.
#[derive(Debug, Clone, PartialEq)]
pub struct Anchor {
    pub name: String,
    pub x: f64,
    pub y: f64,
    /// Skill names to fire on arrival.
    pub on_arrive: Vec<String>,
    /// Direction to tap after arriving.
    pub face: Option<Face>,
}

impl Anchor {
    pub fn new(name: &str, x: f64, y: f64) -> Self {
        Anchor {
            name: name.into(),
            x,
            y,
            on_arrive: Vec::new(),
            face: None,
        }
    }

    pub fn from_json(data: &Value, index: usize) -> Result<Self> {
        let pos = data
            .get("pos")
            .and_then(Value::as_array)
            .filter(|p| p.len() == 2);
        let Some(pos) = pos else {
            return bad(format!("anchor #{index}: 'pos' must be [x, y]"));
        };
        let face = match data.get("face") {
            None | Some(Value::Null) => None,
            Some(Value::String(s)) if s == "left" => Some(Face::Left),
            Some(Value::String(s)) if s == "right" => Some(Face::Right),
            _ => return bad(format!("anchor #{index}: 'face' must be left|right")),
        };
        let on_arrive = match data.get("on_arrive") {
            Some(Value::Array(items)) => items.iter().map(as_string).collect(),
            _ => Vec::new(),
        };
        Ok(Anchor {
            name: data
                .get("name")
                .map(as_string)
                .unwrap_or_else(|| format!("anchor_{index}")),
            x: number(pos.first(), "anchor x")?,
            y: number(pos.get(1), "anchor y")?,
            on_arrive,
            face,
        })
    }

    pub fn to_json(&self) -> Value {
        let mut out = Map::new();
        out.insert("name".into(), self.name.clone().into());
        out.insert("pos".into(), json!([self.x, self.y]));
        if !self.on_arrive.is_empty() {
            out.insert("on_arrive".into(), json!(self.on_arrive));
        }
        if let Some(f) = self.face {
            out.insert(
                "face".into(),
                (if f == Face::Left { "left" } else { "right" }).into(),
            );
        }
        Value::Object(out)
    }
}

/// Anchor list + recorded leg graph.
#[derive(Debug, Clone, PartialEq)]
pub struct Rotation {
    pub anchors: Vec<Anchor>,
    /// `(from, to)` anchor indices → steps. A BTreeMap keeps them sorted,
    /// which is the order the Python host writes them in.
    pub legs: BTreeMap<(usize, usize), Vec<Step>>,
    pub position_jitter_px: i64,
    pub travel_style: TravelStyle,
}

impl Default for Rotation {
    fn default() -> Self {
        Rotation {
            anchors: Vec::new(),
            legs: BTreeMap::new(),
            position_jitter_px: 4,
            travel_style: TravelStyle::Mixed,
        }
    }
}

impl Rotation {
    /// Steps for a leg: the recorded pair, else a shortest chain of
    /// recorded legs, else a direct walk to the anchor.
    pub fn leg_steps(&self, from: usize, to: usize) -> Vec<Step> {
        if let Some(steps) = self.legs.get(&(from, to)) {
            return steps.clone();
        }
        if let Some(path) = self.leg_path(from, to) {
            return path.into_iter().flatten().collect();
        }
        let a = &self.anchors[to];
        vec![Step::WalkTo {
            x: a.x,
            y: a.y,
            style: None,
        }]
    }

    /// Breadth-first search over the recorded legs.
    fn leg_path(&self, from: usize, to: usize) -> Option<Vec<Vec<Step>>> {
        if from == to {
            return Some(Vec::new());
        }
        let mut prev: HashMap<usize, Option<usize>> = HashMap::from([(from, None)]);
        let mut queue = VecDeque::from([from]);
        while let Some(node) = queue.pop_front() {
            for &(a, b) in self.legs.keys() {
                if a != node || prev.contains_key(&b) {
                    continue;
                }
                prev.insert(b, Some(node));
                if b == to {
                    let mut pairs = Vec::new();
                    let mut cur = b;
                    while let Some(Some(p)) = prev.get(&cur) {
                        pairs.push((*p, cur));
                        cur = *p;
                    }
                    pairs.reverse();
                    return Some(pairs.iter().map(|p| self.legs[p].clone()).collect());
                }
                queue.push_back(b);
            }
        }
        None
    }

    /// Drop an anchor and its legs; later indices shift down by one.
    pub fn remove_anchor(&mut self, index: usize) {
        self.anchors.remove(index);
        let shift = |i: usize| if i > index { i - 1 } else { i };
        self.legs = std::mem::take(&mut self.legs)
            .into_iter()
            .filter(|((f, t), _)| *f != index && *t != index)
            .map(|((f, t), steps)| ((shift(f), shift(t)), steps))
            .collect();
    }

    pub fn from_json(data: &Value) -> Result<Self> {
        let Some(obj) = data.as_object().filter(|o| !o.is_empty()) else {
            return Ok(Rotation::default());
        };
        let anchors = match obj.get("anchors") {
            Some(Value::Array(items)) => items
                .iter()
                .enumerate()
                .map(|(i, a)| Anchor::from_json(a, i))
                .collect::<Result<Vec<_>>>()?,
            _ => Vec::new(),
        };
        let mut legs = BTreeMap::new();
        if let Some(Value::Array(items)) = obj.get("legs") {
            for leg in items {
                let (Some(f), Some(t)) = (
                    leg.get("from").and_then(as_i64),
                    leg.get("to").and_then(as_i64),
                ) else {
                    return bad("leg needs 'from' and 'to'");
                };
                let n = anchors.len() as i64;
                if !(0..n).contains(&f) || !(0..n).contains(&t) {
                    return bad(format!("leg ({f},{t}) out of range"));
                }
                let steps = match leg.get("steps") {
                    Some(Value::Array(s)) => {
                        s.iter().map(Step::from_json).collect::<Result<_>>()?
                    }
                    _ => Vec::new(),
                };
                legs.insert((f as usize, t as usize), steps);
            }
        }
        let style = obj
            .get("travel_style")
            .map(as_string)
            .unwrap_or_else(|| "mixed".into());
        let Some(travel_style) = TravelStyle::parse(&style) else {
            return bad("travel_style must be one of walk, flash, mixed");
        };
        Ok(Rotation {
            anchors,
            legs,
            position_jitter_px: obj.get("position_jitter_px").and_then(as_i64).unwrap_or(4),
            travel_style,
        })
    }

    pub fn to_json(&self) -> Value {
        json!({
            "position_jitter_px": self.position_jitter_px,
            "travel_style": self.travel_style.as_str(),
            "anchors": self.anchors.iter().map(Anchor::to_json).collect::<Vec<_>>(),
            "legs": self.legs.iter().map(|((f, t), steps)| json!({
                "from": f, "to": t,
                "steps": steps.iter().map(Step::to_json).collect::<Vec<_>>(),
            })).collect::<Vec<_>>(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rot() -> Rotation {
        Rotation::from_json(&json!({
            "anchors": [
                {"name": "a", "pos": [0.1, 0.5]},
                {"name": "b", "pos": [0.5, 0.5], "on_arrive": ["orb"], "face": "left"},
                {"name": "c", "pos": [0.9, 0.3]}
            ],
            "legs": [
                {"from": 0, "to": 1, "steps": [{"walk_to": [0.5, 0.5], "style": "flash"}]},
                {"from": 1, "to": 2, "steps": [
                    {"climb": {"dir": "up", "until_y": 0.3, "x": 0.6}}, {"wait": 0.5}]}
            ]
        }))
        .unwrap()
    }

    #[test]
    fn round_trips_in_python_key_order() {
        let r = rot();
        let back = Rotation::from_json(&r.to_json()).unwrap();
        assert_eq!(back, r);
        let keys: Vec<_> = r.to_json().as_object().unwrap().keys().cloned().collect();
        assert_eq!(
            keys,
            ["position_jitter_px", "travel_style", "anchors", "legs"]
        );
    }

    #[test]
    fn leg_steps_chain_recorded_legs() {
        let r = rot();
        assert_eq!(r.leg_steps(0, 2).len(), 3);
        assert!(matches!(r.leg_steps(2, 0)[0], Step::WalkTo { .. }));
    }

    #[test]
    fn removing_an_anchor_reindexes_legs() {
        let mut r = rot();
        r.remove_anchor(0);
        assert_eq!(r.anchors[0].name, "b");
        assert_eq!(r.legs.keys().copied().collect::<Vec<_>>(), [(0, 1)]);
    }

    #[test]
    fn coords_resolve_like_python() {
        assert_eq!(resolve_coord(0.5, 170), 85);
        assert_eq!(resolve_coord(0.25, 170), 42); // 42.5 rounds to even
        assert_eq!(resolve_coord(37.0, 170), 37);
    }
}
