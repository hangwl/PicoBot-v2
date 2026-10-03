//! A physics stand-in for the game, shared by the bot tests (a port of
//! the Python suite's `SimBot`): every airborne move is (sideways distance,
//! max rise); the character lands on the highest platform it can reach at
//! the landing column, else falls. Holding a direction walks 4px per sleep
//! tick. The clock only advances when the bot sleeps.

#![allow(dead_code, clippy::field_reassign_with_default)]

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use picobot_core::bot::{Body, BotState, Dir, Keys, Travel};
use picobot_core::config::BotConfig;
use picobot_core::maps::MapEntry;
use picobot_core::navgraph::{GraphOptions, NavGraph};
use picobot_core::reach::{base_reach, Move, Reach, ReachModel};
use picobot_core::rotation::Anchor;
use picobot_core::vision::Image;

pub const FLOOR: [f64; 4] = [0.0, 100.0, 200.0, 100.0];
pub const MID: [f64; 4] = [40.0, 84.0, 120.0, 84.0];
pub const TOP: [f64; 4] = [60.0, 66.0, 100.0, 66.0];
pub const SIDE: [f64; 4] = [135.0, 84.0, 190.0, 84.0];

/// The Python suite's reach (explore 1.0), with overrides.
pub fn reach_with(over: &[(Move, Reach)]) -> ReachModel {
    let mut base = base_reach(&BotConfig::default());
    let r = |dx, rise| Reach { dx, rise };
    for (m, v) in [
        (Move::Jump, r(8.0, 4.0)),
        (Move::Flash, r(25.0, 4.0)),
        (Move::DoubleFlash, r(40.0, 4.0)),
        (Move::UpFlash, r(6.0, 20.0)),
        (Move::UpSideFlash, r(25.0, 16.0)),
        (Move::RopeLift, r(3.0, 30.0)),
        (Move::Teleport, r(25.0, 12.0)),
    ]
    .into_iter()
    .chain(over.iter().copied())
    {
        base[m as usize] = v;
    }
    let mut m = ReachModel::new(base, None);
    m.explore = 1.0;
    m
}

pub struct Sim {
    pub cfg: BotConfig,
    pub state: BotState,
    pub plats: Vec<[f64; 4]>,
    pub ropes: Vec<[f64; 4]>,
    pub pos: (f64, f64),
    /// Positions to report instead of the physics, one per read (the last
    /// repeats).
    pub script: VecDeque<(f64, f64)>,
    pub held: Option<Dir>,
    pub moves: Vec<String>,
    pub downs: Vec<String>,
    pub ups: Vec<String>,
    pub presses: Vec<String>,
    pub logs: Vec<String>,
    pub clock: f64,
    pub slept: f64,
    // Physics
    pub flash: f64,
    pub double: f64,
    pub up: f64,
    pub side: f64,
    pub rope: f64,
    pub rope_cd: f64,
    pub teleport_cd: f64,
    /// The first N airborne moves go nowhere.
    pub fizzle: u32,
    /// The mid-air point of the last airborne move, read once before the
    /// landing (a real dot is seen in flight).
    pub apex: Option<(f64, f64)>,
    pub fail_rope: bool,
    pub opts: GraphOptions,
    // Patrol surface
    pub entry: Option<Arc<MapEntry>>,
    /// (kind, anchor, why) anchor statistics.
    pub stats: Vec<(String, String, String)>,
    pub saves: usize,
    /// What a Down probe reads (None: unreadable).
    pub probe: Option<bool>,
    /// The dot can't be seen.
    pub hidden: bool,
    pub focus: bool,
    pub hazard: Option<String>,
    /// The run stops once the clock reaches this.
    pub stop_at: f64,
    /// Teleport carry: sideways, and straight up with Up held.
    pub tele: (f64, f64),
    pub up_held: bool,
    /// Px walked per sleep tick while a direction is held.
    pub walk: f64,
    /// Px per second carried by a timed tap, and the way the character
    /// faces (a tap in a new direction only turns it).
    pub tap_speed: f64,
    pub facing: Option<Dir>,
    /// Skill key -> px it shifts a flash's landing (toward the jump) when
    /// cast with a direction held.
    pub skill_fx: std::collections::HashMap<String, f64>,
    /// Skill key -> px it moves a standing character, the way it faces.
    pub ground_fx: std::collections::HashMap<String, f64>,
    /// Every takeoff from standing: "jump", or the up flash's re-press
    /// delay ("none" for the patrol's own timing) — shared with a test
    /// recorder.
    pub flights: Arc<Mutex<Vec<String>>>,
    /// Stops the run when set (from any thread or callback).
    pub stop: Arc<AtomicBool>,
    /// A measurement stops when it reaches this move.
    pub stop_on: Option<String>,
    /// Off-platform reports: (position, what the patrol knew).
    pub off: Vec<((f64, f64), serde_json::Value)>,
    /// A rune's box on the minimap; the player's glyph hides it.
    pub rune: Option<(i32, i32, i32, i32)>,
    /// Rune milestones reported: (event, info).
    pub rune_events: Vec<(String, serde_json::Value)>,
    /// The rune puzzle the next activation shows; while open, the index of
    /// the arrow expected next. A wrong key (or a planted failure) closes
    /// it and locks the rune until `puzzle_lock`.
    pub puzzle: [&'static str; 4],
    pub puzzle_at: Option<usize>,
    pub puzzle_lock: f64,
    /// Attempts that fail whatever is pressed.
    pub puzzle_fail: u32,
    /// The arrows never show (an unreadable puzzle).
    pub puzzle_hidden: bool,
    pub activations: u32,
    /// Every press with the clock at the time.
    pub pressed_at: Vec<(f64, String)>,
}

impl Sim {
    pub fn new(plats: &[[f64; 4]], pos: (f64, f64)) -> Self {
        let mut cfg = BotConfig::default();
        cfg.nav_threshold_px = 4;
        cfg.jump_key = "space".into();
        cfg.walk_band_px = 8.0;
        cfg.up_jump_skill_key = Some("r".into());
        cfg.skills = Vec::new();
        cfg.patrol_mode = picobot_core::config::PatrolMode::Anchors;
        let state = BotState::new(&cfg, reach_with(&[]), Some(0));
        Sim {
            cfg,
            state,
            plats: plats.to_vec(),
            ropes: Vec::new(),
            pos,
            script: VecDeque::new(),
            held: None,
            moves: Vec::new(),
            downs: Vec::new(),
            ups: Vec::new(),
            presses: Vec::new(),
            logs: Vec::new(),
            clock: 0.0,
            slept: 0.0,
            flash: 25.0,
            double: 40.0,
            up: 20.0,
            side: 25.0,
            rope: 30.0,
            rope_cd: 0.0,
            teleport_cd: 0.0,
            fizzle: 0,
            apex: None,
            fail_rope: false,
            opts: GraphOptions::default(),
            entry: None,
            stats: Vec::new(),
            saves: 0,
            probe: None,
            hidden: false,
            focus: true,
            hazard: None,
            stop_at: f64::INFINITY,
            tele: (25.0, 6.0),
            up_held: false,
            walk: 4.0,
            tap_speed: 50.0,
            facing: None,
            skill_fx: Default::default(),
            ground_fx: Default::default(),
            flights: Arc::default(),
            stop: Arc::default(),
            stop_on: None,
            off: Vec::new(),
            rune: None,
            rune_events: Vec::new(),
            puzzle: ["up", "down", "left", "right"],
            puzzle_at: None,
            puzzle_lock: 0.0,
            puzzle_fail: 0,
            puzzle_hidden: false,
            activations: 0,
            pressed_at: Vec::new(),
        }
    }

    /// A sim on map "m" (region 200x150) with the platforms drawn and
    /// anchors a0, a1, … at the given px.
    pub fn patrol(plats: &[[f64; 4]], pos: (f64, f64), anchors: &[(f64, f64)]) -> Self {
        let mut s = Sim::new(plats, pos);
        let mut e = MapEntry::new("m");
        let norm = |p: &[f64; 4]| [p[0] / 200.0, p[1] / 150.0, p[2] / 200.0, p[3] / 150.0];
        e.platforms = Some(plats.iter().map(norm).collect());
        e.rotation.anchors = anchors
            .iter()
            .enumerate()
            .map(|(i, (x, y))| Anchor::new(&format!("a{i}"), x / 200.0, y / 150.0))
            .collect();
        s.entry = Some(Arc::new(e));
        s
    }

    pub fn edit_map(&mut self, f: impl FnOnce(&mut MapEntry)) {
        let mut e = (**self.entry.as_ref().unwrap()).clone();
        f(&mut e);
        self.entry = Some(Arc::new(e));
    }

    pub fn count_logs(&self, prefix: &str) -> usize {
        self.logs.iter().filter(|l| l.starts_with(prefix)).count()
    }

    pub fn stat_count(&self, kind: &str, anchor: &str) -> usize {
        self.stats
            .iter()
            .filter(|s| s.0 == kind && s.1 == anchor)
            .count()
    }

    pub fn with_reach(mut self, reach: ReachModel) -> Self {
        self.state.reach = reach;
        self
    }

    /// The graph the navigator plans on (built from the sim's reach).
    pub fn graph(&self) -> Arc<NavGraph> {
        Arc::new(NavGraph::new(
            &self.plats,
            &self.ropes,
            &self.state.reach,
            self.opts,
        ))
    }

    fn physics(&self) -> NavGraph {
        NavGraph::new(&self.plats, &[], &self.state.reach, GraphOptions::default())
    }

    pub fn sign(&self) -> f64 {
        if self.held == Some(Dir::Right) {
            1.0
        } else {
            -1.0
        }
    }

    /// Land on the highest platform the move reaches at the landing column.
    pub fn air(&mut self, dx: f64, rise: f64) {
        if self.fizzle > 0 && rise > 4.0 {
            self.fizzle -= 1;
            return;
        }
        let (x, y) = (self.pos.0 + dx, self.pos.1);
        if rise > 0.0 {
            self.apex = Some((self.pos.0 + dx / 2.0, y - rise));
        }
        let g = self.physics();
        let best = g
            .platforms
            .iter()
            .filter(|p| p.spans(x, 0.0) && p.y_at(x) >= y - rise - 0.01)
            .map(|p| p.y_at(x))
            .fold(None, |b: Option<f64>, v| Some(b.map_or(v, |b| b.min(v))));
        self.pos = (x, best.unwrap_or(150.0));
    }

    fn up_flash_as(&mut self, dir: Option<Dir>, delay: Option<f64>) {
        self.moves.push("up_flash".into());
        let tag = delay.map_or("none".into(), |d| format!("{d}"));
        self.flights.lock().unwrap().push(tag);
        let dx = match dir {
            Some(Dir::Right) => 6.0,
            Some(Dir::Left) => -6.0,
            None => 0.0,
        };
        let up = self.up;
        self.air(dx, up);
    }

    /// Time passes in the air (without walking the held direction).
    pub fn airtime(&mut self, secs: f64) {
        self.clock += secs;
        self.slept += secs;
        self.rope_cd = (self.rope_cd - secs).max(0.0);
        self.teleport_cd = (self.teleport_cd - secs).max(0.0);
    }

    /// The player's glyph overlaps the rune.
    pub fn on_rune(&self) -> bool {
        let Some(r) = self.rune else { return false };
        let (x, y) = (self.pos.0.round() as i32, self.pos.1.round() as i32);
        x - 3 <= r.2 && r.0 <= x + 2 && y - 5 <= r.3 && r.1 <= y
    }

    /// The rune key and the puzzle's arrows; true when `key` was one.
    fn rune_key_press(&mut self, key: &str) -> bool {
        if key == self.cfg.rune_key && self.rune.is_some() {
            if self.puzzle_at.is_none() && self.on_rune() && self.clock >= self.puzzle_lock {
                self.puzzle_at = Some(0);
                self.activations += 1;
            }
            return true;
        }
        let Some(i) = self.puzzle_at else {
            return false;
        };
        if !["up", "down", "left", "right"].contains(&key) {
            return false;
        }
        if self.puzzle_fail > 0 || key != self.puzzle[i] {
            self.puzzle_fail = self.puzzle_fail.saturating_sub(1);
            self.puzzle_at = None;
            self.puzzle_lock = self.clock + 3.0;
        } else if i == 3 {
            self.puzzle_at = None;
            self.rune = None; // solved
        } else {
            self.puzzle_at = Some(i + 1);
        }
        true
    }

    pub fn log_has(&self, needle: &str) -> bool {
        self.logs.iter().any(|l| l.contains(needle))
    }
}

impl Keys for Sim {
    fn key_down(&mut self, key: &str) -> bool {
        self.downs.push(key.into());
        match key {
            "left" => self.held = Some(Dir::Left),
            "right" => self.held = Some(Dir::Right),
            "up" => self.up_held = true,
            _ => {}
        }
        true
    }

    fn key_up(&mut self, key: &str) -> bool {
        self.ups.push(key.into());
        if key == "left" || key == "right" {
            self.held = None;
        }
        if key == "up" {
            self.up_held = false;
        }
        true
    }

    fn press(&mut self, key: &str, hold: Option<f64>) -> bool {
        self.presses.push(key.into());
        self.pressed_at.push((self.clock, key.into()));
        if self.rune_key_press(key) {
            return true;
        }
        if let (Some(dx), Some(_)) = (self.skill_fx.get(key).copied(), self.held) {
            let s = self.sign();
            self.air(s * dx, 0.0);
            return true;
        }
        if let (Some(dx), None, Some(face)) =
            (self.ground_fx.get(key).copied(), self.held, self.facing)
        {
            let sign = if face == Dir::Right { 1.0 } else { -1.0 };
            let g = self.physics();
            if let Some(i) = g.locate(self.pos.0, self.pos.1) {
                let p = g.platforms[i];
                self.pos.0 = (self.pos.0 + sign * dx).clamp(p.x0, p.x1);
            }
            return true;
        }
        if let (Some(h), "left" | "right") = (hold, key) {
            let dir = if key == "right" {
                Dir::Right
            } else {
                Dir::Left
            };
            self.clock += h;
            self.slept += h;
            if self.facing == Some(dir) {
                let sign = if dir == Dir::Right { 1.0 } else { -1.0 };
                let g = self.physics();
                if let Some(i) = g.locate(self.pos.0, self.pos.1) {
                    let p = g.platforms[i];
                    self.pos.0 = (self.pos.0 + sign * self.tap_speed * h).clamp(p.x0, p.x1);
                }
            }
            self.facing = Some(dir);
            return true;
        }
        if key == "space" && self.held.is_none() {
            self.flights.lock().unwrap().push("jump".into());
        }
        if key == "space" && self.held.is_some() {
            let s = self.sign();
            self.air(s * 8.0, 4.0);
        }
        true
    }

    fn release_all(&mut self) {
        self.held = None;
    }
}

impl Body for Sim {
    fn config(&self) -> &BotConfig {
        &self.cfg
    }
    fn state(&mut self) -> &mut BotState {
        &mut self.state
    }
    fn state_ref(&self) -> &BotState {
        &self.state
    }
    fn keys(&mut self) -> &mut dyn Keys {
        self
    }
    fn frame(&mut self) -> Option<Image> {
        Some(Image::new(1, 1))
    }
    fn locate_player(&mut self, _img: &Image) -> Option<(i32, i32)> {
        if self.hidden {
            return None;
        }
        if let (true, Some(a)) = (self.script.is_empty(), self.apex.take()) {
            return Some((a.0.round() as i32, a.1.round() as i32));
        }
        let p = if self.script.len() > 1 {
            self.script.pop_front().unwrap()
        } else if let Some(p) = self.script.front() {
            *p
        } else {
            self.pos
        };
        self.pos = p;
        Some((p.0.round() as i32, p.1.round() as i32))
    }
    fn hazard_in(&mut self, _img: &Image) -> Option<String> {
        self.hazard.clone()
    }
    fn focused(&mut self) -> bool {
        self.focus
    }
    fn sleep(&mut self, secs: f64) -> bool {
        self.slept += secs;
        self.clock += secs;
        self.rope_cd = (self.rope_cd - secs).max(0.0);
        self.teleport_cd = (self.teleport_cd - secs).max(0.0);
        if self.held.is_some() && self.script.is_empty() {
            let dx = self.sign() * self.walk;
            self.air(dx, 0.0);
        }
        false
    }
    fn stopped(&self) -> bool {
        self.clock >= self.stop_at || self.stop.load(Ordering::SeqCst)
    }
    fn now(&self) -> f64 {
        self.clock
    }
    fn log(&mut self, msg: &str) {
        self.logs.push(msg.into());
    }
    fn map(&mut self) -> Option<Arc<MapEntry>> {
        self.entry.clone()
    }
    fn save_map(&mut self, entry: MapEntry) {
        self.saves += 1;
        self.entry = Some(Arc::new(entry));
    }
    fn stat(&mut self, kind: &str, anchor: &str, why: &str) {
        self.stats.push((kind.into(), anchor.into(), why.into()));
    }
    fn off_platform(&mut self, pos: (f64, f64), info: serde_json::Value) {
        self.off.push((pos, info));
    }
    fn window_frame(&mut self) -> Option<Image> {
        let mut img = Image::new(1366, 768);
        if self.puzzle_at == Some(0) && !self.puzzle_hidden {
            for (n, to) in self.puzzle.iter().enumerate() {
                paint_arrow(&mut img, (520 + n as i64 * 100, 210), to);
            }
        }
        Some(img)
    }
    fn locate_rune(&mut self, _img: &Image) -> Option<(i32, i32, i32, i32)> {
        let r = self.rune?;
        let (x, y) = (self.pos.0.round() as i32, self.pos.1.round() as i32);
        let covered = x - 3 <= r.2 && r.0 <= x + 2 && y - 5 <= r.3 && r.1 <= y;
        (!covered).then_some(r)
    }
    fn rune_event(&mut self, event: &str, info: serde_json::Value) {
        self.rune_events.push((event.into(), info));
    }
    fn probe_rope(&mut self) -> Option<bool> {
        self.probe
    }
    fn region_wh(&self) -> Option<(f64, f64)> {
        Some((200.0, 150.0))
    }
    fn segments_px(&mut self) -> Vec<[f64; 4]> {
        self.plats.clone()
    }

    // Instant physics for the primitives, as the Python SimBot does.
    fn move_to_point(
        &mut self,
        tx: f64,
        _ty: f64,
        _t: Option<f64>,
        _s: Travel,
        _flat: bool,
    ) -> bool {
        let g = self.physics();
        if let Some(i) = g.locate(self.pos.0, self.pos.1) {
            let p = g.platforms[i];
            self.pos = (tx.clamp(p.x0, p.x1), self.pos.1);
        }
        true
    }
    fn rope_lift_remaining(&self) -> f64 {
        if self.rope > 0.0 {
            self.rope_cd
        } else {
            f64::INFINITY
        }
    }
    fn rope_lift(&mut self) -> bool {
        if self.rope_cd > 0.0 || self.rope <= 0.0 {
            return false;
        }
        self.moves.push("rope_lift".into());
        if self.script.is_empty() {
            let r = self.rope;
            self.air(0.0, r);
        }
        self.rope_cd = 3.0;
        true
    }
    fn teleport_remaining(&self) -> f64 {
        self.teleport_cd
    }
    fn teleport(&mut self, dir: Option<Dir>) -> bool {
        if self.teleport_cd > 0.0 {
            return false;
        }
        let (dx, up) = self.tele;
        let (dx, rise) = match dir {
            Some(Dir::Right) => (dx, 6.0),
            Some(Dir::Left) => (-dx, 6.0),
            None if self.up_held => (0.0, up),
            None => (0.0, 6.0),
        };
        self.moves.push(match dir {
            Some(d) => format!("teleport:{}", d.key()),
            None if self.up_held => "teleport:up".into(),
            None => "teleport".into(),
        });
        self.air(dx, rise);
        true
    }
    fn up_flash_timed(&mut self, dir: Option<Dir>, delay: Option<f64>, mark: &mut dyn FnMut(&str)) {
        mark("jump");
        mark("rejump");
        self.up_flash_as(dir, delay);
    }
    fn up_flash(&mut self, dir: Option<Dir>) {
        self.up_flash_as(dir, None);
    }

    fn up_side_flash(&mut self, dir: Dir) {
        self.moves.push("up_side_flash".into());
        let dx = if dir == Dir::Right {
            self.side
        } else {
            -self.side
        };
        let up = self.up * 0.8;
        self.air(dx, up);
    }
    fn flash_hop(&mut self) {
        self.moves.push("flash".into());
        let dx = self.sign() * self.flash;
        self.air(dx, 4.0);
        self.airtime(0.5);
    }
    fn double_flash(&mut self) {
        self.moves.push("double_flash".into());
        let dx = self.sign() * self.double;
        self.air(dx, 4.0);
        self.airtime(0.5);
    }
    fn down_jump(&mut self) {
        let g = self.physics();
        if let Some(i) = g.below(self.pos.0, self.pos.1, None) {
            self.pos = (self.pos.0, g.platforms[i].y_at(self.pos.0));
        }
    }
    fn rope_up(&mut self, until_y: f64, dir: Option<Dir>, flash: bool) -> bool {
        self.moves.push(if flash {
            "rope_up+flash".into()
        } else {
            "rope_up".into()
        });
        if self.fail_rope {
            return false;
        }
        if let Some(d) = dir {
            self.pos.0 += if d == Dir::Right { 6.0 } else { -6.0 };
        }
        let g = self.physics();
        for p in &g.platforms {
            if p.spans(self.pos.0, 0.0) && (p.y_at(self.pos.0) - until_y).abs() <= 2.0 {
                self.pos = (self.pos.0, p.y_at(self.pos.0));
                return true;
            }
        }
        false
    }
    fn rope_exit(&mut self, dir: Dir) {
        self.moves.push("rope_exit".into());
        self.pos = (
            self.pos.0 + if dir == Dir::Right { 10.0 } else { -10.0 },
            self.pos.1 + 20.0,
        );
    }
    fn climb(&mut self, up: bool, until_y: f64, x: Option<f64>) -> bool {
        let g = self.physics();
        if let (Some(x), Some(i)) = (x, g.locate(self.pos.0, self.pos.1)) {
            let p = g.platforms[i];
            self.pos = (x.clamp(p.x0, p.x1), self.pos.1);
        }
        for p in &g.platforms {
            if p.spans(self.pos.0, 0.0) && (p.y_at(self.pos.0) - until_y).abs() <= 2.0 {
                self.pos = (self.pos.0, p.y_at(self.pos.0));
                self.moves.push(if up {
                    "climb_up".into()
                } else {
                    "climb_down".into()
                });
                return true;
            }
        }
        false
    }
}

/// A solid arrow at `c` pointing `to`, shaded green (tail) to orange (tip)
/// like the game's rune arrows.
pub fn paint_arrow(img: &mut Image, c: (i64, i64), to: &str) {
    for i in 0..24i64 {
        let t = i as f64 / 23.0;
        let bgr = [0u8, (255.0 - 170.0 * t) as u8, (80.0 + 175.0 * t) as u8];
        for j in 0..12i64 {
            let (a, b) = (i - 12, j - 6);
            let (x, y) = match to {
                "right" => (a, b),
                "left" => (-a, b),
                "down" => (b, a),
                _ => (b, -a),
            };
            img.set_bgr((c.0 + x) as usize, (c.1 + y) as usize, bgr);
        }
    }
}
