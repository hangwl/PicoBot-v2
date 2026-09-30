//! `config.json`: host settings at the top level, bot settings under
//! `"bot"`. The `bot` object is kept verbatim (so saving never drops keys
//! this version doesn't know) and parsed into a typed [`BotConfig`].

use std::path::Path;

use serde_json::{json, Map, Value};

use crate::error::{bad, Error, Result};
use crate::fileio::write_text_atomic;
use crate::json::{as_f64, as_i64, as_string, dumps, opt_string, truthy};
use crate::rotation::Rotation;
use crate::skills::{skills_from_json, Skill, SkillKind};

/// Top-level host settings.
#[derive(Debug, Clone, PartialEq)]
pub struct AppConfig {
    pub default_target_window: String,
    pub bot_token: String,
    pub chat_id: String,
    pub serial_port: String,
    pub ws_port: i64,
    pub http_port: i64,
    /// Dashboard stream rate, 1–30.
    pub view_fps: f64,
    pub ws_tls: bool,
    pub ws_certfile: String,
    pub ws_keyfile: String,
    /// The raw `"bot"` object; see [`BotConfig::from_json`].
    pub bot: Map<String, Value>,
}

impl Default for AppConfig {
    fn default() -> Self {
        AppConfig {
            default_target_window: "Eluna (x64)".into(),
            bot_token: String::new(),
            chat_id: String::new(),
            serial_port: String::new(),
            ws_port: 8765,
            http_port: 8000,
            view_fps: 10.0,
            ws_tls: false,
            ws_certfile: String::new(),
            ws_keyfile: String::new(),
            bot: Map::new(),
        }
    }
}

impl AppConfig {
    /// Parse leniently: bad or missing fields fall back to defaults, as
    /// the Python host does.
    pub fn from_json(raw: &Value) -> Self {
        let d = AppConfig::default();
        let Some(o) = raw.as_object() else { return d };
        let text = |k: &str, def: &str| o.get(k).map(as_string).unwrap_or_else(|| def.to_owned());
        AppConfig {
            default_target_window: text("default_target_window", &d.default_target_window),
            bot_token: text("bot_token", &d.bot_token),
            chat_id: text("chat_id", &d.chat_id),
            serial_port: text("serial_port", &d.serial_port),
            ws_port: o.get("ws_port").and_then(as_i64).unwrap_or(d.ws_port),
            http_port: o.get("http_port").and_then(as_i64).unwrap_or(d.http_port),
            view_fps: o
                .get("view_fps")
                .and_then(as_f64)
                .unwrap_or(d.view_fps)
                .clamp(1.0, 30.0),
            ws_tls: o.get("ws_tls").map(truthy).unwrap_or(d.ws_tls),
            ws_certfile: text("ws_certfile", &d.ws_certfile),
            ws_keyfile: text("ws_keyfile", &d.ws_keyfile),
            bot: o
                .get("bot")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default(),
        }
    }

    pub fn to_json(&self) -> Value {
        let mut o = Map::new();
        o.insert(
            "default_target_window".into(),
            self.default_target_window.clone().into(),
        );
        o.insert("bot_token".into(), self.bot_token.clone().into());
        o.insert("chat_id".into(), self.chat_id.clone().into());
        o.insert("serial_port".into(), self.serial_port.clone().into());
        o.insert("ws_port".into(), self.ws_port.into());
        o.insert("http_port".into(), self.http_port.into());
        o.insert("view_fps".into(), self.view_fps.into());
        o.insert("ws_tls".into(), self.ws_tls.into());
        o.insert("ws_certfile".into(), self.ws_certfile.clone().into());
        o.insert("ws_keyfile".into(), self.ws_keyfile.clone().into());
        o.insert("bot".into(), Value::Object(self.bot.clone()));
        Value::Object(o)
    }

    /// Load `path`; a missing or unreadable file gives the defaults (the
    /// error is returned alongside so the caller can report it).
    pub fn load(path: &Path) -> (Self, Option<Error>) {
        if !path.exists() {
            return (AppConfig::default(), None);
        }
        let parsed = std::fs::read_to_string(path)
            .map_err(Error::from)
            .and_then(|t| Ok(serde_json::from_str::<Value>(&t)?));
        match parsed {
            Ok(v) if v.is_object() => (AppConfig::from_json(&v), None),
            Ok(_) => (
                AppConfig::default(),
                Some(Error::Format("config is not an object".into())),
            ),
            Err(e) => (AppConfig::default(), Some(e)),
        }
    }

    /// Write with 4-space indents, like the Python host.
    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
            std::fs::create_dir_all(dir)?;
        }
        write_text_atomic(path, &dumps(&self.to_json(), 4))?;
        Ok(())
    }
}

/// BGR marker colours on the in-game minimap.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MinimapColors {
    pub player: [u8; 3],
    pub other_player: [u8; 3],
    pub rune: [u8; 3],
    pub border: [u8; 3],
}

impl Default for MinimapColors {
    fn default() -> Self {
        MinimapColors {
            player: [12, 240, 239],
            other_player: [118, 45, 253],
            rune: [255, 102, 221],
            border: [228, 228, 228],
        }
    }
}

impl MinimapColors {
    pub fn to_json(&self) -> Value {
        json!({
            "player": self.player,
            "other_player": self.other_player,
            "rune": self.rune,
            "border": self.border,
        })
    }

    pub fn from_json(data: &Value) -> Result<Self> {
        let mut c = MinimapColors::default();
        let Some(o) = data.as_object() else {
            return Ok(c);
        };
        for (name, slot) in [
            ("player", &mut c.player),
            ("other_player", &mut c.other_player),
            ("rune", &mut c.rune),
            ("border", &mut c.border),
        ] {
            match o.get(name) {
                None | Some(Value::Null) => {}
                Some(Value::Array(v)) if v.len() == 3 => {
                    for (dst, src) in slot.iter_mut().zip(v) {
                        *dst = as_i64(src).unwrap_or(0).clamp(0, 255) as u8;
                    }
                }
                Some(_) => return bad(format!("minimap color '{name}' must be [B, G, R]")),
            }
        }
        Ok(c)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClassTravel {
    Flash,
    Teleport,
    Walk,
}

impl ClassTravel {
    pub fn as_str(self) -> &'static str {
        match self {
            ClassTravel::Flash => "flash",
            ClassTravel::Teleport => "teleport",
            ClassTravel::Walk => "walk",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PatrolPolicy {
    Weighted,
    Greedy,
}

impl PatrolPolicy {
    pub fn as_str(self) -> &'static str {
        match self {
            PatrolPolicy::Weighted => "weighted",
            PatrolPolicy::Greedy => "greedy",
        }
    }
}

/// Tuning knobs for the bot, from `config.json["bot"]`.
#[derive(Debug, Clone, PartialEq)]
pub struct BotConfig {
    // Behaviour toggles
    pub stop_when_players_appear: bool,
    pub stop_when_rune_appears: bool,
    pub pause_on_lie_detector: bool,
    // Keys
    pub attack_keys: Vec<String>,
    pub buff_keys: Vec<String>,
    pub buff_interval_seconds: f64,
    pub jump_key: String,
    pub up_jump_skill_key: Option<String>,
    pub up_jump_skill_cooldown: f64,
    // Navigation
    pub walk_band_px: f64,
    pub nav_threshold_px: i64,
    pub nav_stuck_limit: i64,
    pub vert_jump_interval: f64,
    pub nav_up_flash_px: f64,
    pub nav_rope_lift_px: f64,
    pub nav_up_side_dx_px: f64,
    pub nav_jump_px: f64,
    pub nav_gap_px: f64,
    pub nav_double_gap_px: f64,
    pub rope_penalty: f64,
    pub patrol_policy: PatrolPolicy,
    pub patrol_weight_temp: f64,
    pub nav_reach_file: String,
    pub anchor_float_px: f64,
    pub flash_repress_seconds: f64,
    pub combo_repress_seconds: f64,
    // Weave
    pub weave_double_chance: f64,
    pub weave_range_px: i64,
    pub weave_edge_margin_px: i64,
    // Vision
    pub minimap_colors: MinimapColors,
    pub minimap_region: Option<[i64; 4]>,
    // Skills and rotation
    pub skills: Vec<Skill>,
    pub rotation: Rotation,
    // Movement
    pub flash_jump_enabled: bool,
    pub flash_jump_key: Option<String>,
    pub travel_style: String,
    // Class profile
    pub class_profiles: Map<String, Value>,
    pub class_active: String,
    pub class_travel: ClassTravel,
    pub air_attacks: bool,
    pub teleport_key: Option<String>,
    pub teleport_cooldown: f64,
    pub nav_teleport_dx: f64,
    pub nav_teleport_rise: f64,
    // Maps
    pub maps_dir: String,
    pub auto_select_map: bool,
    pub active_map: Option<String>,
    pub marker_inset_px: i64,
    pub name_ocr: bool,
    pub minimap_name_region: Option<[i64; 4]>,
    pub name_scan_px: i64,
    // Debug
    pub debug_capture_dir: String,
    pub debug_capture_max_events: i64,
}

impl Default for BotConfig {
    fn default() -> Self {
        let mut cfg = BotConfig {
            stop_when_players_appear: true,
            stop_when_rune_appears: true,
            pause_on_lie_detector: false,
            attack_keys: vec!["a".into()],
            buff_keys: Vec::new(),
            buff_interval_seconds: 60.0,
            jump_key: "alt".into(),
            up_jump_skill_key: None,
            up_jump_skill_cooldown: 3.0,
            walk_band_px: 8.0,
            nav_threshold_px: 5,
            nav_stuck_limit: 40,
            vert_jump_interval: 0.9,
            nav_up_flash_px: 26.0,
            nav_rope_lift_px: 90.0,
            nav_up_side_dx_px: 30.0,
            nav_jump_px: 10.0,
            nav_gap_px: 30.0,
            nav_double_gap_px: 48.0,
            rope_penalty: 5.0,
            patrol_policy: PatrolPolicy::Weighted,
            patrol_weight_temp: 1.0,
            nav_reach_file: "nav_reach.json".into(),
            anchor_float_px: 4.0,
            flash_repress_seconds: 0.15,
            combo_repress_seconds: 0.16,
            weave_double_chance: 0.4,
            weave_range_px: 24,
            weave_edge_margin_px: 4,
            minimap_colors: MinimapColors::default(),
            minimap_region: None,
            skills: Vec::new(),
            rotation: Rotation::default(),
            flash_jump_enabled: true,
            flash_jump_key: None,
            travel_style: "mixed".into(),
            class_profiles: Map::new(),
            class_active: "default".into(),
            class_travel: ClassTravel::Flash,
            air_attacks: true,
            teleport_key: None,
            teleport_cooldown: 1.0,
            nav_teleport_dx: 25.0,
            nav_teleport_rise: 12.0,
            maps_dir: "maps".into(),
            auto_select_map: true,
            active_map: None,
            marker_inset_px: 4,
            name_ocr: true,
            minimap_name_region: None,
            name_scan_px: 160,
            debug_capture_dir: "debug/frames".into(),
            debug_capture_max_events: 100,
        };
        cfg.skills = legacy_skills(&cfg);
        cfg
    }
}

/// Skills synthesised from the legacy `attack_keys` / `buff_keys` fields.
fn legacy_skills(cfg: &BotConfig) -> Vec<Skill> {
    let mut out = Vec::new();
    let attacks = if cfg.attack_keys.is_empty() {
        vec!["a".to_owned()]
    } else {
        cfg.attack_keys.clone()
    };
    for key in attacks {
        out.push(Skill::new(&format!("attack_{key}"), &key));
    }
    for key in &cfg.buff_keys {
        let mut s = Skill::new(&format!("buff_{key}"), key);
        s.cooldown = cfg.buff_interval_seconds;
        s.kind = SkillKind::Buff;
        out.push(s);
    }
    out
}

fn num_err(name: &str) -> Error {
    Error::Format(format!("bot.{name} must be a number"))
}

fn rect(v: &Value, name: &str) -> Result<Option<[i64; 4]>> {
    if !truthy(v) {
        return Ok(None);
    }
    let items = v.as_array().filter(|a| a.len() == 4);
    let Some(items) = items else {
        return bad(format!("{name} must be [x, y, w, h]"));
    };
    let mut out = [0i64; 4];
    for (slot, item) in out.iter_mut().zip(items) {
        *slot = as_i64(item).ok_or_else(|| num_err(name))?;
    }
    Ok(Some(out))
}

impl BotConfig {
    /// Parse `config.json["bot"]`, then apply the active class profile.
    pub fn from_json(data: &Map<String, Value>) -> Result<Self> {
        let mut cfg = BotConfig::default();
        if data.is_empty() {
            return Ok(cfg);
        }
        let get = |k: &str| data.get(k);
        let f64_of = |k: &str| -> Result<Option<f64>> {
            get(k)
                .map(|v| as_f64(v).ok_or_else(|| num_err(k)))
                .transpose()
        };
        let i64_of = |k: &str| -> Result<Option<i64>> {
            get(k)
                .map(|v| as_i64(v).ok_or_else(|| num_err(k)))
                .transpose()
        };
        for (name, slot) in [
            (
                "stop_when_players_appear",
                &mut cfg.stop_when_players_appear,
            ),
            ("stop_when_rune_appears", &mut cfg.stop_when_rune_appears),
            ("pause_on_lie_detector", &mut cfg.pause_on_lie_detector),
            ("name_ocr", &mut cfg.name_ocr),
        ] {
            if let Some(v) = get(name) {
                *slot = truthy(v);
            }
        }
        for (name, slot) in [
            ("buff_interval_seconds", &mut cfg.buff_interval_seconds),
            ("up_jump_skill_cooldown", &mut cfg.up_jump_skill_cooldown),
            ("nav_up_flash_px", &mut cfg.nav_up_flash_px),
            ("nav_rope_lift_px", &mut cfg.nav_rope_lift_px),
            ("nav_up_side_dx_px", &mut cfg.nav_up_side_dx_px),
            ("nav_jump_px", &mut cfg.nav_jump_px),
            ("nav_gap_px", &mut cfg.nav_gap_px),
            ("nav_double_gap_px", &mut cfg.nav_double_gap_px),
            ("vert_jump_interval", &mut cfg.vert_jump_interval),
            ("weave_double_chance", &mut cfg.weave_double_chance),
            ("anchor_float_px", &mut cfg.anchor_float_px),
            ("walk_band_px", &mut cfg.walk_band_px),
            ("flash_repress_seconds", &mut cfg.flash_repress_seconds),
            ("combo_repress_seconds", &mut cfg.combo_repress_seconds),
            ("rope_penalty", &mut cfg.rope_penalty),
            ("patrol_weight_temp", &mut cfg.patrol_weight_temp),
        ] {
            if let Some(v) = f64_of(name)? {
                *slot = v;
            }
        }
        for (name, slot) in [
            ("nav_threshold_px", &mut cfg.nav_threshold_px),
            ("nav_stuck_limit", &mut cfg.nav_stuck_limit),
            ("marker_inset_px", &mut cfg.marker_inset_px),
            ("weave_range_px", &mut cfg.weave_range_px),
            ("weave_edge_margin_px", &mut cfg.weave_edge_margin_px),
            ("name_scan_px", &mut cfg.name_scan_px),
            (
                "debug_capture_max_events",
                &mut cfg.debug_capture_max_events,
            ),
        ] {
            if let Some(v) = i64_of(name)? {
                *slot = v;
            }
        }
        if let Some(v) = get("patrol_policy") {
            cfg.patrol_policy = if as_string(v) == "greedy" {
                PatrolPolicy::Greedy
            } else {
                PatrolPolicy::Weighted
            };
        }
        let string_list = |v: &Value| match v {
            Value::Array(items) => items.iter().map(as_string).collect(),
            _ => Vec::new(),
        };
        if let Some(v) = get("attack_keys") {
            cfg.attack_keys = string_list(v);
        }
        if let Some(v) = get("buff_keys") {
            cfg.buff_keys = string_list(v);
        }
        if let Some(v) = get("jump_key") {
            cfg.jump_key = as_string(v);
        }
        if data.contains_key("up_jump_skill_key") {
            cfg.up_jump_skill_key = opt_string(get("up_jump_skill_key"));
        }
        if let Some(v) = get("nav_reach_file").filter(|v| truthy(v)) {
            cfg.nav_reach_file = as_string(v);
        }
        if let Some(v) = get("minimap_colors") {
            cfg.minimap_colors = MinimapColors::from_json(v)?;
        }
        if let Some(v) = get("minimap_region") {
            cfg.minimap_region = rect(v, "minimap_region")?;
        }
        if let Some(v) = get("minimap_name_region") {
            cfg.minimap_name_region = rect(v, "minimap_name_region")?;
        }
        cfg.skills = match get("skills") {
            Some(v @ Value::Object(m)) if !m.is_empty() => skills_from_json(v)?,
            // No explicit skills: synthesise from the (possibly overridden)
            // legacy key lists — before the class profile, which may
            // replace the book.
            _ => legacy_skills(&cfg),
        };
        if let Some(v @ Value::Object(_)) = get("rotation") {
            cfg.rotation = Rotation::from_json(v)?;
        }
        if let Some(Value::Object(fj)) = get("flash_jump") {
            cfg.flash_jump_enabled = fj.get("enabled").map(truthy).unwrap_or(true);
            cfg.flash_jump_key = opt_string(fj.get("key"));
        }
        if let Some(v) = get("travel_style") {
            cfg.travel_style = as_string(v);
        }
        if let Some(v) = get("air_attacks") {
            cfg.air_attacks = truthy(v);
        }
        if data.contains_key("teleport_key") {
            cfg.teleport_key = opt_string(get("teleport_key"));
        }
        if let Some(v) = f64_of("teleport_cooldown")? {
            cfg.teleport_cooldown = v;
        }
        cfg.apply_class(get("class"))?;
        if let Some(v) = get("maps_dir") {
            cfg.maps_dir = as_string(v);
        }
        if let Some(v) = get("debug_capture_dir").filter(|v| truthy(v)) {
            cfg.debug_capture_dir = as_string(v);
        }
        if data.contains_key("active_map") {
            cfg.active_map = opt_string(get("active_map"));
        }
        if let Some(v) = get("auto_select_map") {
            cfg.auto_select_map = truthy(v);
        }
        Ok(cfg)
    }

    /// Overlay the active class profile onto the flat keys.
    pub fn apply_class(&mut self, block: Option<&Value>) -> Result<()> {
        let Some(Value::Object(block)) = block else {
            return Ok(());
        };
        if let Some(Value::Object(profiles)) = block.get("profiles") {
            self.class_profiles = profiles
                .iter()
                .filter(|(_, v)| v.is_object())
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
        }
        let active = block
            .get("active")
            .filter(|v| truthy(v))
            .or_else(|| block.get("name").filter(|v| truthy(v)))
            .map(as_string)
            .unwrap_or_else(|| "default".into());
        self.class_active = active.clone();
        if let Some(Value::Object(p)) = self.class_profiles.get(&active).cloned() {
            if let Some(v) = p.get("travel") {
                self.class_travel = parse_travel(&as_string(v)).unwrap_or(ClassTravel::Flash);
            }
            if let Some(v) = p.get("air_attacks") {
                self.air_attacks = truthy(v);
            }
            if p.contains_key("teleport_key") {
                self.teleport_key = opt_string(p.get("teleport_key"));
            }
            if let Some(v) = p.get("teleport_cooldown") {
                self.teleport_cooldown = as_f64(v).ok_or_else(|| num_err("teleport_cooldown"))?;
            }
            if let Some(v) = p.get("jump_key") {
                self.jump_key = if truthy(v) {
                    as_string(v)
                } else {
                    "space".into()
                };
            }
            if p.contains_key("up_jump_skill_key") {
                self.up_jump_skill_key = opt_string(p.get("up_jump_skill_key"));
            }
            if let Some(Value::Object(fj)) = p.get("flash_jump") {
                if fj.contains_key("key") {
                    self.flash_jump_key = opt_string(fj.get("key"));
                }
                if let Some(v) = fj.get("enabled") {
                    self.flash_jump_enabled = truthy(v);
                }
            }
            // A skills entry — even an empty one — owns the kit; no entry
            // inherits the global book.
            if let Some(Value::Object(skills)) = p.get("skills") {
                self.skills = skills
                    .iter()
                    .filter(|(_, spec)| spec.is_object())
                    .map(|(n, spec)| Skill::from_json(n, spec))
                    .collect::<Result<_>>()?;
            }
        }
        if self.class_travel == ClassTravel::Teleport && self.teleport_key.is_none() {
            self.class_travel = ClassTravel::Flash; // no key bound — can't blink
        }
        Ok(())
    }

    /// `nav_reach.json`, or `nav_reach_<class>.json` when a class profile
    /// is active — each class learns its own move reach.
    /// Every field with its effective value (the dashboard's `config`
    /// event; Python's `asdict`).
    pub fn snapshot(&self) -> Value {
        let rect = |r: Option<[i64; 4]>| r.map_or(Value::Null, |r| json!(r));
        json!({
            "stop_when_players_appear": self.stop_when_players_appear,
            "stop_when_rune_appears": self.stop_when_rune_appears,
            "pause_on_lie_detector": self.pause_on_lie_detector,
            "attack_keys": self.attack_keys,
            "buff_keys": self.buff_keys,
            "buff_interval_seconds": self.buff_interval_seconds,
            "jump_key": self.jump_key,
            "up_jump_skill_key": self.up_jump_skill_key,
            "up_jump_skill_cooldown": self.up_jump_skill_cooldown,
            "walk_band_px": self.walk_band_px,
            "nav_threshold_px": self.nav_threshold_px,
            "nav_stuck_limit": self.nav_stuck_limit,
            "vert_jump_interval": self.vert_jump_interval,
            "nav_up_flash_px": self.nav_up_flash_px,
            "nav_rope_lift_px": self.nav_rope_lift_px,
            "nav_up_side_dx_px": self.nav_up_side_dx_px,
            "nav_jump_px": self.nav_jump_px,
            "nav_gap_px": self.nav_gap_px,
            "nav_double_gap_px": self.nav_double_gap_px,
            "rope_penalty": self.rope_penalty,
            "patrol_policy": self.patrol_policy.as_str(),
            "patrol_weight_temp": self.patrol_weight_temp,
            "nav_reach_file": self.nav_reach_file,
            "anchor_float_px": self.anchor_float_px,
            "flash_repress_seconds": self.flash_repress_seconds,
            "combo_repress_seconds": self.combo_repress_seconds,
            "weave_double_chance": self.weave_double_chance,
            "weave_range_px": self.weave_range_px,
            "weave_edge_margin_px": self.weave_edge_margin_px,
            "minimap_colors": self.minimap_colors.to_json(),
            "minimap_region": rect(self.minimap_region),
            "skills": crate::skills::skills_to_json(&self.skills),
            "rotation": self.rotation.to_json(),
            "flash_jump_enabled": self.flash_jump_enabled,
            "flash_jump_key": self.flash_jump_key,
            "travel_style": self.travel_style,
            "class_profiles": self.class_profiles,
            "class_active": self.class_active,
            "class_travel": self.class_travel.as_str(),
            "air_attacks": self.air_attacks,
            "teleport_key": self.teleport_key,
            "teleport_cooldown": self.teleport_cooldown,
            "nav_teleport_dx": self.nav_teleport_dx,
            "nav_teleport_rise": self.nav_teleport_rise,
            "maps_dir": self.maps_dir,
            "auto_select_map": self.auto_select_map,
            "active_map": self.active_map,
            "marker_inset_px": self.marker_inset_px,
            "name_ocr": self.name_ocr,
            "minimap_name_region": rect(self.minimap_name_region),
            "name_scan_px": self.name_scan_px,
            "debug_capture_dir": self.debug_capture_dir,
            "debug_capture_max_events": self.debug_capture_max_events,
        })
    }

    pub fn reach_path(&self) -> String {
        if self.class_active != "default"
            && !self.class_active.is_empty()
            && !self.class_profiles.is_empty()
        {
            let p = Path::new(&self.nav_reach_file);
            let stem = p
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("nav_reach");
            let ext = p
                .extension()
                .and_then(|s| s.to_str())
                .map(|e| format!(".{e}"))
                .unwrap_or_default();
            let name = format!("{stem}_{}{ext}", self.class_active);
            return match p.parent().filter(|d| !d.as_os_str().is_empty()) {
                Some(dir) => dir.join(name).to_string_lossy().into_owned(),
                None => name,
            };
        }
        self.nav_reach_file.clone()
    }
}

fn parse_travel(s: &str) -> Option<ClassTravel> {
    Some(match s {
        "flash" => ClassTravel::Flash,
        "teleport" => ClassTravel::Teleport,
        "walk" => ClassTravel::Walk,
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn obj(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn empty_bot_config_synthesises_the_default_attack() {
        let cfg = BotConfig::from_json(&Map::new()).unwrap();
        assert_eq!(cfg.skills.len(), 1);
        assert_eq!(cfg.skills[0].name, "attack_a");
        assert_eq!(cfg.reach_path(), "nav_reach.json");
    }

    #[test]
    fn class_profile_overlays_kit_and_owns_skills() {
        let cfg = BotConfig::from_json(&obj(json!({
            "jump_key": "alt",
            "skills": {"g": {"key": "g"}},
            "class": {"active": "mage", "profiles": {
                "mage": {"travel": "teleport", "air_attacks": false, "teleport_key": "w",
                         "jump_key": "space", "skills": {}},
                "erel": {"travel": "flash"}
            }}
        })))
        .unwrap();
        assert_eq!(cfg.class_travel, ClassTravel::Teleport);
        assert!(!cfg.air_attacks);
        assert_eq!(cfg.jump_key, "space");
        assert!(cfg.skills.is_empty()); // `{}` owns the kit
        assert_eq!(cfg.reach_path(), "nav_reach_mage.json");
    }

    #[test]
    fn teleport_without_a_key_falls_back_to_flash() {
        let cfg = BotConfig::from_json(&obj(json!({
            "class": {"active": "m", "profiles": {"m": {"travel": "teleport"}}}
        })))
        .unwrap();
        assert_eq!(cfg.class_travel, ClassTravel::Flash);
    }

    #[test]
    fn bad_numbers_are_errors_and_app_config_is_lenient() {
        assert!(BotConfig::from_json(&obj(json!({"nav_gap_px": "wide"}))).is_err());
        let app = AppConfig::from_json(&json!({"ws_port": "x", "view_fps": 99, "bot": {"k": 1}}));
        assert_eq!(app.ws_port, 8765);
        assert_eq!(app.view_fps, 30.0);
        assert_eq!(app.bot.get("k"), Some(&json!(1)));
    }
}
