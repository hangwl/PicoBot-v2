//! The bot's body: what it senses, how it presses keys, and the movement
//! primitives built from those key presses.
//!
//! [`Body`] has a small set of required methods — capture, dot position,
//! hazards, focus, keys, a stop-aware sleep, the clock, the current map —
//! and implements every movement primitive (flash hop, timed up flash,
//! rope lift, rope grab, climb, walking with approach hops, teleport,
//! attack weaving) as a default method on top of them. The host's real body
//! uses the defaults; the test simulator overrides the airborne ones with
//! instant physics, as the Python tests do.

use std::sync::Arc;

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};

use super::session::Session;
use crate::config::{BotConfig, ClassTravel};
use crate::effects::{SkillEffect, MOVES_PX};
use crate::maps::MapEntry;
use crate::navgraph::{GraphCache, GraphOptions, NavGraph};
use crate::reach::{base_reach, ReachModel};
use crate::rotation::{resolve_coord, Rotation};
use crate::skills::{Skill, SkillBook, Stance};
use crate::summons::SummonTracker;
use crate::timing::{human_between, human_hold, key_gap, release_lag};
use crate::vision::{platform_row_at, platform_span_at, Image};

/// A sideways direction.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Dir {
    Left,
    Right,
}

impl Dir {
    pub fn key(self) -> &'static str {
        match self {
            Dir::Left => "left",
            Dir::Right => "right",
        }
    }

    pub fn toward(dx: f64) -> Dir {
        if dx > 0.0 {
            Dir::Right
        } else {
            Dir::Left
        }
    }

    pub fn flip(self) -> Dir {
        match self {
            Dir::Left => Dir::Right,
            Dir::Right => Dir::Left,
        }
    }
}

/// Key presses to the Pico (implemented by `picobot_io::hid::HidController`).
pub trait Keys {
    fn key_down(&mut self, key: &str) -> bool;
    fn key_up(&mut self, key: &str) -> bool;
    fn press(&mut self, key: &str, hold: Option<f64>) -> bool;
    fn release_all(&mut self);
}

/// A drawn leg: (kind, x0, y0, x1, y1).
pub type LegViz = (String, f64, f64, f64, f64);

/// What the dashboard shows of the bot (published by the host).
#[derive(Debug, Clone, Default)]
pub struct Viz {
    pub state: String,
    pub player: Option<(f64, f64)>,
    pub target: Option<(f64, f64)>,
    /// The leg being run and the rest of the plan: (kind, x0, y0, x1, y1).
    pub route: Option<Vec<LegViz>>,
    pub plan: Option<Vec<LegViz>>,
    pub hazard: Option<String>,
    /// Other-player markers on the minimap right now.
    pub others: usize,
    /// The run's counters (`Session::snapshot`), refreshed by the host.
    pub session: Option<serde_json::Value>,
    /// Live summons: (skill, anchor, seconds left).
    pub summons: Vec<(String, String, f64)>,
    /// Summon charges: (skill, have, max).
    pub summon_charges: Vec<(String, u32, u32)>,
    pub patrol: Option<PatrolStatus>,
}

/// Where the patrol loop stands (the dashboard's Home status).
#[derive(Debug, Clone, Default, PartialEq, serde::Serialize)]
pub struct PatrolStatus {
    pub target: Option<String>,
    pub leg: Option<usize>,
    pub legs: Option<usize>,
    #[serde(rename = "move")]
    pub move_kind: Option<String>,
    pub misses: u32,
    pub next: Vec<String>,
    pub arrived: u32,
    pub halted: bool,
}

/// Where an attack is cast: the stance a skill is tagged for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Window {
    Air,
    Ground,
}

impl Window {
    fn allows(self, s: Stance) -> bool {
        matches!(
            (s, self),
            (Stance::Any, _) | (Stance::Air, Window::Air) | (Stance::Ground, Window::Ground)
        )
    }

    fn exclusive(self) -> Stance {
        match self {
            Window::Air => Stance::Air,
            Window::Ground => Stance::Ground,
        }
    }
}

/// A ready skill tagged only for its window is cast at least this often.
const EXCLUSIVE_READY_CHANCE: f64 = 0.85;
/// The attack rate is counted over this many seconds.
const RATE_WINDOW_S: f64 = 60.0;

/// Everything the bot remembers between ticks. Owned by the bot thread.
pub struct BotState {
    pub skills: SkillBook,
    pub summons: SummonTracker,
    pub reach: ReachModel,
    pub graphs: GraphCache,
    pub rng: StdRng,
    pub viz: Viz,
    /// Monotonic time of the last rope lift / teleport.
    pub last_rope_lift: f64,
    pub last_teleport: f64,
    /// Learned minimap px one flash weave covers.
    pub hop_px: f64,
    pub travel_attack_at: f64,
    /// Px a skill may shift the current move's landing (back, forward),
    /// while a flash is planned onto a platform; None = unconstrained.
    pub air_slack: Option<(f64, f64)>,
    /// When each attack was cast, over the last `RATE_WINDOW_S`.
    pub attack_log: std::collections::VecDeque<f64>,
    pub anchor_idx: usize,
    pub travel_target: Option<usize>,
    /// Anchor index → monotonic time its ban lapses.
    pub bans: std::collections::HashMap<usize, f64>,
    /// Anchors just reached, waiting for their summon on the next tick.
    pub arrive_pending: Vec<usize>,
    pub weave_dir: Option<Dir>,
    pub weave_bounds: Option<(f64, f64)>,
    pub roam_origin: Option<(f64, f64)>,
    /// Skill edits from the dashboard, swapped in on the bot thread.
    pub pending_skills: Option<Vec<Skill>>,
    /// The map version the state was last synced to.
    pub map_version: u64,
    /// No-platform patrol: anchors left to visit, and the head's deadline.
    pub route: Vec<usize>,
    pub checkpoint: Option<(usize, f64)>,
    pub session: Session,
    log_throttle: std::collections::HashMap<&'static str, f64>,
}

impl BotState {
    pub fn new(cfg: &BotConfig, reach: ReachModel, seed: Option<u64>) -> Self {
        BotState {
            skills: SkillBook::new(cfg.skills.clone()),
            summons: SummonTracker::default(),
            reach,
            graphs: GraphCache::default(),
            rng: seed.map_or_else(StdRng::from_os_rng, StdRng::seed_from_u64),
            viz: Viz::default(),
            last_rope_lift: f64::NEG_INFINITY,
            last_teleport: f64::NEG_INFINITY,
            hop_px: 14.0,
            travel_attack_at: f64::NEG_INFINITY,
            air_slack: None,
            attack_log: Default::default(),
            anchor_idx: 0,
            travel_target: None,
            bans: Default::default(),
            arrive_pending: Vec::new(),
            weave_dir: None,
            weave_bounds: None,
            roam_origin: None,
            pending_skills: None,
            map_version: 0,
            route: Vec::new(),
            checkpoint: None,
            session: Session::default(),
            log_throttle: Default::default(),
        }
    }

    pub fn for_config(cfg: &BotConfig) -> Self {
        BotState::new(cfg, ReachModel::new(base_reach(cfg), None), None)
    }
}

/// How a planned move ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Travel {
    Walk,
    Flash,
    Mixed,
}

pub trait Body {
    // -- Required: the world ---------------------------------------------------
    fn config(&self) -> &BotConfig;
    fn state(&mut self) -> &mut BotState;
    fn state_ref(&self) -> &BotState;
    fn keys(&mut self) -> &mut dyn Keys;
    /// A minimap capture, or None until the panel is located.
    fn frame(&mut self) -> Option<Image>;
    /// The player's feet in `img` (this body's own dot tracker).
    fn locate_player(&mut self, img: &Image) -> Option<(i32, i32)>;
    /// Why the bot should stop moving right now (loading, rune, other
    /// players), judged from `img`.
    fn hazard_in(&mut self, img: &Image) -> Option<String>;
    fn focused(&mut self) -> bool;
    /// Stop-aware sleep: true when woken by a stop.
    fn sleep(&mut self, secs: f64) -> bool;
    fn stopped(&self) -> bool;
    /// Monotonic seconds.
    fn now(&self) -> f64;
    fn log(&mut self, msg: &str);
    /// The current map (a fresh copy after dashboard edits), if resolved.
    fn map(&mut self) -> Option<Arc<MapEntry>>;
    /// Minimap region size (w, h) in px.
    fn region_wh(&self) -> Option<(f64, f64)>;

    // -- Optional hooks (the host overrides) -----------------------------------
    /// Bumped when the resolved map changes.
    fn map_version(&self) -> u64 {
        0
    }
    /// A settled feet position, for the platform-fit diagnostic.
    fn note_pos(&mut self, _pos: (f64, f64)) {}
    /// Persist a learned rope on the current map.
    fn save_map(&mut self, _entry: MapEntry) {}
    /// Anchor statistics: visit / miss / skip.
    fn stat(&mut self, _kind: &str, _anchor: &str, _why: &str) {}
    /// Detail appended to the current hazard's alert.
    fn hazard_note(&self) -> Option<String> {
        None
    }
    /// A high-priority alert (the host adds Telegram).
    fn notify(&mut self, msg: &str) {
        self.log(msg);
    }

    // -- Helpers -------------------------------------------------------------------
    fn should_continue(&self) -> bool {
        !self.stopped()
    }

    /// Log at most every `every` seconds per `key`.
    fn log_every(&mut self, key: &'static str, every: f64, msg: &str) {
        let now = self.now();
        let last = self
            .state()
            .log_throttle
            .get(key)
            .copied()
            .unwrap_or(f64::NEG_INFINITY);
        if now - last > every {
            self.state().log_throttle.insert(key, now);
            self.log(msg);
        }
    }

    fn rotation(&mut self) -> Rotation {
        match self.map() {
            Some(e) => e.rotation.clone(),
            None => self.config().rotation.clone(),
        }
    }

    fn rx(&self, v: f64) -> f64 {
        resolve_coord(v, self.region_wh().map_or(200.0, |w| w.0) as i64) as f64
    }

    fn ry(&self, v: f64) -> f64 {
        resolve_coord(v, self.region_wh().map_or(150.0, |w| w.1) as i64) as f64
    }

    /// Anchor positions in minimap px.
    fn anchors_px(&mut self) -> Vec<(f64, f64)> {
        let rot = self.rotation();
        rot.anchors
            .iter()
            .map(|a| (self.rx(a.x), self.ry(a.y)))
            .collect()
    }

    /// The drawn platforms of the current map, in minimap px.
    fn segments_px(&mut self) -> Vec<[f64; 4]> {
        let (Some(e), Some((w, h))) = (self.map(), self.region_wh()) else {
            return Vec::new();
        };
        e.platforms
            .iter()
            .flatten()
            .map(|s| [s[0] * w, s[1] * h, s[2] * w, s[3] * h])
            .collect()
    }

    fn graph_options(&self) -> GraphOptions {
        let cfg = self.config();
        GraphOptions {
            rope_penalty: cfg.rope_penalty,
            allow_flash: cfg.class_travel == ClassTravel::Flash && cfg.flash_jump_enabled,
            allow_double_flash: cfg.double_flash,
            walk_speed: self
                .state_ref()
                .reach
                .walk_stats()
                .map_or(GraphOptions::default().walk_speed, |w| w.speed),
            allow_teleport: cfg.class_travel == ClassTravel::Teleport && cfg.teleport_key.is_some(),
            ..Default::default()
        }
    }

    /// The movement graph of the current map's drawn platforms.
    fn graph(&mut self) -> Option<Arc<NavGraph>> {
        let (map, wh, opts) = (self.map(), self.region_wh(), self.graph_options());
        let st = self.state();
        st.graphs.get(map.as_deref(), wh, &st.reach, opts)
    }

    /// Capture and read the player's feet (updating the dashboard).
    fn pos(&mut self) -> Option<(f64, f64)> {
        let img = self.frame();
        let p = img
            .and_then(|img| self.locate_player(&img))
            .map(|(x, y)| (x as f64, y as f64));
        self.state().viz.player = p;
        if let Some(p) = p {
            self.note_pos(p);
        }
        p
    }

    fn hazard(&mut self) -> Option<String> {
        let img = self.frame()?;
        let h = self.hazard_in(&img);
        self.state().viz.hazard = h.clone();
        h
    }

    fn sleep_between(&mut self, mean: f64, lo: f64, hi: f64) -> bool {
        self.sleep(human_between(mean, lo, hi, 0.3))
    }

    // -- Attacks ---------------------------------------------------------------------
    fn use_skill(&mut self, skill: &Skill) -> bool {
        if !self.focused() || !self.should_continue() {
            return false;
        }
        if self.keys().press(&skill.key, skill.hold) {
            let now = self.now();
            self.state().skills.mark_used(&skill.name, now);
            if skill.kind.is_attack() {
                self.state().attack_log.push_back(now);
            }
            self.log(&format!("Skill: {}", skill.name));
            return true;
        }
        false
    }

    /// A ready attack that may be cast in `w`: skills on cooldown are
    /// preferred over spam, and `weight` sets the odds within the pool.
    fn pick_attack(&mut self, w: Window) -> Option<Skill> {
        let now = self.now();
        // A skill measured to shift the character further than there is
        // room for would put it off the platform: skip it in this window.
        let ground = w == Window::Ground;
        let fx: Vec<SkillEffect> = self
            .state()
            .reach
            .skill_effects()
            .into_iter()
            .filter(|e| e.on_ground == ground && e.dx.abs() >= MOVES_PX)
            .collect();
        let slack = if fx.is_empty() {
            None
        } else if ground {
            self.ground_slack()
        } else {
            self.state().air_slack
        };
        let effects = slack.map(|s| (s, fx));
        let st = self.state();
        let ready: Vec<Skill> = st
            .skills
            .ready_attacks(now)
            .into_iter()
            .filter(|s| w.allows(s.stance) && s.weight > 0.0)
            .filter(|s| match &effects {
                Some(((back, fwd), all)) => all
                    .iter()
                    .find(|e| e.skill == s.name)
                    .is_none_or(|e| e.fits(*back, *fwd)),
                None => true,
            })
            .collect();
        let with_cd: Vec<&Skill> = ready.iter().filter(|s| s.cooldown > 0.0).collect();
        let pool: Vec<&Skill> = if with_cd.is_empty() {
            ready.iter().collect()
        } else {
            with_cd
        };
        let total: f64 = pool.iter().map(|s| s.weight).sum();
        if pool.is_empty() {
            return None;
        }
        let mut roll = st.rng.random::<f64>() * total;
        for s in &pool {
            roll -= s.weight;
            if roll < 0.0 {
                return Some((*s).clone());
            }
        }
        pool.last().map(|s| (*s).clone())
    }

    /// Room to be shifted on the ground: the current leg's landing room
    /// when there is one, else (back and forward) the platform room around
    /// the character, less a margin.
    fn ground_slack(&mut self) -> Option<(f64, f64)> {
        const MARGIN: f64 = 6.0;
        if let Some(s) = self.state().air_slack {
            return Some(s);
        }
        let pos = self.pos()?;
        let g = self.graph()?;
        let p = g.platforms[g.locate(pos.0, pos.1)?];
        let room = ((pos.0 - p.x0).min(p.x1 - pos.0) - MARGIN).max(0.0);
        Some((room, room))
    }

    /// Attacks cast in the last minute.
    fn attack_rate(&mut self) -> f64 {
        let now = self.now();
        let log = &mut self.state().attack_log;
        while log.front().is_some_and(|t| now - *t > RATE_WINDOW_S) {
            log.pop_front();
        }
        log.len() as f64 * 60.0 / RATE_WINDOW_S
    }

    /// The odds a window fires: `base`, pushed up while the recent rate is
    /// under `target_attacks_per_min` and down while it is over.
    fn window_odds(&mut self, base: f64) -> f64 {
        let target = self.config().target_attacks_per_min;
        if target <= 0.0 {
            return base;
        }
        let deficit = (target - self.attack_rate()) / target;
        (base + deficit).clamp(0.0, 1.0)
    }

    /// One attack window in `w`: rolled against `base` (see `window_odds`;
    /// a ready skill tagged for only this window fires at least
    /// `EXCLUSIVE_READY_CHANCE` of the time), then 1 attack, or 2 with
    /// `weave_double_chance`. Returns how many landed.
    fn attack_window(&mut self, w: Window, base: f64) -> usize {
        let mut p = self.window_odds(base);
        let now = self.now();
        let exclusive = w.exclusive();
        if p < EXCLUSIVE_READY_CHANCE
            && self
                .state()
                .skills
                .ready_attacks(now)
                .iter()
                .any(|s| s.stance == exclusive && s.weight > 0.0)
        {
            p = EXCLUSIVE_READY_CHANCE;
        }
        if p < 1.0 && (p <= 0.0 || self.state().rng.random::<f64>() >= p) {
            return 0;
        }
        self.weave_attacks(w)
    }

    /// The attack window a landing carries (`ground_attack_chance`).
    fn ground_window(&mut self) -> usize {
        let base = self.config().ground_attack_chance;
        self.attack_window(Window::Ground, base)
    }

    /// 1 attack, or 2 with `weave_double_chance`; returns how many landed.
    fn weave_attacks(&mut self, w: Window) -> usize {
        let chance = self.config().weave_double_chance;
        let n = if self.state().rng.random::<f64>() < chance {
            2
        } else {
            1
        };
        let mut done = 0;
        for i in 0..n {
            let Some(skill) = self.pick_attack(w) else {
                break;
            };
            if i > 0 {
                self.sleep_between(0.13, 0.08, 0.22);
            }
            done += self.use_skill(&skill) as usize;
        }
        done
    }

    /// A move's attack window (`move_attack_chance`): once a flash has
    /// triggered, then the rest of the airtime. Classes that can't attack
    /// airborne attack after landing instead.
    fn after_flash(&mut self, airtime: f64) {
        let chance = self.config().move_attack_chance;
        if !self.config().air_attacks {
            self.sleep_between(airtime, airtime * 0.6, airtime * 1.6);
            self.attack_window(Window::Ground, chance);
            return;
        }
        self.sleep_between(0.09, 0.05, 0.15);
        let n = self.attack_window(Window::Air, chance);
        let rest = if n < 2 { airtime } else { airtime * 0.65 };
        self.sleep_between(rest, rest * 0.6, rest * 1.6);
    }

    // -- Movement primitives -------------------------------------------------------
    fn flash_key(&self) -> String {
        let cfg = self.config();
        cfg.flash_jump_key
            .clone()
            .unwrap_or_else(|| cfg.jump_key.clone())
    }

    /// Log-normal gap around `mean` for a mid-air re-press.
    fn repress(&self, mean: f64) -> f64 {
        human_between(mean, mean * 0.65, mean * 1.5, 0.3)
    }

    /// Sleep before a key that leads the next press: the key spacing will
    /// add a gap between the two, so take one out here.
    fn lead_sleep(&mut self, secs: f64) {
        self.sleep((secs - key_gap()).max(0.0));
    }

    /// One flash jump (the caller holds the direction), attacks woven in.
    fn flash_hop(&mut self) {
        let jk = self.flash_key();
        self.keys().press(&jk, None);
        let gap = self.repress(self.config().flash_repress_seconds);
        self.sleep(gap);
        self.keys().press(&jk, None);
        self.after_flash(0.34);
    }

    /// Two sideways flashes in one airtime (the caller holds the direction).
    fn double_flash(&mut self) {
        let jk = self.flash_key();
        self.keys().press(&jk, None);
        self.sleep_between(0.17, 0.11, 0.26);
        self.keys().press(&jk, None);
        let gap = self.repress(self.config().combo_repress_seconds);
        self.sleep(gap);
        self.keys().press(&jk, None);
        self.after_flash(0.33);
    }

    /// The up-flash re-press delay: inside the timing sweep's plateau, else
    /// a default window.
    fn up_rejump_delay(&mut self) -> f64 {
        let (lo, mid, hi) = self
            .state()
            .reach
            .plateau("up_flash", 1.0)
            .unwrap_or((0.16, 0.22, 0.30));
        human_between(mid, lo, hi, 0.15)
    }

    /// Jump, then Up + jump `delay` seconds after the first key-down. `mark`
    /// is told when each jump goes down ("jump", "rejump").
    fn timed_rejump(&mut self, delay: f64, mark: &mut dyn FnMut(&str)) {
        const UP_LEAD: f64 = 0.04;
        let jk = self.flash_key();
        self.keys().key_down(&jk);
        let t0 = self.now();
        mark("jump");
        self.sleep(human_hold(Some(&jk)).min((delay * 0.4).max(0.02)));
        self.keys().key_up(&jk);
        let wait = t0 + delay - UP_LEAD - self.now();
        self.sleep(wait.max(0.0));
        self.keys().key_down("up");
        let wait = t0 + delay - self.now();
        self.sleep(wait.max(0.0));
        self.keys().key_down(&jk);
        mark("rejump");
        self.sleep(human_hold(Some(&jk)));
        self.keys().key_up(&jk);
        self.keys().key_up("up");
    }

    /// The upward flash jump; a direction adds a diagonal drift. `delay`
    /// times the re-press (default: the measured sweet spot).
    fn up_flash_timed(&mut self, dir: Option<Dir>, delay: Option<f64>, mark: &mut dyn FnMut(&str)) {
        if let Some(d) = dir {
            self.keys().key_down(d.key());
        }
        let delay = delay.unwrap_or_else(|| self.up_rejump_delay());
        self.timed_rejump(delay, mark);
        self.after_flash(0.36);
        if let Some(d) = dir {
            self.keys().key_up(d.key());
        }
    }

    fn up_flash(&mut self, dir: Option<Dir>) {
        self.up_flash_timed(dir, None, &mut |_| {});
    }

    /// Up flash, then a sideways flash mid-air: up and over a gap.
    fn up_side_flash(&mut self, dir: Dir) {
        let jk = self.flash_key();
        self.keys().key_down(dir.key());
        self.keys().press(&jk, None);
        let gap = self.repress(self.config().flash_repress_seconds * 0.8);
        self.lead_sleep(gap);
        self.keys().key_down("up");
        self.keys().press(&jk, None);
        let gap = self.repress(self.config().combo_repress_seconds);
        self.sleep(gap);
        self.keys().key_up("up");
        self.keys().press(&jk, None);
        self.after_flash(0.33);
        self.keys().key_up(dir.key());
    }

    /// Seconds until the rope lift is usable (infinite when unbound).
    fn rope_lift_remaining(&self) -> f64 {
        let cfg = self.config();
        if cfg.up_jump_skill_key.is_none() {
            return f64::INFINITY;
        }
        (cfg.up_jump_skill_cooldown - (self.now() - self.state_ref().last_rope_lift)).max(0.0)
    }

    /// Press the rope lift; false when unbound, cooling or unfocused.
    fn rope_lift(&mut self) -> bool {
        if self.rope_lift_remaining() > 0.0 || !self.focused() {
            return false;
        }
        let now = self.now();
        self.state().last_rope_lift = now;
        let key = self.config().up_jump_skill_key.clone().unwrap_or_default();
        self.keys().press(&key, None);
        self.sleep_between(0.45, 0.35, 0.6);
        true
    }

    /// Vertical boost: rope lift when ready, else jump + Up + jump.
    fn up_jump(&mut self) -> bool {
        if !self.focused() {
            return false;
        }
        if self.rope_lift() {
            return true;
        }
        let jk = self.config().jump_key.clone();
        self.keys().press(&jk, None);
        self.sleep_between(0.1, 0.07, 0.14);
        self.keys().key_down("up");
        self.keys().key_down(&jk);
        self.sleep_between(0.5, 0.4, 0.62);
        self.keys().key_up(&jk);
        self.keys().key_up("up");
        self.sleep_between(0.3, 0.22, 0.45);
        true
    }

    fn teleport_remaining(&self) -> f64 {
        let cfg = self.config();
        if cfg.teleport_key.is_none() {
            return f64::INFINITY;
        }
        (cfg.teleport_cooldown - (self.now() - self.state_ref().last_teleport)).max(0.0)
    }

    /// Blink toward `dir`; false when unbound, cooling or unfocused.
    fn teleport(&mut self, dir: Option<Dir>) -> bool {
        if self.teleport_remaining() > 0.0 || !self.focused() {
            return false;
        }
        let now = self.now();
        self.state().last_teleport = now;
        let key = self.config().teleport_key.clone().unwrap_or_default();
        if let Some(d) = dir {
            self.keys().key_down(d.key());
        }
        self.keys().press(&key, None);
        self.sleep_between(0.3, 0.2, 0.45);
        if let Some(d) = dir {
            self.keys().key_up(d.key());
        }
        true
    }

    fn down_jump(&mut self) {
        if !self.focused() {
            return;
        }
        let jk = self.config().jump_key.clone();
        self.keys().key_down("down");
        self.keys().press(&jk, None);
        self.sleep_between(0.1, 0.07, 0.14);
        self.keys().key_up("down");
        self.sleep_between(0.4, 0.3, 0.55);
    }

    /// Leap off a rope: hold a direction and jump (there's no release key).
    fn rope_exit(&mut self, dir: Dir) {
        let jk = self.config().jump_key.clone();
        self.keys().key_down(dir.key());
        self.keys().press(&jk, None);
        self.sleep_between(0.55, 0.4, 0.75);
        self.keys().key_up(dir.key());
    }

    /// Hanging on a rope? Holding Down slides down a rope but only crouches
    /// on the ground (Down, not Up — Up on a portal changes maps). None
    /// when the dot can't be read.
    fn probe_rope(&mut self) -> Option<bool> {
        let before = self.pos()?;
        if !self.focused() {
            return None;
        }
        self.keys().key_down("down");
        self.sleep_between(0.35, 0.25, 0.5);
        self.keys().key_up("down");
        self.sleep_between(0.12, 0.08, 0.2);
        let after = self.pos()?;
        Some(after.1 - before.1 >= 2.0)
    }

    /// Watch the character on a rope until y crosses `target` (px). A grab
    /// (`grab`) latches when it rises and fails if it falls well past the
    /// target; a plain climb latches on any movement and only stalls once
    /// latched.
    fn follow_rope(&mut self, up: bool, target: f64, timeout: f64, grab: bool) -> bool {
        let what = if grab { "Rope grab" } else { "Climb" };
        let start = self.now();
        let (mut grabbed, mut last_y, mut still, mut lost) = (false, None::<f64>, 0, 0);
        while self.should_continue() && self.focused() && self.now() - start < timeout {
            let Some(img) = self.frame() else {
                self.sleep(0.3);
                continue;
            };
            if let Some(why) = self.hazard_in(&img) {
                self.log(&format!("{what} aborted: {why}"));
                return false;
            }
            let Some((_, y)) = self.locate_player(&img) else {
                lost += 1;
                if lost >= 10 {
                    self.log(&format!("{what} aborted: position lost"));
                    return false;
                }
                self.sleep(0.15);
                continue;
            };
            lost = 0;
            let y = y as f64;
            if (up && y <= target + 2.0) || (!up && y >= target - 2.0) {
                return true;
            }
            if grab {
                if let Some(ly) = last_y {
                    // 1px of detection jitter is neither progress nor a fall.
                    if y <= ly - 2.0 {
                        grabbed = true; // climbing (or the jump arc)
                        still = 0;
                    } else if (y - ly).abs() <= 1.0 {
                        still += 1; // latched and holding, or landed
                    } else {
                        still = 0; // falling: keep waiting
                        if grabbed && y > target + 30.0 {
                            self.log("Rope grab failed: fell off");
                            return false;
                        }
                    }
                }
                if last_y.is_none_or(|ly| (y - ly).abs() > 1.0) {
                    last_y = Some(y);
                }
            } else if last_y.is_some_and(|ly| (y - ly).abs() <= 1.0) {
                still += 1;
            } else {
                still = 0;
                grabbed = grabbed || last_y.is_some();
                last_y = Some(y);
            }
            if !grabbed && self.now() - start > 2.5 {
                self.log(&format!("{what} failed: never latched"));
                return false;
            }
            if still >= 10 && (grab || grabbed) {
                self.log(&format!("{what} failed: stalled"));
                return false;
            }
            self.sleep(0.15);
        }
        self.log(&format!("{what} failed: timed out"));
        false
    }

    /// Grab a rope on the move and climb to `until_y` (px): Up goes down
    /// well before takeoff, then a hop toward the rope (or a flash from
    /// further out) sweeps the character through its column. No attacks:
    /// one mid-air costs the grab.
    fn rope_up(&mut self, until_y: f64, dir: Option<Dir>, flash: bool) -> bool {
        let how = match (dir, flash) {
            (Some(d), true) => format!("flash {}", d.key()),
            (Some(d), false) => format!("hop {}", d.key()),
            (None, _) => "jump".to_owned(),
        };
        self.log(&format!("Rope grab ({how}) → y≈{until_y:.0}"));
        self.keys().key_down("up");
        self.sleep_between(0.18, 0.12, 0.28); // Up already held at the rope
        if let Some(d) = dir {
            self.keys().key_down(d.key());
            self.sleep_between(0.05, 0.03, 0.09);
        }
        let jk = self.config().jump_key.clone();
        self.keys().press(&jk, None);
        if flash && dir.is_some() {
            let gap = self.repress(self.config().flash_repress_seconds);
            self.sleep(gap);
            let fk = self.flash_key();
            self.keys().press(&fk, None);
        }
        self.sleep_between(0.12, 0.08, 0.18);
        let ok = self.follow_rope(true, until_y, 12.0, true);
        if ok {
            self.sleep_between(0.3, 0.2, 0.45); // the mount happens at the top
        }
        self.keys().key_up("up");
        if let Some(d) = dir {
            self.keys().key_up(d.key());
        }
        ok
    }

    /// Hold up/down on a rope until y crosses `until_y` (px), aligning to
    /// `x` (px) first.
    fn climb(&mut self, up: bool, until_y: f64, x: Option<f64>) -> bool {
        if let Some(x) = x {
            if let Some(p) = self.pos() {
                if !self.move_to_point(x, p.1, Some(2.0), Travel::Walk, false) {
                    return false;
                }
            }
        }
        let key = if up { "up" } else { "down" };
        self.log(&format!("Climb {key} → y≈{until_y:.0}"));
        self.keys().key_down(key);
        let ok = self.follow_rope(up, until_y, 10.0, false);
        self.keys().key_up(key);
        ok
    }

    /// One travel weave in the class's style: flash (jump → re-press →
    /// attacks), teleport (blink → attacks), or a plain walk weave.
    fn weave_move(&mut self, dir: Dir) {
        let cfg = self.config();
        if cfg.class_travel == ClassTravel::Teleport && cfg.teleport_key.is_some() {
            self.keys().key_down(dir.key());
            let key = self.config().teleport_key.clone().unwrap_or_default();
            self.keys().press(&key, None);
            let now = self.now();
            self.state().last_teleport = now;
            self.after_flash(0.3);
            self.keys().key_up(dir.key());
            return;
        }
        self.keys().key_down(dir.key());
        if self.config().flash_jump_enabled {
            self.flash_hop();
        } else {
            let chance = self.config().move_attack_chance;
            self.attack_window(Window::Ground, chance);
            self.sleep_between(0.4, 0.28, 0.6);
        }
        self.keys().key_up(dir.key());
    }

    /// A tap on `dir`: the key held exactly `secs`.
    fn tap(&mut self, dir: Dir, secs: f64) -> bool {
        self.keys().press(dir.key(), Some(secs))
    }

    /// Step to within `tol` px of `x` in calibrated taps: the longest that
    /// doesn't overshoot, re-reading the dot after each. False without a
    /// tap table, or when 8 taps don't get there.
    fn nudge_to(&mut self, x: f64, tol: f64) -> bool {
        let Some(table) = self.state_ref().reach.tap_table() else {
            return false;
        };
        let tol = tol.max(table.resolution() * 0.5);
        for _ in 0..8 {
            if !self.should_continue() || !self.focused() {
                return false;
            }
            let Some(pos) = super::measure::settle(self, 0.7) else {
                return false;
            };
            let need = x - pos.0;
            if need.abs() <= tol {
                return true;
            }
            let Some(row) = table.choose(need.abs()) else {
                return true;
            };
            self.tap(Dir::toward(need), row.secs);
        }
        false
    }

    /// Weave an attack into a walked leg, spaced ~0.4s.
    fn travel_attack(&mut self) {
        let now = self.now();
        if now < self.state().travel_attack_at {
            return;
        }
        if let Some(skill) = self.pick_attack(Window::Ground) {
            if self.use_skill(&skill) {
                let gap = human_between(0.43, 0.33, 0.6, 0.3);
                self.state().travel_attack_at = now + gap;
            }
        }
    }

    /// Walk (or flash-weave, then walk the last stretch) toward
    /// `(tx, ty)` px. `flat` treats the leg as horizontal — arrival on x
    /// alone, no vertical jumps. False on hazards, focus loss, stop, or
    /// when stuck.
    fn move_to_point(
        &mut self,
        tx: f64,
        ty: f64,
        threshold: Option<f64>,
        style: Travel,
        flat: bool,
    ) -> bool {
        let cfg = self.config().clone();
        let threshold = threshold.unwrap_or(cfg.nav_threshold_px as f64);
        let (stop_band, start_band) = (threshold * 0.75, threshold * 1.5);
        let mut flash_ok = style != Travel::Walk
            && match cfg.class_travel {
                ClassTravel::Flash => cfg.flash_jump_enabled,
                _ => cfg.teleport_key.is_some(),
            };
        let mut ty = ty;
        self.state().viz.target = Some((tx, ty));
        self.log(&format!("Navigating to ({tx:.0}, {ty:.0})"));
        let mut held: Option<Dir> = None;
        let (mut last_vert, mut escapes, mut snapped) = (f64::NEG_INFINITY, 0, false);
        let (mut vert_ref, mut vert_fails): (Option<f64>, u32) = (None, 0);
        let (mut stuck, mut last, mut hop_from): (i64, Option<(f64, f64)>, Option<f64>) =
            (0, None, None);

        let result = 'nav: loop {
            if !self.should_continue() || !self.focused() {
                break 'nav false;
            }
            let Some(img) = self.frame() else {
                self.sleep(0.5);
                continue;
            };
            if let Some(why) = self.hazard_in(&img) {
                self.log(&format!("Navigation aborted: {why}"));
                break 'nav false;
            }
            let Some(p) = self.locate_player(&img) else {
                self.sleep(0.5);
                continue;
            };
            let pos = (p.0 as f64, p.1 as f64);
            self.note_pos(pos);
            let (cx, cy) = pos;
            if let Some(from) = hop_from.take() {
                let moved = (cx - from).abs();
                if moved > 2.0 {
                    let hop = &mut self.state().hop_px;
                    *hop = 0.7 * *hop + 0.3 * moved;
                }
            }
            if !snapped && !flat {
                // A target recorded beneath the lowest platform is
                // unreachable: snap it onto the nearest drawn row.
                snapped = true;
                if let Some(py) = platform_row_at(&self.segments_px(), tx, ty, 8.0) {
                    if py as f64 != ty {
                        ty = py as f64;
                        self.state().viz.target = Some((tx, ty));
                    }
                }
            }
            let dx = tx - cx;
            let dy = if flat { 0.0 } else { ty - cy };
            if dx.abs() <= threshold && dy.abs() <= threshold {
                self.log("Navigation target reached");
                break 'nav true;
            }
            let hop = self.state().hop_px;
            if flash_ok {
                let span = platform_span_at(&self.segments_px(), cx, cy, 6.0);
                let room = span.is_none_or(|(x0, x1)| {
                    if dx > 0.0 {
                        x1 as f64 - cx > hop
                    } else {
                        cx - x0 as f64 > hop
                    }
                });
                if room && dx.abs() > hop {
                    set_dir(self, &mut held, None, false);
                    hop_from = Some(cx);
                    self.weave_move(Dir::toward(dx));
                    stuck = if last == Some(pos) { stuck + 1 } else { 0 };
                    last = Some(pos);
                    if stuck >= 4 {
                        self.log("Flash hops aren't moving — walking instead");
                        flash_ok = false;
                        stuck = 0;
                    }
                    continue;
                }
                if dx.abs() > cfg.walk_band_px {
                    // One hop away: a plain jump closes it without the
                    // overshoot that ping-pongs over the target.
                    set_dir(self, &mut held, None, false);
                    let d = Dir::toward(dx);
                    self.keys().key_down(d.key());
                    let jk = cfg.jump_key.clone();
                    self.keys().press(&jk, None);
                    self.after_flash(0.4);
                    self.keys().key_up(d.key());
                    stuck = if last == Some(pos) { stuck + 1 } else { 0 };
                    last = Some(pos);
                    if stuck >= 4 {
                        self.log("Approach hops aren't moving — walking instead");
                        flash_ok = false;
                        stuck = 0;
                    }
                    continue;
                }
            } else {
                self.travel_attack();
            }
            if (held == Some(Dir::Right) && dx <= stop_band)
                || (held == Some(Dir::Left) && dx >= -stop_band)
            {
                set_dir(self, &mut held, None, true);
            }
            if held.is_none() {
                if dx > threshold {
                    set_dir(self, &mut held, Some(Dir::Right), false);
                } else if dx < -threshold {
                    set_dir(self, &mut held, Some(Dir::Left), false);
                }
            }
            if dx.abs() <= threshold * 3.0 {
                let now = self.now();
                if dy < -threshold && now - last_vert >= cfg.vert_jump_interval {
                    vert_fails = if vert_ref.is_some_and(|r| cy >= r - 1.0) {
                        vert_fails + 1
                    } else {
                        0
                    };
                    if vert_fails >= 2 {
                        break 'nav vert_stuck(self, dx, start_band, "ascend");
                    }
                    if !self.up_jump() {
                        self.sleep(0.05);
                        continue;
                    }
                    vert_ref = Some(cy);
                    last_vert = now;
                } else if dy > threshold && now - last_vert >= cfg.vert_jump_interval {
                    vert_fails = if vert_ref.is_some_and(|r| cy <= r + 1.0) {
                        vert_fails + 1
                    } else {
                        0
                    };
                    vert_ref = Some(cy);
                    last_vert = now;
                    if vert_fails >= 2 {
                        break 'nav vert_stuck(self, dx, start_band, "descend");
                    }
                    self.down_jump();
                } else {
                    self.sleep(0.05);
                }
            } else {
                self.sleep(0.05);
            }
            stuck = if last == Some(pos) { stuck + 1 } else { 0 };
            last = Some(pos);
            if stuck >= cfg.nav_stuck_limit {
                escapes += 1;
                if escapes >= 2 {
                    self.log("Stuck twice — abandoning leg");
                    break 'nav false;
                }
                self.log("Stuck — sidestepping");
                set_dir(self, &mut held, None, false);
                let back = Dir::toward(dx).flip();
                self.keys().key_down(back.key());
                self.sleep_between(0.4, 0.28, 0.6);
                self.keys().key_up(back.key());
                self.up_jump();
                stuck = 0;
                last = None;
            }
        };
        set_dir(self, &mut held, None, false);
        self.keys().release_all();
        self.state().viz.target = None;
        result
    }
}

/// Change the held walking direction (hysteresis helper for
/// `move_to_point`); `lag` adds the human delay in letting go.
fn set_dir<B: Body + ?Sized>(body: &mut B, held: &mut Option<Dir>, new: Option<Dir>, lag: bool) {
    if *held == new {
        return;
    }
    if let Some(old) = *held {
        if lag {
            body.sleep(release_lag());
        }
        body.keys().key_up(old.key());
    }
    if let Some(d) = new {
        body.keys().key_down(d.key());
    }
    *held = new;
}

/// Vertical jumps stopped making progress: accept the position if already
/// aligned horizontally, else abort the leg.
fn vert_stuck<B: Body + ?Sized>(body: &mut B, dx: f64, start_band: f64, verb: &str) -> bool {
    if dx.abs() <= start_band {
        body.log(&format!(
            "Vertically blocked ({verb}) — horizontally aligned, accepting position"
        ));
        return true;
    }
    body.log(&format!("Vertically blocked ({verb}) — aborting leg"));
    false
}
