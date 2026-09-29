//! The movement primitives' real key sequences (the default `Body`
//! methods, nothing overridden): what the Pico actually receives.

#![allow(clippy::field_reassign_with_default)]

use std::collections::VecDeque;
use std::sync::Arc;

use picobot_core::bot::{Body, BotState, Dir, Keys, Travel};
use picobot_core::config::{BotConfig, ClassTravel};
use picobot_core::maps::MapEntry;
use picobot_core::skills::Skill;
use picobot_core::vision::Image;

/// Records key events; a held arrow walks 2px per sleep tick; `ys` scripts
/// the reported y (e.g. a rope climb).
struct KeyBody {
    cfg: BotConfig,
    state: BotState,
    events: Vec<String>,
    /// The clock at each event.
    times: Vec<f64>,
    pos: (f64, f64),
    held: Option<Dir>,
    ys: VecDeque<f64>,
    clock: f64,
}

impl KeyBody {
    fn new() -> Self {
        let mut cfg = BotConfig::default();
        cfg.jump_key = "space".into();
        cfg.weave_double_chance = 0.0;
        cfg.skills = vec![Skill::new("main", "a")];
        cfg.up_jump_skill_key = Some("r".into());
        let state = BotState::new(
            &cfg,
            picobot_core::reach::ReachModel::new(picobot_core::reach::base_reach(&cfg), None),
            Some(1),
        );
        KeyBody {
            cfg,
            state,
            events: Vec::new(),
            times: Vec::new(),
            pos: (50.0, 100.0),
            held: None,
            ys: VecDeque::new(),
            clock: 0.0,
        }
    }

    /// Only the key events, as "down:x" / "up:x" / "press:x".
    fn seq(&self) -> Vec<&str> {
        self.events.iter().map(String::as_str).collect()
    }

    fn presses(&self) -> Vec<&str> {
        self.events
            .iter()
            .filter_map(|e| e.strip_prefix("press:"))
            .collect()
    }
}

impl Keys for KeyBody {
    fn key_down(&mut self, key: &str) -> bool {
        self.events.push(format!("down:{key}"));
        self.times.push(self.clock);
        self.held = match key {
            "left" => Some(Dir::Left),
            "right" => Some(Dir::Right),
            _ => self.held,
        };
        true
    }
    fn key_up(&mut self, key: &str) -> bool {
        self.events.push(format!("up:{key}"));
        self.times.push(self.clock);
        if key == "left" || key == "right" {
            self.held = None;
        }
        true
    }
    fn press(&mut self, key: &str, _hold: Option<f64>) -> bool {
        self.events.push(format!("press:{key}"));
        self.times.push(self.clock);
        true
    }
    fn release_all(&mut self) {
        self.held = None;
    }
}

impl Body for KeyBody {
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
        if let Some(y) = self.ys.pop_front() {
            self.pos.1 = y;
        }
        Some((self.pos.0.round() as i32, self.pos.1.round() as i32))
    }
    fn hazard_in(&mut self, _img: &Image) -> Option<String> {
        None
    }
    fn focused(&mut self) -> bool {
        true
    }
    fn sleep(&mut self, secs: f64) -> bool {
        self.clock += secs;
        match self.held {
            Some(Dir::Right) => self.pos.0 += 2.0,
            Some(Dir::Left) => self.pos.0 -= 2.0,
            None => {}
        }
        false
    }
    fn stopped(&self) -> bool {
        false
    }
    fn now(&self) -> f64 {
        self.clock
    }
    fn log(&mut self, _msg: &str) {}
    fn map(&mut self) -> Option<Arc<MapEntry>> {
        None
    }
    fn region_wh(&self) -> Option<(f64, f64)> {
        Some((200.0, 150.0))
    }
}

#[test]
fn every_flash_move_attacks_after_its_last_jump() {
    type Case = (&'static str, fn(&mut KeyBody), usize);
    let cases: [Case; 3] = [
        ("flash_hop", |b| b.flash_hop(), 2),
        ("double_flash", |b| b.double_flash(), 3),
        ("up_side_flash", |b| b.up_side_flash(Dir::Right), 3),
    ];
    for (name, f, jumps) in cases {
        let mut b = KeyBody::new();
        f(&mut b);
        let mut want = vec!["space"; jumps];
        want.push("a");
        assert_eq!(b.presses(), want, "{name}");
    }
}

#[test]
fn the_up_flash_is_timed_holds_up_into_the_second_jump_and_attacks_after() {
    let mut b = KeyBody::new();
    let mut marks = Vec::new();
    b.up_flash_timed(None, Some(0.2), &mut |m| marks.push(m.to_owned()));
    assert_eq!(
        b.seq(),
        [
            "down:space",
            "up:space",
            "down:up",
            "down:space",
            "up:space",
            "up:up",
            "press:a"
        ]
    );
    assert_eq!(marks, ["jump", "rejump"]);
    // The second jump went down 0.2s after the first (key spacing may
    // push it a little later, never earlier).
    let gap = b.times[3] - b.times[0];
    assert!((0.2..0.25).contains(&gap), "{gap}");
}

#[test]
fn a_rope_grab_holds_up_before_moving_and_never_attacks() {
    let mut b = KeyBody::new();
    b.ys = [100.0, 96.0, 90.0, 84.0, 78.0, 70.0, 64.0].into();
    assert!(b.rope_up(64.0, Some(Dir::Right), false));
    let seq = b.seq();
    let up = seq.iter().position(|e| *e == "down:up").unwrap();
    let right = seq.iter().position(|e| *e == "down:right").unwrap();
    let jump = seq.iter().position(|e| *e == "press:space").unwrap();
    assert!(up < right && right < jump, "{seq:?}");
    assert!(!b.presses().contains(&"a"));
    assert!(seq.ends_with(&["up:up", "up:right"]));
}

#[test]
fn a_rope_grab_that_never_latches_gives_up() {
    let mut b = KeyBody::new(); // y never changes
    assert!(!b.rope_up(40.0, None, false));
    assert!(b.clock < 4.0);
}

#[test]
fn walking_holds_a_direction_and_lets_go_at_the_target() {
    let mut b = KeyBody::new();
    b.cfg.flash_jump_enabled = false;
    assert!(b.move_to_point(80.0, 100.0, Some(4.0), Travel::Walk, true));
    assert!((b.pos.0 - 80.0).abs() <= 4.0);
    // Walking with flash off weaves an attack in first, then holds the arrow.
    let first_down = b.events.iter().find(|e| e.starts_with("down:"));
    assert_eq!(first_down.map(String::as_str), Some("down:right"));
    assert!(b.events.contains(&"up:right".to_owned()));
}

#[test]
fn rope_lift_and_teleport_respect_their_cooldowns() {
    let mut b = KeyBody::new();
    assert!(b.rope_lift());
    assert!(!b.rope_lift()); // 3s cooldown
    b.clock += 3.1;
    assert!(b.rope_lift());

    b.cfg.class_travel = ClassTravel::Teleport;
    b.cfg.teleport_key = Some("w".into());
    assert!(b.teleport(Some(Dir::Left)));
    assert!(!b.teleport(Some(Dir::Left)));
    assert!(b.presses().contains(&"w"));
}

#[test]
fn classes_that_cannot_attack_airborne_attack_after_landing() {
    let mut b = KeyBody::new();
    b.cfg.air_attacks = false;
    b.flash_hop();
    assert_eq!(b.presses(), ["space", "space", "a"]);
    assert!(b.clock > 0.3); // rode out the airtime before attacking
}
