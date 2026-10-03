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

/// `Movement` is an attack that also moves the character (a rush, a
/// hold-and-dash): it is cast like any attack, and its effect is measured.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkillKind {
    Attack,
    Buff,
    Summon,
    Movement,
}

impl SkillKind {
    /// Cast in attack windows.
    pub fn is_attack(self) -> bool {
        matches!(self, SkillKind::Attack | SkillKind::Movement)
    }

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

/// Where a skill can be cast.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Stance {
    Ground,
    Air,
    #[default]
    Any,
}

impl Stance {
    pub fn as_str(self) -> &'static str {
        match self {
            Stance::Ground => "ground",
            Stance::Air => "air",
            Stance::Any => "any",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "ground" => Stance::Ground,
            "air" => Stance::Air,
            "any" => Stance::Any,
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
    /// Cast on the ground, in the air, or either.
    pub stance: Stance,
    /// Relative odds of being picked among ready skills.
    pub weight: f64,
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
            stance: Stance::Any,
            weight: 1.0,
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
        let stance = match obj.get("stance") {
            None | Some(Value::Null) => Stance::Any,
            Some(v) => Stance::parse(&as_string(v)).ok_or_else(|| {
                crate::Error::Format(format!(
                    "skill {name:?}: stance must be one of ground, air, any"
                ))
            })?,
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
            stance,
            weight: num("weight", 1.0)?.max(0.0),
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
        if self.stance != Stance::Any {
            out.insert("stance".into(), self.stance.as_str().into());
        }
        if self.weight != 1.0 {
            out.insert("weight".into(), self.weight.into());
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

/// Charges left, and when the next one returns (None when full).
#[derive(Debug, Clone, Copy, PartialEq)]
struct Charges {
    have: u32,
    next: Option<f64>,
}

/// Named skills plus their cooldown state. Times are seconds on one clock
/// (the caller's `now`), which keeps every rule testable.
#[derive(Debug, Clone, Default)]
pub struct SkillBook {
    skills: Vec<Skill>,
    charges: std::collections::HashMap<String, Charges>,
    /// When each skill was last cast (a buff lasts its `duration` from then).
    cast_at: std::collections::HashMap<String, f64>,
}

impl SkillBook {
    pub fn new(skills: Vec<Skill>) -> Self {
        SkillBook {
            skills,
            charges: Default::default(),
            cast_at: Default::default(),
        }
    }

    pub fn skills(&self) -> &[Skill] {
        &self.skills
    }

    pub fn get(&self, name: &str) -> Option<&Skill> {
        self.skills.iter().find(|s| s.name == name)
    }

    pub fn contains(&self, name: &str) -> bool {
        self.get(name).is_some()
    }

    pub fn len(&self) -> usize {
        self.skills.len()
    }

    pub fn is_empty(&self) -> bool {
        self.skills.is_empty()
    }

    /// Merge definitions over the book (by name; new ones go last).
    pub fn overlay(&mut self, skills: &[Skill]) {
        for s in skills {
            match self.skills.iter_mut().find(|x| x.name == s.name) {
                Some(slot) => *slot = s.clone(),
                None => self.skills.push(s.clone()),
            }
        }
    }

    /// Keep `other`'s cooldowns and charges for skills of the same name,
    /// so a rebuilt book doesn't make everything ready at once.
    pub fn carry_from(mut self, other: &SkillBook) -> Self {
        for (name, state) in &other.charges {
            if let Some(max) = self.get(name).map(|s| s.charges) {
                let have = state.have.min(max);
                self.charges.insert(
                    name.clone(),
                    Charges {
                        have,
                        next: state.next,
                    },
                );
            }
        }
        for (name, t) in &other.cast_at {
            if self.contains(name) {
                self.cast_at.insert(name.clone(), *t);
            }
        }
        self
    }

    /// Charges after recharging up to `now`: one returns per cooldown
    /// while below the maximum.
    fn sync(&mut self, idx: usize, now: f64) -> Charges {
        let skill = &self.skills[idx];
        let mut c = self.charges.get(&skill.name).copied().unwrap_or(Charges {
            have: skill.charges,
            next: None,
        });
        while c.have < skill.charges {
            match c.next {
                Some(t) if now >= t => {
                    c.have += 1;
                    c.next = (c.have < skill.charges).then_some(t + skill.cooldown);
                }
                _ => break,
            }
        }
        self.charges.insert(skill.name.clone(), c);
        c
    }

    fn index(&self, name: &str) -> Option<usize> {
        self.skills.iter().position(|s| s.name == name)
    }

    pub fn charges(&mut self, name: &str, now: f64) -> u32 {
        let Some(i) = self.index(name) else { return 0 };
        if self.skills[i].cooldown <= 0.0 {
            return self.skills[i].charges;
        }
        self.sync(i, now).have
    }

    /// Seconds until usable (0 with a charge left).
    pub fn remaining(&mut self, name: &str, now: f64) -> f64 {
        let Some(i) = self.index(name) else {
            return 0.0;
        };
        if self.skills[i].cooldown <= 0.0 {
            return 0.0;
        }
        let c = self.sync(i, now);
        match c.next {
            Some(t) if c.have == 0 => (t - now).max(0.0),
            _ => 0.0,
        }
    }

    pub fn ready(&mut self, name: &str, now: f64) -> bool {
        self.contains(name) && self.remaining(name, now) <= 0.0
    }

    pub fn mark_used(&mut self, name: &str, now: f64) {
        let Some(i) = self.index(name) else { return };
        self.cast_at.insert(name.to_owned(), now);
        let cooldown = self.skills[i].cooldown;
        if cooldown <= 0.0 {
            return;
        }
        let c = self.sync(i, now);
        let next = c.next.unwrap_or(now + cooldown); // recharge starts with the first use
        self.charges.insert(
            name.to_owned(),
            Charges {
                have: c.have.saturating_sub(1),
                next: Some(next),
            },
        );
    }

    fn ready_of(&mut self, kinds: &[SkillKind], now: f64) -> Vec<Skill> {
        // Two passes: `remaining` needs `&mut self` (it recharges), so it
        // can't run inside an iterator that also borrows `self.skills`.
        let candidates: Vec<usize> = (0..self.skills.len())
            .filter(|&i| kinds.contains(&self.skills[i].kind))
            .collect();
        let mut ready = Vec::new();
        for i in candidates {
            let name = self.skills[i].name.clone();
            if self.remaining(&name, now) <= 0.0 {
                ready.push(self.skills[i].clone());
            }
        }
        ready
    }

    pub fn ready_attacks(&mut self, now: f64) -> Vec<Skill> {
        self.ready_of(&[SkillKind::Attack, SkillKind::Movement], now)
    }

    /// Buffs to cast: off cooldown and worn off (a buff with a `duration`
    /// lasts that long from its last cast).
    pub fn due_buffs(&mut self, now: f64) -> Vec<Skill> {
        let ready = self.ready_of(&[SkillKind::Buff], now);
        ready
            .into_iter()
            .filter(|s| {
                s.duration <= 0.0
                    || self
                        .cast_at
                        .get(&s.name)
                        .is_none_or(|t| now - t >= s.duration)
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_buff_is_due_once_off_cooldown_and_worn_off() {
        let buff = |name: &str, cooldown: f64, duration: f64| Skill {
            kind: SkillKind::Buff,
            cooldown,
            duration,
            ..Skill::new(name, "b")
        };
        let mut book = SkillBook::new(vec![
            buff("lasting", 0.0, 180.0), // no cooldown: lasts its duration
            buff("long", 30.0, 120.0),   // outlasts its cooldown
            buff("plain", 60.0, 0.0),    // cooldown only
        ]);
        let names = |v: Vec<Skill>| v.into_iter().map(|s| s.name).collect::<Vec<_>>();
        assert_eq!(names(book.due_buffs(0.0)), ["lasting", "long", "plain"]);
        for n in ["lasting", "long", "plain"] {
            book.mark_used(n, 0.0);
        }
        assert!(book.due_buffs(10.0).is_empty()); // not every tick
        assert!(book.due_buffs(59.0).is_empty()); // "long" is off cooldown, still up
        assert_eq!(names(book.due_buffs(60.0)), ["plain"]);
        assert_eq!(names(book.due_buffs(120.0)), ["long", "plain"]);
        assert_eq!(names(book.due_buffs(180.0)), ["lasting", "long", "plain"]);
        // Carried into a new book (a skill edit) with the clock intact.
        let carried = SkillBook::new(vec![buff("lasting", 0.0, 180.0)]).carry_from(&book);
        let mut carried = carried;
        assert!(carried.due_buffs(100.0).is_empty());
    }

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
    fn stance_and_weight_round_trip_and_default_out() {
        let spec = json!({"key": "a", "kind": "attack", "stance": "ground", "weight": 2.5});
        let s = Skill::from_json("hit", &spec).unwrap();
        assert_eq!((s.stance, s.weight), (Stance::Ground, 2.5));
        assert_eq!(s.to_json(), spec);
        let plain =
            Skill::from_json("p", &json!({"key": "a", "stance": "any", "weight": 1})).unwrap();
        assert_eq!(plain.to_json(), json!({"key": "a", "kind": "attack"}));
        assert!(Skill::from_json("x", &json!({"key": "a", "stance": "sky"})).is_err());
    }

    #[test]
    fn rejects_unknown_kind_and_missing_key() {
        assert!(Skill::from_json("x", &json!({"key": "a", "kind": "dance"})).is_err());
        assert!(Skill::from_json("x", &json!({"kind": "attack"})).is_err());
    }
}

#[cfg(test)]
mod book_tests {
    use super::*;

    fn skill(name: &str, cooldown: f64, kind: SkillKind, charges: u32) -> Skill {
        Skill {
            cooldown,
            kind,
            charges,
            ..Skill::new(name, name)
        }
    }

    #[test]
    fn zero_cooldown_is_always_ready() {
        let mut b = SkillBook::new(vec![skill("a", 0.0, SkillKind::Attack, 1)]);
        b.mark_used("a", 0.0);
        assert!(b.ready("a", 0.0));
        assert!(!b.ready("nope", 0.0));
    }

    #[test]
    fn cooldown_counts_down() {
        let mut b = SkillBook::new(vec![skill("s", 60.0, SkillKind::Attack, 1)]);
        b.mark_used("s", 100.0);
        assert!(!b.ready("s", 110.0));
        assert_eq!(b.remaining("s", 110.0), 50.0);
        assert!(b.ready("s", 161.0));
    }

    #[test]
    fn ready_attacks_and_due_buffs() {
        let mut b = SkillBook::new(vec![
            skill("spam", 0.0, SkillKind::Attack, 1),
            skill("burst", 30.0, SkillKind::Attack, 1),
            skill("holy", 120.0, SkillKind::Buff, 1),
            skill("fountain", 57.0, SkillKind::Summon, 1),
        ]);
        let names = |v: Vec<Skill>| v.into_iter().map(|s| s.name).collect::<Vec<_>>();
        assert_eq!(names(b.ready_attacks(0.0)), ["spam", "burst"]);
        assert_eq!(names(b.due_buffs(0.0)), ["holy"]);
        b.mark_used("burst", 0.0);
        assert_eq!(names(b.ready_attacks(1.0)), ["spam"]);
    }

    #[test]
    fn charges_return_one_per_cooldown() {
        let mut b = SkillBook::new(vec![skill("s", 10.0, SkillKind::Summon, 2)]);
        b.mark_used("s", 100.0);
        b.mark_used("s", 101.0);
        assert_eq!(b.charges("s", 101.0), 0);
        assert_eq!(b.charges("s", 110.5), 1); // first back at 110
        assert_eq!(b.charges("s", 119.0), 1);
        assert_eq!(b.charges("s", 120.0), 2); // second at 120
        assert_eq!(b.charges("s", 500.0), 2); // never above max
    }

    #[test]
    fn use_while_recharging_keeps_the_timer() {
        let mut b = SkillBook::new(vec![skill("s", 10.0, SkillKind::Summon, 2)]);
        b.mark_used("s", 100.0); // timer -> 110
        b.mark_used("s", 105.0); // still -> 110
        assert_eq!(b.charges("s", 110.0), 1);
    }

    #[test]
    fn rebuilt_book_keeps_cooldowns_and_caps_charges() {
        let mut old = SkillBook::new(vec![
            skill("burst", 30.0, SkillKind::Attack, 1),
            skill("orb", 60.0, SkillKind::Summon, 3),
        ]);
        old.mark_used("burst", 100.0);
        old.charges("orb", 100.0);
        let mut new = SkillBook::new(vec![
            skill("burst", 30.0, SkillKind::Attack, 1),
            skill("orb", 60.0, SkillKind::Summon, 1),
            skill("fresh", 10.0, SkillKind::Attack, 1),
        ])
        .carry_from(&old);
        assert!(!new.ready("burst", 110.0)); // still cooling
        assert_eq!(new.charges("orb", 100.0), 1); // 3 capped to 1
        assert!(new.ready("fresh", 100.0));
    }

    #[test]
    fn overlay_replaces_by_name_and_appends() {
        let mut b = SkillBook::new(vec![skill("a", 0.0, SkillKind::Attack, 1)]);
        b.overlay(&[
            skill("a", 5.0, SkillKind::Attack, 1),
            skill("b", 5.0, SkillKind::Buff, 1),
        ]);
        assert_eq!(b.len(), 2);
        assert_eq!(b.get("a").unwrap().cooldown, 5.0);
    }
}
