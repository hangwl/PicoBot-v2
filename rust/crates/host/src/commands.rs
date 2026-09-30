//! Dashboard edits: class profiles, skills, movement keys, patrol policy,
//! map stats and titles, the route preview, and drawn layout (platforms,
//! ropes, anchors). They run on the `DashboardCommands` thread.
//!
//! Settings are edited in the persisted `bot` block of `config.json` and
//! `BotConfig` is derived from it again — exactly what a restart would
//! load — and a running bot picks the new config up.

use std::cell::RefCell;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

use picobot_core::bot::measure::plan_for;
use picobot_core::bot::navigator::legs_viz;
use picobot_core::config::{BotConfig, ClassTravel};
use picobot_core::layout::{
    erase_target, line_under_feet, next_anchor_name, r4, resnap_anchors, restore_anchors,
    same_lines, shift_line, tidy, Erase, Moved,
};
use picobot_core::maps::{MapEntry, PlayerRule};
use picobot_core::minimap::PlayerTracker;
use picobot_core::platform_fit::{Seg, SegKey};
use picobot_core::rotation::Anchor;
use picobot_core::skills::{skills_to_json, Skill, SkillBook};
use picobot_core::timing::monotonic;
use picobot_core::title::normalize_name;
use picobot_core::vision::{platform_row_at, Image};
use serde_json::{json, Map, Value};

use crate::feed::Eyes;
use crate::host::{dash, Host};
use crate::telegram::Sent;

/// The line under the feet for "align here": this close to it.
const HERE_REACH_PX: f64 = 10.0;
const UNDO_DEPTH: usize = 50;

/// One undo step: the lines before an edit and the anchors it moved.
pub type UndoStep = (Vec<Seg>, Moved);

fn obj(v: &mut Value) -> &mut Map<String, Value> {
    if !v.is_object() {
        *v = json!({});
    }
    v.as_object_mut().expect("just made an object")
}

/// `bot.class`, created if missing.
fn class_block(bot: &mut Map<String, Value>) -> &mut Map<String, Value> {
    obj(bot.entry("class").or_insert_with(|| json!({})))
}

fn kit_summary(cfg: &BotConfig) -> String {
    let extra = match (&cfg.class_travel, &cfg.teleport_key) {
        (ClassTravel::Teleport, Some(k)) => format!(" ({k})"),
        (ClassTravel::Teleport, None) => " (None)".into(),
        _ => String::new(),
    };
    let attacks = if cfg.air_attacks {
        "air attacks"
    } else {
        "attacks on landing"
    };
    format!("{}{extra}, {attacks}", cfg.class_travel.as_str())
}

fn parse_seg(text: &str) -> Option<Seg> {
    let v: Vec<f64> = text
        .split(',')
        .map(|p| p.trim().parse().ok())
        .collect::<Option<_>>()?;
    <[f64; 4]>::try_from(v).ok()
}

impl Host {
    /// The edit commands; false when `msg` isn't one.
    pub(crate) fn handle_edit(self: &Arc<Self>, msg: &str) -> bool {
        let arg = |n: usize| msg.splitn(n + 1, '|').nth(n).unwrap_or("").to_owned();
        let tail = |prefix: &str| msg.strip_prefix(prefix).map(str::to_owned);
        match msg {
            "class|list" => self.send_class(),
            "skills|list" => self.send_skills(),
            "layout|reset" => self.layout_reset(),
            _ if msg.starts_with("class|use|") => self.class_use(arg(2).trim()),
            _ if msg.starts_with("class|caps|") => self.class_caps(&arg(2)),
            _ if msg.starts_with("class|add|") => {
                let mut p = msg.splitn(4, '|').skip(2);
                let (name, spec) = (p.next().unwrap_or(""), p.next().unwrap_or(""));
                self.class_add(name, spec);
            }
            _ if msg.starts_with("patrol|policy|") => self.patrol_set(Some(&arg(2)), None),
            _ if msg.starts_with("safety|set|") => self.safety_set(&arg(2)),
            "notify|test" => self.notify_test(),
            _ if msg.starts_with("attacks|set|") => self.attacks_set(&arg(2)),
            _ if msg.starts_with("layout|erase|") => self.layout_erase(msg),
            _ if msg.starts_with("patrol|temp|") => self.patrol_set(None, Some(&arg(2))),
            "map|stats|reset" => self.map_stats_reset(""),
            _ if msg.starts_with("map|stats|reset|") => self.map_stats_reset(&arg(3)),
            _ if msg.starts_with("map|title|") => self.map_title(msg),
            _ if msg.starts_with("map|players|") => self.map_players(msg),
            _ if msg.starts_with("skills|set|") => self.skills_set(&arg(2)),
            _ if msg.starts_with("skills|del|") => self.skills_del(&arg(2)),
            _ if msg.starts_with("movekeys|set|") => self.movekeys_set(&arg(2)),
            _ if msg.starts_with("nav|") => self.nav_command(msg),
            "layout|save" => self.layout_save(""),
            "layout|clear" => self.layout_clear(""),
            _ if msg.starts_with("layout|save|") => {
                self.layout_save(&tail("layout|save|").unwrap_or_default())
            }
            _ if msg.starts_with("layout|clear|") => {
                self.layout_clear(&tail("layout|clear|").unwrap_or_default())
            }
            _ if msg.starts_with("layout|anchor|") => self.layout_anchor(msg),
            _ if msg.starts_with("layout|rope|") => self.layout_rope(msg),
            _ if msg.starts_with("layout|plat|") => self.layout_platform(msg),
            _ => return false,
        }
        true
    }

    // -- Settings ---------------------------------------------------------------------

    /// Edit the persisted `bot` block and re-derive `BotConfig` from it; a
    /// block that no longer parses is rolled back.
    fn commit_bot(&self, edit: impl FnOnce(&mut Map<String, Value>)) -> Result<BotConfig, String> {
        let (before, raw) = {
            let mut c = self.config.lock().unwrap();
            let before = c.bot.clone();
            edit(&mut c.bot);
            (before, c.bot.clone())
        };
        let mut cfg = match BotConfig::from_json(&raw) {
            Ok(c) => c,
            Err(e) => {
                self.config.lock().unwrap().bot = before;
                return Err(e.to_string());
            }
        };
        {
            let old = self.bot_config.lock().unwrap();
            cfg.active_map = old.active_map.clone();
            cfg.auto_select_map = old.auto_select_map;
        }
        *self.bot_config.lock().unwrap() = cfg.clone();
        self.config_version.fetch_add(1, Ordering::Relaxed);
        self.save_config();
        Ok(cfg)
    }

    pub(crate) fn send_class(&self) {
        let cfg = self.bot_config();
        let plan = plan_for(&cfg);
        let measured: Vec<&str> = {
            let reach = self.reach.lock().unwrap();
            plan.iter()
                .copied()
                .filter(|m| reach.measured.iter().any(|(n, _)| n == m))
                .collect()
        };
        let profiles: Map<String, Value> = cfg
            .class_profiles
            .iter()
            .map(|(name, p)| {
                let row = json!({
                    "travel": p.get("travel").cloned().unwrap_or_else(|| "flash".into()),
                    "air_attacks": p.get("air_attacks").is_none_or(picobot_core::json::truthy),
                    "double_flash": p.get("double_flash").is_none_or(picobot_core::json::truthy),
                    "teleport_key": p.get("teleport_key").cloned().unwrap_or(Value::Null),
                });
                (name.clone(), row)
            })
            .collect();
        self.clients.broadcast(&dash(json!({
            "event": "class",
            "active": cfg.class_active,
            "profiles": profiles,
            "policy": cfg.patrol_policy.as_str(),
            "temp": cfg.patrol_weight_temp,
            "measure_plan": plan,
            "measured_moves": measured,
            "measured": measured.len(),
        })));
    }

    fn class_add(self: &Arc<Self>, name: &str, spec: &str) {
        let name = name.trim();
        if name.is_empty() {
            self.bus.emit("error", "class profile needs a name");
            return;
        }
        if self
            .bot_config
            .lock()
            .unwrap()
            .class_profiles
            .contains_key(name)
        {
            self.bus
                .emit("error", &format!("class profile '{name}' already exists"));
            return;
        }
        let raw: Value = if spec.trim().is_empty() {
            json!({})
        } else {
            match serde_json::from_str(spec) {
                Ok(v) => v,
                Err(_) => {
                    self.bus.emit("error", "bad class spec");
                    return;
                }
            }
        };
        let keep = [
            "travel",
            "air_attacks",
            "double_flash",
            "teleport_key",
            "teleport_cooldown",
            "skills",
        ];
        let spec: Map<String, Value> = raw
            .as_object()
            .into_iter()
            .flatten()
            .filter(|(k, _)| keep.contains(&k.as_str()))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        let saved = self.commit_bot(|bot| {
            obj(class_block(bot)
                .entry("profiles")
                .or_insert_with(|| json!({})))
            .insert(name.into(), Value::Object(spec));
        });
        if let Err(e) = saved {
            self.bus.emit("error", &format!("bad class spec: {e}"));
            return;
        }
        self.bus
            .emit("bot", &format!("class profile {name} created"));
        self.class_use(name);
    }

    /// `class|caps|{json}`: the active profile's capabilities
    /// (`double_flash`, `air_attacks`).
    fn class_caps(self: &Arc<Self>, spec: &str) {
        let Ok(Value::Object(want)) = serde_json::from_str::<Value>(spec) else {
            self.bus
                .emit("error", "class capabilities must be a JSON object");
            return;
        };
        let mut edits: Vec<(String, bool)> = Vec::new();
        for (k, v) in &want {
            match (k.as_str(), v) {
                ("double_flash" | "air_attacks", Value::Bool(b)) => edits.push((k.clone(), *b)),
                _ => {
                    self.bus
                        .emit("error", &format!("invalid class capability: {k}"));
                    return;
                }
            }
        }
        let active = self.bot_config().class_active;
        if let Err(e) = self.commit_bot(|bot| {
            let profiles = obj(class_block(bot)
                .entry("profiles")
                .or_insert_with(|| json!({})));
            let profile = obj(profiles.entry(active).or_insert_with(|| json!({})));
            for (k, b) in edits {
                profile.insert(k, b.into());
            }
        }) {
            self.bus.emit("error", &e);
            return;
        }
        self.bus.emit("bot", "class capabilities saved");
        self.send_class();
        self.send_config();
    }

    /// Apply a class profile live: the kit decides which moves exist, so
    /// the bot and any measurement stop first, and reach is per character.
    fn class_use(self: &Arc<Self>, name: &str) {
        if !self
            .bot_config
            .lock()
            .unwrap()
            .class_profiles
            .contains_key(name)
        {
            self.bus
                .emit("error", &format!("no class profile named '{name}'"));
            return;
        }
        Host::stop_slot(&self.bot);
        if Host::slot_running(&self.measurer) {
            Host::stop_slot(&self.measurer);
            self.bus
                .emit("measure", "measurement stopped — class switched");
        }
        let cfg = match self.commit_bot(|bot| {
            class_block(bot).insert("active".into(), name.into());
        }) {
            Ok(c) => c,
            Err(e) => {
                self.bus.emit("error", &format!("class {name}: {e}"));
                return;
            }
        };
        let saved = self.reach.lock().unwrap().save(true);
        if let Err(e) = saved {
            self.bus.emit("error", &format!("saving reach failed: {e}"));
        }
        let reach = picobot_core::reach::ReachModel::load(
            picobot_core::reach::base_reach(&cfg),
            self.root.join(cfg.reach_path()),
        );
        self.put_reach(reach);
        *self.last_measure.lock().unwrap() = None; // the plan follows the kit
        self.bus
            .emit("bot", &format!("class: {name} ({})", kit_summary(&cfg)));
        self.send_class();
        self.send_skills();
        self.send_config();
        self.send_measure(None);
    }

    fn patrol_set(&self, policy: Option<&str>, temp: Option<&str>) {
        let t = match temp {
            Some(t) => match t.trim().parse::<f64>() {
                Ok(v) if v.is_finite() => Some(v.max(0.05)),
                _ => {
                    self.bus
                        .emit("error", &format!("invalid patrol temperature: {t:?}"));
                    return;
                }
            },
            None => None,
        };
        let policy = policy.filter(|p| ["weighted", "greedy"].contains(p));
        let cfg = match self.commit_bot(|bot| {
            if let Some(p) = policy {
                bot.insert("patrol_policy".into(), p.into());
            }
            if let Some(t) = t {
                bot.insert("patrol_weight_temp".into(), t.into());
            }
        }) {
            Ok(c) => c,
            Err(e) => {
                self.bus.emit("error", &e);
                return;
            }
        };
        self.bus.emit(
            "bot",
            &format!(
                "patrol policy: {} (temp {:?})",
                cfg.patrol_policy.as_str(),
                cfg.patrol_weight_temp
            ),
        );
        self.send_class();
    }

    /// `safety|set|{json}`: the stop-when toggles and the heartbeat.
    fn safety_set(&self, spec: &str) {
        let Ok(Value::Object(want)) = serde_json::from_str::<Value>(spec) else {
            self.bus
                .emit("error", "safety settings must be a JSON object");
            return;
        };
        let bools = [
            "stop_when_players_appear",
            "stop_when_rune_appears",
            "stop_when_map_unrecognized",
        ];
        let mut edits: Vec<(String, Value)> = Vec::new();
        for (k, v) in &want {
            match (k.as_str(), v) {
                ("allowed_other_players", v) if v.as_i64().is_some_and(|n| n >= 0) => {
                    edits.push((k.clone(), v.clone()))
                }
                ("other_player_min_px", v) if v.as_i64().is_some_and(|n| n >= 1) => {
                    edits.push((k.clone(), v.clone()))
                }
                (k, Value::Bool(_)) if bools.contains(&k) => edits.push((k.into(), v.clone())),
                ("heartbeat_minutes", v) if v.as_f64().is_some_and(|m| m >= 0.0) => {
                    edits.push((k.clone(), v.clone()))
                }
                _ => {
                    self.bus
                        .emit("error", &format!("invalid safety setting: {k}"));
                    return;
                }
            }
        }
        if let Err(e) = self.commit_bot(|bot| {
            for (k, v) in edits {
                bot.insert(k, v);
            }
        }) {
            self.bus.emit("error", &e);
            return;
        }
        self.bus.emit("bot", "safety settings saved");
        self.send_config();
    }

    /// `map|players|<follow|ignore|allow:N>[|<name>]`: how this map treats
    /// other-player markers.
    fn map_players(&self, msg: &str) {
        let mut p = msg.splitn(4, '|').skip(2);
        let rule = match PlayerRule::parse(p.next().unwrap_or("")) {
            Ok(r) => r,
            Err(e) => {
                self.bus.emit("error", &e.to_string());
                return;
            }
        };
        let Some(mut entry) = self.target_or_report(p.next().unwrap_or("").trim()) else {
            return;
        };
        entry.other_players = rule;
        let name = entry.name.clone();
        self.save_entry(entry);
        let what = match rule {
            None => "follows the global setting".to_owned(),
            Some(PlayerRule::Ignore) => "ignores other-player markers".to_owned(),
            Some(PlayerRule::Allow(n)) => format!("allows up to {n} other players"),
        };
        self.bus.emit("map", &format!("{name} {what}"));
    }

    /// `attacks|set|{json}`: how often moves and landings carry attacks,
    /// and the attack rate to steer toward.
    fn attacks_set(&self, spec: &str) {
        let Ok(Value::Object(want)) = serde_json::from_str::<Value>(spec) else {
            self.bus
                .emit("error", "attack settings must be a JSON object");
            return;
        };
        let mut edits: Vec<(String, f64)> = Vec::new();
        for (k, v) in &want {
            let chance = matches!(
                k.as_str(),
                "move_attack_chance" | "ground_attack_chance" | "weave_double_chance"
            );
            match v.as_f64() {
                Some(n) if chance && (0.0..=1.0).contains(&n) => edits.push((k.clone(), n)),
                Some(n) if k == "target_attacks_per_min" && n >= 0.0 => edits.push((k.clone(), n)),
                _ => {
                    self.bus
                        .emit("error", &format!("invalid attack setting: {k}"));
                    return;
                }
            }
        }
        if let Err(e) = self.commit_bot(|bot| {
            for (k, n) in edits {
                bot.insert(k, n.into());
            }
        }) {
            self.bus.emit("error", &e);
            return;
        }
        self.bus.emit("bot", "attack settings saved");
        self.send_config();
    }

    /// `notify|test`: one Telegram message, answered in the log.
    fn notify_test(&self) {
        match self
            .telegram
            .send("PicoBot (Rust host): test alert — Telegram works.")
        {
            Sent::Ok => self.bus.emit("notify", "test alert sent to Telegram"),
            other => self
                .bus
                .emit("error", &format!("Telegram test failed: {other:?}")),
        }
    }

    /// `layout|erase|x,y[|name]`: delete the anchor or learned rope under
    /// a click (minimap px); a stray click deletes nothing.
    fn layout_erase(&self, msg: &str) {
        let mut p = msg.splitn(4, '|').skip(2);
        let coords = p.next().unwrap_or("");
        let Some((x, y)) = coords.split_once(',').and_then(|(a, b)| {
            Some((a.trim().parse::<f64>().ok()?, b.trim().parse::<f64>().ok()?))
        }) else {
            return;
        };
        let Some(mut entry) = self.target_or_report(p.next().unwrap_or("").trim()) else {
            return;
        };
        let Some((w, h)) = self.minimap_size() else {
            self.bus.emit("error", "no minimap frame — can't erase");
            return;
        };
        let ropes = entry.ropes.clone().unwrap_or_default();
        let name = entry.name.clone();
        match erase_target(&entry.rotation.anchors, &ropes, x, y, w, h) {
            Erase::Anchor(i) => {
                let removed = entry.rotation.anchors[i].name.clone();
                entry.rotation.remove_anchor(i);
                self.save_entry(entry);
                self.bus
                    .emit("map", &format!("{name}: removed anchor {removed}"));
            }
            Erase::Rope(i) => {
                let mut left = ropes.clone();
                self.push_undo(&name, "ropes", (ropes, Vec::new()));
                left.remove(i);
                let n = left.len();
                Host::set_lines(&mut entry, "ropes", left);
                self.save_entry(entry);
                self.bus.emit(
                    "map",
                    &format!("{name}: removed the rope ({n} left) — it is re-learned if the bot hangs there again"),
                );
            }
            Erase::Nothing => self.bus.emit(
                "map",
                "nothing to erase there — click on an anchor or a rope",
            ),
        }
    }

    pub(crate) fn send_skills(&self) {
        let cfg = self.bot_config();
        let profile = cfg.class_profiles.get(&cfg.class_active);
        let own = profile.is_some_and(|p| p.get("skills").is_some_and(Value::is_object));
        self.clients.broadcast(&dash(json!({
            "event": "skills",
            "source": if profile.is_some() { cfg.class_active.as_str() } else { "global" },
            "inherited": profile.is_some() && !own,
            "skills": skills_to_json(&cfg.skills),
        })));
    }

    /// Write the edited book where it belongs: the active profile's own
    /// kit (seeded from the book in use on its first edit), else the
    /// global book.
    fn commit_skills(&self, edit: impl FnOnce(&mut Map<String, Value>)) -> Result<(), String> {
        let cfg = self.bot_config();
        let active = cfg.class_active.clone();
        let profiled = cfg.class_profiles.contains_key(&active);
        let current = skills_to_json(&cfg.skills);
        self.commit_bot(|bot| {
            let book = if profiled {
                let profiles = obj(class_block(bot)
                    .entry("profiles")
                    .or_insert_with(|| json!({})));
                let p = obj(profiles.entry(active).or_insert_with(|| json!({})));
                let skills = p.entry("skills").or_insert_with(|| current.clone());
                if !skills.is_object() {
                    *skills = current.clone();
                }
                obj(skills)
            } else {
                let skills = bot.entry("skills").or_insert_with(|| current.clone());
                *skills = current.clone(); // the effective book, incl. legacy keys
                obj(skills)
            };
            edit(book);
        })?;
        self.push_skills_to_bot();
        self.send_skills();
        Ok(())
    }

    /// A running bot swaps the new book in on its own thread, cooldowns kept.
    fn push_skills_to_bot(&self) {
        if !self.is_bot_running() {
            return;
        }
        let mut book = SkillBook::new(self.bot_config().skills);
        if let Some(e) = self.identity.entry() {
            book.overlay(&e.skills);
        }
        *self.pending_skills.lock().unwrap() = Some(book.skills().to_vec());
    }

    fn skills_set(&self, payload: &str) {
        let parsed: Result<Skill, String> = serde_json::from_str::<Value>(payload)
            .map_err(|e| e.to_string())
            .and_then(|spec| {
                let name = spec
                    .get("name")
                    .and_then(Value::as_str)
                    .ok_or("missing name")?
                    .to_owned();
                Skill::from_json(&name, &spec).map_err(|e| e.to_string())
            });
        let skill = match parsed {
            Ok(s) => s,
            Err(e) => {
                self.bus.emit("error", &format!("invalid skill spec: {e}"));
                return;
            }
        };
        let (name, spec) = (skill.name.clone(), skill.to_json());
        if let Err(e) = self.commit_skills(|book| {
            book.insert(name, spec);
        }) {
            self.bus.emit("error", &format!("invalid skill spec: {e}"));
            return;
        }
        self.bus.emit(
            "skill",
            &format!(
                "skill saved: {} ({}, {})",
                skill.name,
                skill.kind.as_str(),
                skill.key
            ),
        );
    }

    fn skills_del(&self, name: &str) {
        if !self.bot_config().skills.iter().any(|s| s.name == name) {
            self.bus.emit("error", &format!("no such skill: {name}"));
            return;
        }
        if let Err(e) = self.commit_skills(|book| {
            book.remove(name);
        }) {
            self.bus.emit("error", &e);
            return;
        }
        self.bus.emit("skill", &format!("skill removed: {name}"));
    }

    /// Movement keybinds and the arrival radius. With a profile active the
    /// keys belong to its kit.
    fn movekeys_set(&self, payload: &str) {
        let Ok(spec) = serde_json::from_str::<Map<String, Value>>(payload) else {
            return;
        };
        let key = |v: &Value| -> Option<String> {
            picobot_core::json::truthy(v)
                .then(|| picobot_core::json::as_string(v).trim().to_lowercase())
        };
        let mut kit = Map::new(); // jump_key, up_jump_skill_key
        let mut flash = Map::new(); // flash_jump: {key, enabled}
        let mut radius = None;
        let mut said: Vec<String> = Vec::new();
        if let Some(v) = spec.get("jump_key") {
            let k = key(v).unwrap_or_else(|| "space".into());
            said.push(format!("jump_key={k}"));
            kit.insert("jump_key".into(), k.into());
        }
        if let Some(v) = spec.get("up_jump_skill_key") {
            let k = key(v);
            said.push(format!(
                "up_jump_skill_key={}",
                k.as_deref().unwrap_or("None")
            ));
            kit.insert("up_jump_skill_key".into(), k.into());
        }
        if let Some(v) = spec.get("flash_jump_key") {
            flash.insert("key".into(), key(v).into());
        }
        if let Some(v) = spec.get("flash_jump_enabled") {
            flash.insert("enabled".into(), picobot_core::json::truthy(v).into());
        }
        if !flash.is_empty() {
            said.push(format!("flash_jump={}", Value::Object(flash.clone())));
        }
        if let Some(v) = spec.get("nav_threshold_px").filter(|v| !v.is_null()) {
            match picobot_core::json::as_i64(v) {
                Some(r) => {
                    let r = r.clamp(2, 15);
                    said.push(format!("nav_threshold_px={r}"));
                    radius = Some(r);
                }
                None => {
                    self.bus
                        .emit("error", &format!("invalid arrival radius: {v}"));
                    return;
                }
            }
        }
        if said.is_empty() {
            return;
        }
        let cfg = self.bot_config();
        let active = cfg.class_active.clone();
        let profiled = cfg.class_profiles.contains_key(&active);
        let saved = self.commit_bot(|bot| {
            let target = if profiled {
                let profiles = obj(class_block(bot)
                    .entry("profiles")
                    .or_insert_with(|| json!({})));
                obj(profiles.entry(active).or_insert_with(|| json!({})))
            } else {
                &mut *bot
            };
            target.extend(kit);
            if !flash.is_empty() {
                obj(target.entry("flash_jump").or_insert_with(|| json!({}))).extend(flash);
            }
            if let Some(r) = radius {
                bot.insert("nav_threshold_px".into(), r.into());
            }
        });
        if let Err(e) = saved {
            self.bus.emit("error", &e);
            return;
        }
        self.bus
            .emit("bot", &format!("movement keys: {}", said.join(", ")));
        self.send_config();
    }

    // -- Maps -------------------------------------------------------------------------

    fn entry_named(&self, name: &str) -> Option<MapEntry> {
        let name = name.trim();
        if name.is_empty() {
            self.identity.entry()
        } else {
            self.maps.lock().unwrap().get(name).cloned()
        }
    }

    fn map_stats_reset(&self, name: &str) {
        let Some(entry) = self.entry_named(name) else {
            return;
        };
        self.anchor_stats.lock().unwrap().reset(&entry.name);
        self.bus
            .emit("map", &format!("{}: anchor stats reset", entry.name));
        self.send_maps();
    }

    /// `map|title|record|clear[|<name>]`: the map's recorded title.
    fn map_title(&self, msg: &str) {
        let parts: Vec<&str> = msg.split('|').collect();
        let op = parts.get(2).copied().unwrap_or("");
        let Some(mut entry) = self.entry_named(parts.get(3).copied().unwrap_or("")) else {
            self.bus
                .emit("error", "pick the map first (no map resolved)");
            return;
        };
        match op {
            "clear" => {
                if entry.map_name.is_none() {
                    self.bus
                        .emit("map", &format!("{} has no recorded title", entry.name));
                    return;
                }
                entry.map_name = None;
                let name = entry.name.clone();
                self.save_entry(entry);
                self.bus
                    .emit("map", &format!("{name}: recorded title cleared"));
            }
            "record" => {
                let Some(title) = self.identity.current().title else {
                    self.bus
                        .emit("error", "no title read yet — press Re-detect and wait");
                    return;
                };
                let want = normalize_name(&title);
                let other = self
                    .maps
                    .lock()
                    .unwrap()
                    .load_all()
                    .iter()
                    .find(|e| {
                        e.name != entry.name
                            && e.map_name
                                .as_deref()
                                .is_some_and(|m| normalize_name(m) == want)
                    })
                    .map(|e| e.name.clone());
                if let Some(o) = other {
                    self.bus.emit(
                        "error",
                        &format!("that title is already recorded for {o} — clear it there first"),
                    );
                    return;
                }
                entry.map_name = Some(title.clone());
                let name = entry.name.clone();
                self.save_entry(entry);
                self.bus
                    .emit("map", &format!("{name}: recorded title set to \"{title}\""));
            }
            _ => {}
        }
    }

    // -- Route preview ------------------------------------------------------------------

    /// `nav|show|on|off`, `nav|preview|x,y` (minimap px): a route from the
    /// player to a clicked point, shown for 20s.
    fn nav_command(&self, msg: &str) {
        let parts: Vec<&str> = msg.split('|').collect();
        if parts.len() < 3 {
            return;
        }
        match parts[1] {
            "show" => {
                let on = parts[2] == "on";
                self.nav_show.store(on, Ordering::Relaxed);
                if !on {
                    *self.nav_preview.lock().unwrap() = None;
                }
            }
            "preview" => {
                let Some((gx, gy)) = parts[2]
                    .split_once(',')
                    .and_then(|(a, b)| Some((a.trim().parse().ok()?, b.trim().parse().ok()?)))
                else {
                    return;
                };
                self.nav_show.store(true, Ordering::Relaxed);
                let region = self.live_region();
                let entry = self.identity.entry();
                let graph =
                    self.nav_graph(entry.as_ref(), region.map(|r| (r.2 as f64, r.3 as f64)));
                let Some(graph) = graph else {
                    self.bus.emit(
                        "error",
                        "route preview needs a resolved map with drawn platforms",
                    );
                    return;
                };
                let pos = self
                    .bot_viz()
                    .and_then(|v| v.player)
                    .or_else(|| self.read_feet(1));
                let Some(pos) = pos else {
                    self.bus.emit("error", "route preview: no player position");
                    return;
                };
                match graph.route(pos, (gx, gy), &[]) {
                    Some(legs) => {
                        let kinds: Vec<&str> = legs.iter().map(|l| l.kind.as_str()).collect();
                        *self.nav_preview.lock().unwrap() =
                            Some((legs_viz(&legs), monotonic() + 20.0));
                        self.bus
                            .emit("nav", &format!("route: {}", kinds.join(" → ")));
                    }
                    None => {
                        *self.nav_preview.lock().unwrap() = None;
                        self.bus.emit(
                            "nav",
                            &format!(
                                "no route from ({:.0}, {:.0}) to ({gx:.0}, {gy:.0}) — off the drawn platforms, or beyond jump reach",
                                pos.0, pos.1
                            ),
                        );
                    }
                }
            }
            _ => {}
        }
    }

    // -- Layout -----------------------------------------------------------------------

    /// Which map a layout write goes to. A typed name is the user's word
    /// (a new file is made if needed); blank needs the title-verified map
    /// — a pin alone is no evidence of what's on screen.
    fn layout_target(&self, name: &str) -> Result<MapEntry, String> {
        let name = name.trim();
        if !name.is_empty() {
            let found = self.maps.lock().unwrap().get(name).cloned();
            return Ok(found.unwrap_or_else(|| MapEntry::new(name)));
        }
        match (self.identity.entry(), self.identity.current().via) {
            (Some(e), Some("ocr")) => Ok(e),
            _ => Err(
                "current map isn't verified by its title — type the map name to save anyway".into(),
            ),
        }
    }

    fn target_or_report(&self, name: &str) -> Option<MapEntry> {
        match self.layout_target(name) {
            Ok(e) => Some(e),
            Err(e) => {
                self.bus.emit("error", &e);
                None
            }
        }
    }

    fn minimap_size(&self) -> Option<(f64, f64)> {
        self.live_region()
            .filter(|r| r.2 > 0 && r.3 > 0)
            .map(|r| (r.2 as f64, r.3 as f64))
    }

    /// A minimap capture on this thread (its own grabber).
    fn minimap_capture(&self) -> Option<Image> {
        thread_local! {
            static EYES: RefCell<Option<Eyes>> = const { RefCell::new(None) };
        }
        let region = self.get_feed()?.analyzer.region()?;
        let title = self.window_title();
        EYES.with(|cell| {
            let mut cell = cell.borrow_mut();
            if cell.as_ref().is_none_or(|e| e.window.title != title) {
                *cell = Eyes::open(&title);
            }
            cell.as_mut()?.capture(region)
        })
    }

    /// The feet from `reads` captures 0.1s apart, only when they agree
    /// within 1px (standing still).
    fn read_feet(&self, reads: usize) -> Option<(f64, f64)> {
        let feed = self.get_feed()?;
        let mut tracker = PlayerTracker::default();
        let mut got = Vec::new();
        for i in 0..reads {
            if i > 0 {
                std::thread::sleep(Duration::from_millis(100));
            }
            let img = self.minimap_capture()?;
            got.push(feed.analyzer.player_pos(&img, &mut tracker)?);
        }
        let (xs, ys): (Vec<i32>, Vec<i32>) = got.iter().copied().unzip();
        let spread = |v: &[i32]| v.iter().max().unwrap_or(&0) - v.iter().min().unwrap_or(&0);
        if spread(&xs) > 1 || spread(&ys) > 1 {
            return None;
        }
        let mid = |mut v: Vec<i32>| {
            v.sort_unstable();
            v[v.len() / 2] as f64
        };
        Some((mid(xs), mid(ys)))
    }

    fn layout_save(&self, name: &str) {
        let Some(region) = self.live_region() else {
            self.bus.emit("error", "no minimap layout detected to save");
            return;
        };
        let Some(mut entry) = self.target_or_report(name) else {
            return;
        };
        entry.minimap_region = Some([
            region.0 as i64,
            region.1 as i64,
            region.2 as i64,
            region.3 as i64,
        ]);
        let res = self.identity.current();
        if entry.map_name.is_none()
            && res.title.is_some()
            && res.title_map.as_ref().is_none_or(|m| *m == entry.name)
        {
            entry.map_name = res.title;
        }
        let name = entry.name.clone();
        self.save_entry(entry);
        self.bus.emit(
            "map",
            &format!(
                "layout saved for {name}: [{}, {}, {}, {}]",
                region.0, region.1, region.2, region.3
            ),
        );
    }

    fn layout_clear(&self, name: &str) {
        let Some(mut entry) = self.target_or_report(name) else {
            return;
        };
        if entry.minimap_region.is_none() {
            self.bus
                .emit("error", &format!("{} has no stored layout", entry.name));
            return;
        }
        entry.minimap_region = None;
        let name = entry.name.clone();
        self.save_entry(entry);
        self.bus.emit("map", &format!("layout cleared for {name}"));
    }

    fn layout_reset(&self) {
        let feed = self.feed.lock().unwrap().clone();
        match feed {
            Some(f) => {
                f.analyzer.reset_region();
                self.bus
                    .emit("vision", "minimap layout reset — re-detecting");
            }
            None => self.bus.emit("vision", "no minimap to reset"),
        }
    }

    fn platforms_px(entry: &MapEntry, w: f64, h: f64) -> Vec<Seg> {
        entry
            .platforms
            .iter()
            .flatten()
            .map(|s| [s[0] * w, s[1] * h, s[2] * w, s[3] * h])
            .collect()
    }

    /// `layout|anchor|x,y | del|x,y | undo | clear [|<name>]`: a click
    /// snaps onto the drawn platform under it, floating like the dot.
    fn layout_anchor(&self, msg: &str) {
        let parts: Vec<&str> = msg.split('|').collect();
        if parts.len() < 3 {
            return;
        }
        let op = parts[2];
        let (coords, rest) = if op == "del" {
            (parts.get(3).copied().unwrap_or(""), parts.get(4..))
        } else {
            (op, parts.get(3..))
        };
        let name = rest.and_then(|r| r.first()).map_or("", |s| s.trim());
        let Some(mut entry) = self.target_or_report(name) else {
            return;
        };
        let rot = &mut entry.rotation;
        if op == "undo" || op == "clear" {
            if rot.anchors.is_empty() {
                self.bus.emit("map", &format!("{}: no anchors", entry.name));
                return;
            }
            let n = if op == "clear" { rot.anchors.len() } else { 1 };
            for _ in 0..n {
                let last = rot.anchors.len() - 1;
                rot.remove_anchor(last);
            }
            let left = rot.anchors.len();
            let name = entry.name.clone();
            self.save_entry(entry);
            self.bus
                .emit("map", &format!("{name}: {left} anchor(s) left"));
            return;
        }
        let Some((x, y)) = coords.split_once(',').and_then(|(a, b)| {
            Some((a.trim().parse::<f64>().ok()?, b.trim().parse::<f64>().ok()?))
        }) else {
            return;
        };
        let Some((w, h)) = self.minimap_size() else {
            self.bus
                .emit("error", "no minimap frame — can't place anchors");
            return;
        };
        if !(0.0..=w).contains(&x) || !(0.0..=h).contains(&y) {
            self.bus.emit("error", "anchor click is off the minimap");
            return;
        }
        if op == "del" {
            let d = |a: &Anchor| (a.x * w - x).powi(2) + (a.y * h - y).powi(2);
            let Some(i) = (0..rot.anchors.len())
                .min_by(|&a, &b| d(&rot.anchors[a]).total_cmp(&d(&rot.anchors[b])))
            else {
                return;
            };
            let removed = rot.anchors[i].name.clone();
            rot.remove_anchor(i);
            let name = entry.name.clone();
            self.save_entry(entry);
            self.bus
                .emit("map", &format!("{name}: removed anchor {removed}"));
            return;
        }
        let segs = Host::platforms_px(&entry, w, h);
        let snapped = platform_row_at(&segs, x, y, 12.0);
        let y = match snapped {
            Some(row) => row as f64 - self.bot_config.lock().unwrap().anchor_float_px,
            None => y,
        };
        let rot = &mut entry.rotation;
        let an = next_anchor_name(&rot.anchors);
        rot.anchors.push(Anchor::new(&an, r4(x / w), r4(y / h)));
        let name = entry.name.clone();
        self.save_entry(entry);
        let note = if snapped.is_some() {
            ""
        } else {
            " (no drawn platform under it)"
        };
        self.bus
            .emit("map", &format!("{name}: anchor {an} placed{note}"));
    }

    fn push_undo(&self, map: &str, field: &'static str, step: UndoStep) {
        let mut undo = self.layout_undo.lock().unwrap();
        let stack = undo.entry((map.to_owned(), field)).or_default();
        stack.push(step);
        if stack.len() > UNDO_DEPTH {
            stack.remove(0);
        }
    }

    fn lines_of(entry: &MapEntry, field: &str) -> Vec<Seg> {
        let lines = if field == "platforms" {
            &entry.platforms
        } else {
            &entry.ropes
        };
        lines.clone().unwrap_or_default()
    }

    fn set_lines(entry: &mut MapEntry, field: &str, segs: Vec<Seg>) {
        let v = (!segs.is_empty()).then_some(segs);
        if field == "platforms" {
            entry.platforms = v;
        } else {
            entry.ropes = v;
        }
    }

    /// `layout|<plat|rope>|x0,y0,x1,y1|undo|clear|tidy[|<name>]`: drawn
    /// segments (px here, stored normalised). New platform drags are
    /// tidied, and anchors follow lines that move.
    fn layout_segments(&self, msg: &str, field: &'static str, label: &str) {
        let parts: Vec<&str> = msg.splitn(4, '|').collect();
        if parts.len() < 3 {
            return;
        }
        let (payload, name) = (parts[2], parts.get(3).map_or("", |s| s.trim()));
        let Some(mut entry) = self.target_or_report(name) else {
            return;
        };
        let segs = Host::lines_of(&entry, field);
        let key = (entry.name.clone(), field);
        match payload {
            "undo" => {
                let step = self
                    .layout_undo
                    .lock()
                    .unwrap()
                    .get_mut(&key)
                    .and_then(Vec::pop);
                let Some((before, moved)) = step else {
                    // Only this session's edits can be undone.
                    self.bus
                        .emit("map", &format!("{}: no {label} edit to undo", entry.name));
                    return;
                };
                restore_anchors(&mut entry.rotation.anchors, &moved);
                let left = before.len();
                Host::set_lines(&mut entry, field, before);
                let name = entry.name.clone();
                self.save_entry(entry);
                self.bus
                    .emit("map", &format!("{name}: undid {label} ({left} left)"));
            }
            "clear" => {
                self.push_undo(&entry.name, field, (segs, Vec::new()));
                Host::set_lines(&mut entry, field, Vec::new());
                let name = entry.name.clone();
                self.save_entry(entry);
                self.bus.emit("map", &format!("{label} cleared for {name}"));
            }
            "tidy" => {
                let Some((w, h)) = self.minimap_size() else {
                    self.bus
                        .emit("error", &format!("no minimap frame — can't tidy {label}"));
                    return;
                };
                let tidied = tidy(&segs, w, h);
                if same_lines(&tidied, &segs) {
                    self.bus.emit(
                        "map",
                        &format!("{}: {label}s already tidy — nothing to level or merge (tidy doesn't move lines to the feet)", entry.name),
                    );
                    return;
                }
                let moved = resnap_anchors(&mut entry.rotation.anchors, &segs, &tidied, w, h, 4.0);
                let (n0, n1, nm) = (segs.len(), tidied.len(), moved.len());
                self.push_undo(&entry.name, field, (segs, moved));
                Host::set_lines(&mut entry, field, tidied);
                let name = entry.name.clone();
                self.save_entry(entry);
                let tail = if nm > 0 {
                    format!(", moved {nm} anchor(s) with their lines")
                } else {
                    String::new()
                };
                self.bus.emit(
                    "map",
                    &format!("{name}: tidied {label}s ({n0} → {n1}){tail}"),
                );
            }
            _ => {
                let Some(seg) = parse_seg(payload) else {
                    return;
                };
                if (seg[0] - seg[2]).powi(2) + (seg[1] - seg[3]).powi(2) < 16.0 {
                    return; // an accidental click
                }
                let Some((w, h)) = self.minimap_size() else {
                    self.bus
                        .emit("error", &format!("no minimap frame — can't draw {label}"));
                    return;
                };
                if seg.iter().any(|v| *v < 0.0)
                    || seg[0] > w
                    || seg[2] > w
                    || seg[1] > h
                    || seg[3] > h
                {
                    self.bus
                        .emit("error", &format!("{label} drag is off the minimap"));
                    return;
                }
                let before = segs.clone();
                let mut segs = segs;
                segs.push([
                    r4(seg[0] / w),
                    r4(seg[1] / h),
                    r4(seg[2] / w),
                    r4(seg[3] / h),
                ]);
                let mut moved = Vec::new();
                if field == "platforms" {
                    // Hand drags are never level: straighten, merge overlaps.
                    segs = tidy(&segs, w, h);
                    moved = resnap_anchors(&mut entry.rotation.anchors, &before, &segs, w, h, 4.0);
                }
                self.push_undo(&entry.name, field, (before, moved));
                let n = segs.len();
                Host::set_lines(&mut entry, field, segs);
                let name = entry.name.clone();
                self.save_entry(entry);
                self.bus.emit("map", &format!("{name}: {label} {n} drawn"));
            }
        }
    }

    fn layout_platform(&self, msg: &str) {
        if msg.starts_with("layout|plat|feet|") {
            self.platform_to_feet(msg);
        } else if msg == "layout|plat|here" || msg.starts_with("layout|plat|here|") {
            self.platform_here(msg);
        } else {
            self.layout_segments(msg, "platforms", "platform");
        }
    }

    /// `layout|rope|del|<x0,y0,x1,y1>[|<name>]` removes one learned rope;
    /// the rest as for platforms.
    fn layout_rope(&self, msg: &str) {
        let Some(rest) = msg.strip_prefix("layout|rope|del|") else {
            self.layout_segments(msg, "ropes", "rope");
            return;
        };
        let mut p = rest.splitn(2, '|');
        let Some(want) = p.next().and_then(parse_seg).map(|s| SegKey::of(&s)) else {
            return;
        };
        let Some(mut entry) = self.target_or_report(p.next().unwrap_or("")) else {
            return;
        };
        let mut ropes = entry.ropes.clone().unwrap_or_default();
        let Some(idx) = ropes.iter().position(|r| SegKey::of(r) == want) else {
            self.bus.emit(
                "error",
                "that rope changed — reopen the map page and try again",
            );
            return;
        };
        self.push_undo(&entry.name, "ropes", (ropes.clone(), Vec::new()));
        let gone = ropes.remove(idx);
        let left = ropes.len();
        Host::set_lines(&mut entry, "ropes", ropes);
        let name = entry.name.clone();
        self.save_entry(entry);
        let at = self
            .minimap_size()
            .map(|(w, _)| format!(" at x {:.0}", gone[0] * w))
            .unwrap_or_default();
        self.bus.emit("map", &format!("{name}: removed the rope{at} ({left} left) — it is re-learned if the bot hangs there again"));
    }

    /// `layout|plat|feet|<x0,y0,x1,y1>[|<name>]`: move one line by its
    /// platform-fit offset, onto where the feet settle.
    fn platform_to_feet(&self, msg: &str) {
        let rest = msg.strip_prefix("layout|plat|feet|").unwrap_or("");
        let mut p = rest.splitn(2, '|');
        let Some(want) = p.next().and_then(parse_seg).map(|s| SegKey::of(&s)) else {
            return;
        };
        let Some(entry) = self.target_or_report(p.next().unwrap_or("")) else {
            return;
        };
        let segs = entry.platforms.clone().unwrap_or_default();
        let Some(idx) = segs.iter().position(|s| SegKey::of(s) == want) else {
            self.bus.emit(
                "error",
                "that platform changed — reopen the map page and try again",
            );
            return;
        };
        let Some(dy) = self.platfit.lock().unwrap().offset(&entry.name, &segs[idx]) else {
            self.bus
                .emit("error", "not enough samples on that platform yet");
            return;
        };
        if dy.abs() < 0.5 {
            self.bus.emit(
                "map",
                &format!("{}: that platform already sits at the feet", entry.name),
            );
            return;
        }
        let Some((w, h)) = self.minimap_size() else {
            self.bus
                .emit("error", "no minimap frame — can't move the platform");
            return;
        };
        self.shift_platform(entry, segs, idx, dy, w, h, "onto the feet");
    }

    /// `layout|plat|here[|<name>]`: move the line under the standing
    /// player onto their feet, measured now.
    fn platform_here(&self, msg: &str) {
        let name = msg.split('|').nth(3).unwrap_or("");
        let Some(entry) = self.target_or_report(name) else {
            return;
        };
        let segs = entry.platforms.clone().unwrap_or_default();
        if segs.is_empty() {
            self.bus
                .emit("error", &format!("{} has no drawn platforms", entry.name));
            return;
        }
        let size = self.minimap_size();
        let feet = size.and_then(|_| self.read_feet(3));
        let (Some((w, h)), Some(feet)) = (size, feet) else {
            self.bus.emit(
                "error",
                "couldn't read a steady position — stand still on the platform and try again",
            );
            return;
        };
        let Some((idx, dy)) = line_under_feet(&segs, w, h, feet, HERE_REACH_PX) else {
            self.bus.emit(
                "error",
                &format!(
                    "no drawn platform within {HERE_REACH_PX:.0}px of your feet (x {}, y {})",
                    feet.0, feet.1
                ),
            );
            return;
        };
        if dy.abs() < 0.5 {
            self.bus.emit(
                "map",
                &format!("{}: that platform already sits at your feet", entry.name),
            );
            return;
        }
        self.shift_platform(entry, segs, idx, dy, w, h, "onto your feet");
    }

    /// Move one line `dy` px (down +), carrying its anchors and fit
    /// samples; undoable like any platform edit.
    #[allow(clippy::too_many_arguments)]
    fn shift_platform(
        &self,
        mut entry: MapEntry,
        segs: Vec<Seg>,
        idx: usize,
        dy: f64,
        w: f64,
        h: f64,
        why: &str,
    ) {
        let old = segs[idx];
        let new = shift_line(&old, dy, h);
        let mut moved_to = segs.clone();
        moved_to[idx] = new;
        let moved = resnap_anchors(
            &mut entry.rotation.anchors,
            &segs,
            &moved_to,
            w,
            h,
            dy.abs() + 1.0,
        );
        let followed = moved.len();
        self.push_undo(&entry.name, "platforms", (segs, moved));
        entry.platforms = Some(moved_to);
        let name = entry.name.clone();
        self.save_entry(entry);
        self.platfit.lock().unwrap().moved(&name, &old, &new, dy);
        let row = (old[1] + old[3]) / 2.0 * h;
        let dir = if dy > 0.0 { "down" } else { "up" };
        let tail = if followed > 0 {
            format!("; {followed} anchor(s) followed")
        } else {
            String::new()
        };
        self.bus.emit(
            "map",
            &format!(
                "{name}: moved the platform at y {row:.0} {dir} {:.1}px {why}{tail}",
                dy.abs()
            ),
        );
        self.send_maps();
    }
}
