//! Per-map storage: one JSON file per farmed map under `maps/`.
//!
//! ```json
//! {
//!   "name": "limina_1f_east",          // user alias
//!   "map_name": "Limina : 1-5 East",   // OCR'd in-game title
//!   "fingerprint": null,               // legacy, carried through unused
//!   "minimap_region": [x, y, w, h],    // remembered panel layout
//!   "walls": null,                     // legacy {left,right,floor}
//!   "platforms": [[x0,y0,x1,y1], ...], // drawn platform lines (0–1)
//!   "ropes": [[x0,y0,x1,y1], ...],     // learned ropes (0–1)
//!   "rotation": {...},
//!   "skills": {...}
//! }
//! ```

use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::error::{bad, Result};
use crate::fileio::write_text_atomic;
use crate::json::{as_f64, as_i64, as_string, dumps, truthy};
use crate::rotation::Rotation;
use crate::skills::{skills_from_json, skills_to_json, Skill};

/// A line segment in normalised minimap coordinates: `[x0, y0, x1, y1]`.
pub type Segment = [f64; 4];

/// Legacy per-map boundaries (normalised); kept so files round-trip.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Walls {
    pub left: Option<f64>,
    pub right: Option<f64>,
    pub floor: Option<f64>,
}

/// How one map treats other-player markers, instead of the global setting.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlayerRule {
    /// Markers here aren't players (monsters drawn as players): never pause.
    Ignore,
    /// Pause only above this many.
    Allow(i64),
}

impl PlayerRule {
    pub fn from_json(v: &Value) -> Option<Self> {
        match v.get("mode")?.as_str()? {
            "ignore" => Some(PlayerRule::Ignore),
            "allow" => Some(PlayerRule::Allow(v.get("allowed")?.as_i64()?.max(0))),
            _ => None,
        }
    }

    pub fn to_json(self) -> Value {
        match self {
            PlayerRule::Ignore => json!({"mode": "ignore"}),
            PlayerRule::Allow(n) => json!({"mode": "allow", "allowed": n}),
        }
    }

    /// `follow`, `ignore` or `allow:N` (what the dashboard sends).
    pub fn parse(s: &str) -> Result<Option<Self>> {
        match s.trim() {
            "follow" => Ok(None),
            "ignore" => Ok(Some(PlayerRule::Ignore)),
            other => match other
                .strip_prefix("allow:")
                .and_then(|n| n.trim().parse::<i64>().ok())
            {
                Some(n) if n >= 0 => Ok(Some(PlayerRule::Allow(n))),
                _ => bad("rule must be follow, ignore or allow:N"),
            },
        }
    }

    /// The form `parse` reads; `follow` for no rule.
    pub fn label(rule: Option<Self>) -> String {
        match rule {
            None => "follow".into(),
            Some(PlayerRule::Ignore) => "ignore".into(),
            Some(PlayerRule::Allow(n)) => format!("allow:{n}"),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct MapEntry {
    pub name: String,
    pub map_name: Option<String>,
    /// Legacy field, carried through as-is.
    pub fingerprint: Value,
    pub minimap_region: Option<[i64; 4]>,
    pub walls: Option<Walls>,
    pub platforms: Option<Vec<Segment>>,
    pub ropes: Option<Vec<Segment>>,
    pub rotation: Rotation,
    pub skills: Vec<Skill>,
    /// Other-player handling for this map; None follows the global setting.
    pub other_players: Option<PlayerRule>,
    /// Where this entry was loaded from / last saved to.
    pub path: Option<PathBuf>,
}

impl MapEntry {
    pub fn new(name: &str) -> Self {
        MapEntry {
            name: name.into(),
            map_name: None,
            fingerprint: Value::Null,
            minimap_region: None,
            walls: None,
            platforms: None,
            ropes: None,
            rotation: Rotation::default(),
            skills: Vec::new(),
            other_players: None,
            path: None,
        }
    }

    pub fn from_json(data: &Value, path: Option<PathBuf>) -> Result<Self> {
        let Some(obj) = data.as_object() else {
            return bad("map file must be an object");
        };
        let Some(name) = obj.get("name").filter(|v| truthy(v)) else {
            return bad("map file requires a 'name'");
        };
        Ok(MapEntry {
            name: as_string(name),
            map_name: match obj.get("map_name") {
                None | Some(Value::Null) => None,
                Some(v) => Some(as_string(v)),
            },
            fingerprint: obj.get("fingerprint").cloned().unwrap_or(Value::Null),
            minimap_region: region(obj.get("minimap_region")),
            walls: walls(obj.get("walls")),
            platforms: segments(obj.get("platforms")),
            ropes: segments(obj.get("ropes")),
            rotation: Rotation::from_json(obj.get("rotation").unwrap_or(&Value::Null))?,
            skills: skills_from_json(obj.get("skills").unwrap_or(&Value::Null))?,
            other_players: obj.get("other_players").and_then(PlayerRule::from_json),
            path,
        })
    }

    pub fn to_json(&self) -> Value {
        let segs = |s: &Option<Vec<Segment>>| match s {
            Some(v) if !v.is_empty() => json!(v),
            _ => Value::Null,
        };
        let walls = match self.walls {
            Some(w) => {
                let mut m = Map::new();
                for (k, v) in [("left", w.left), ("right", w.right), ("floor", w.floor)] {
                    if let Some(v) = v {
                        m.insert(k.into(), v.into());
                    }
                }
                if m.is_empty() {
                    Value::Null
                } else {
                    Value::Object(m)
                }
            }
            None => Value::Null,
        };
        let mut out = json!({
            "name": self.name,
            "map_name": self.map_name,
            "fingerprint": self.fingerprint,
            "minimap_region": self.minimap_region,
            "walls": walls,
            "platforms": segs(&self.platforms),
            "ropes": segs(&self.ropes),
            "rotation": self.rotation.to_json(),
            "skills": skills_to_json(&self.skills),
        });
        if let Some(rule) = self.other_players {
            out["other_players"] = rule.to_json();
        }
        out
    }
}

/// `[x, y, w, h]` of ints, else None (Python: `tuple(int(v) ...)`, len 4).
fn region(v: Option<&Value>) -> Option<[i64; 4]> {
    let items = v.filter(|v| truthy(v))?.as_array()?;
    if items.len() != 4 {
        return None;
    }
    let mut out = [0i64; 4];
    for (slot, item) in out.iter_mut().zip(items) {
        *slot = as_i64(item)?;
    }
    Some(out)
}

fn walls(v: Option<&Value>) -> Option<Walls> {
    let obj = v?.as_object()?;
    let side = |k: &str| {
        obj.get(k)
            .filter(|v| v.is_number())
            .and_then(as_f64)
            .filter(|f| (0.0..=1.5).contains(f))
    };
    let w = Walls {
        left: side("left"),
        right: side("right"),
        floor: side("floor"),
    };
    (w != Walls::default()).then_some(w)
}

/// Valid `[x0,y0,x1,y1]` rows (all within 0–1.5); None when there are none.
fn segments(v: Option<&Value>) -> Option<Vec<Segment>> {
    let rows = v?.as_array()?;
    let out: Vec<Segment> = rows
        .iter()
        .filter_map(|row| {
            let row = row.as_array().filter(|r| r.len() == 4)?;
            let mut seg = [0.0; 4];
            for (slot, item) in seg.iter_mut().zip(row) {
                *slot = as_f64(item)?;
            }
            seg.iter().all(|v| (0.0..=1.5).contains(v)).then_some(seg)
        })
        .collect();
    (!out.is_empty()).then_some(out)
}

/// The directory of map files, loaded lazily and cached.
pub struct MapStore {
    pub directory: PathBuf,
    entries: Option<Vec<MapEntry>>,
    /// Files that failed to load on the last scan, with the reason.
    pub skipped: Vec<(PathBuf, String)>,
}

impl MapStore {
    pub fn new(directory: impl Into<PathBuf>) -> Self {
        MapStore {
            directory: directory.into(),
            entries: None,
            skipped: Vec::new(),
        }
    }

    pub fn load_all(&mut self) -> &[MapEntry] {
        if self.entries.is_none() {
            let (entries, skipped) = scan(&self.directory);
            self.entries = Some(entries);
            self.skipped = skipped;
        }
        self.entries.as_deref().unwrap_or_default()
    }

    pub fn reload(&mut self) -> &[MapEntry] {
        self.entries = None;
        self.load_all()
    }

    pub fn names(&mut self) -> Vec<String> {
        self.load_all().iter().map(|e| e.name.clone()).collect()
    }

    pub fn get(&mut self, name: &str) -> Option<&MapEntry> {
        self.load_all().iter().find(|e| e.name == name)
    }

    /// Write the entry's file and update the cache; returns its path.
    /// Names that sanitise to the same file name ("Foo 2" / "Foo_2") get
    /// distinct files.
    pub fn save(&mut self, mut entry: MapEntry) -> Result<PathBuf> {
        std::fs::create_dir_all(&self.directory)?;
        let safe: String = entry
            .name
            .chars()
            .map(|c| {
                if c.is_alphanumeric() || c == '-' || c == '_' {
                    c
                } else {
                    '_'
                }
            })
            .collect();
        let taken: Vec<PathBuf> = self
            .load_all()
            .iter()
            .filter(|e| e.name != entry.name)
            .filter_map(|e| e.path.as_deref().map(canonical))
            .collect();
        let mut path = self.directory.join(format!("{safe}.json"));
        let mut n = 2;
        while taken.contains(&canonical(&path)) {
            path = self.directory.join(format!("{safe}_{n}.json"));
            n += 1;
        }
        write_text_atomic(&path, &dumps(&entry.to_json(), 2))?;
        entry.path = Some(path.clone());
        let entries = self.entries.get_or_insert_with(Vec::new);
        match entries.iter_mut().find(|e| e.name == entry.name) {
            Some(slot) => *slot = entry,
            None => entries.push(entry),
        }
        Ok(path)
    }
}

/// An absolute, comparable form of `p` (the file may not exist yet).
fn canonical(p: &Path) -> PathBuf {
    std::path::absolute(p).unwrap_or_else(|_| p.to_path_buf())
}

fn scan(dir: &Path) -> (Vec<MapEntry>, Vec<(PathBuf, String)>) {
    let mut paths: Vec<PathBuf> = match std::fs::read_dir(dir) {
        Ok(rd) => rd
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.extension().is_some_and(|x| x == "json"))
            .collect(),
        Err(_) => return (Vec::new(), Vec::new()),
    };
    paths.sort();
    let mut entries = Vec::new();
    let mut skipped = Vec::new();
    for path in paths {
        let loaded = std::fs::read_to_string(&path)
            .map_err(crate::Error::from)
            .and_then(|text| Ok(serde_json::from_str::<Value>(&text)?))
            .and_then(|v| MapEntry::from_json(&v, Some(path.clone())));
        match loaded {
            Ok(e) => entries.push(e),
            Err(e) => skipped.push((path, e.to_string())),
        }
    }
    (entries, skipped)
}

#[cfg(test)]
mod tests {
    #[test]
    fn a_player_rule_round_trips_and_absent_stays_absent() {
        let mut e = MapEntry::new("Crowded");
        assert!(e.to_json().get("other_players").is_none());
        for rule in [PlayerRule::Ignore, PlayerRule::Allow(3)] {
            e.other_players = Some(rule);
            let back = MapEntry::from_json(&e.to_json(), None).unwrap();
            assert_eq!(back.other_players, Some(rule));
        }
    }

    #[test]
    fn player_rules_parse_the_dashboard_form() {
        assert_eq!(PlayerRule::parse("follow").unwrap(), None);
        assert_eq!(
            PlayerRule::parse("ignore").unwrap(),
            Some(PlayerRule::Ignore)
        );
        assert_eq!(
            PlayerRule::parse("allow:2").unwrap(),
            Some(PlayerRule::Allow(2))
        );
        assert!(PlayerRule::parse("allow:-1").is_err());
        assert!(PlayerRule::parse("sometimes").is_err());
        assert_eq!(PlayerRule::label(Some(PlayerRule::Allow(2))), "allow:2");
    }

    use super::*;

    #[test]
    fn invalid_rows_and_regions_are_dropped() {
        let e = MapEntry::from_json(
            &json!({
                "name": "m",
                "minimap_region": [1, 2, 3],
                "platforms": [[0.1, 0.2, 0.3, 0.2], [0.1, 0.2, 9.0, 0.2], "junk"],
                "walls": {"left": 0.1, "right": "x"}
            }),
            None,
        )
        .unwrap();
        assert_eq!(e.minimap_region, None);
        assert_eq!(e.platforms, Some(vec![[0.1, 0.2, 0.3, 0.2]]));
        assert_eq!(
            e.walls,
            Some(Walls {
                left: Some(0.1),
                right: None,
                floor: None
            })
        );
        assert!(MapEntry::from_json(&json!({"name": ""}), None).is_err());
    }

    #[test]
    fn store_saves_colliding_names_to_distinct_files() {
        let dir = std::env::temp_dir().join(format!("picobot-maps-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let mut store = MapStore::new(&dir);
        let a = store.save(MapEntry::new("Foo 2")).unwrap();
        let b = store.save(MapEntry::new("Foo_2")).unwrap();
        assert_ne!(a, b);
        let mut again = MapStore::new(&dir);
        let mut names = again.names();
        names.sort();
        assert_eq!(names, ["Foo 2", "Foo_2"]);
        let entry = again.get("Foo 2").unwrap().clone();
        assert_eq!(again.save(entry).unwrap(), a); // stable
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
