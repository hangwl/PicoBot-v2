//! The Python host's move-measurement tests, ported onto the simulator.

mod sim;

use std::collections::HashMap;
use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex};

use picobot_core::bot::{Body, Flight, MeasureStatus, Mode, MoveMeasurer, Recorder};
use picobot_core::config::ClassTravel;
use picobot_core::reach::{Move, Reach};
use sim::*;

const LEDGE: [f64; 4] = [40.0, 78.0, 160.0, 78.0]; // 22px above the floor
const HIGH: [f64; 4] = [60.0, 56.0, 140.0, 56.0]; // 22px above the ledge

/// Measurement physics: every move carries further than the model thinks
/// (flash 37 vs 30, rises 22 vs 12/15, teleport 44/22 vs 25/12), except
/// the jump (10 vs 8 carried) and the double flash (48 vs 45).
fn mbot(plats: &[[f64; 4]], pos: (f64, f64)) -> Sim {
    let r = |dx, rise| Reach { dx, rise };
    let reach = reach_with(&[
        (Move::Jump, r(10.0, 4.0)),
        (Move::Flash, r(30.0, 4.0)),
        (Move::DoubleFlash, r(48.0, 4.0)),
        (Move::UpFlash, r(6.0, 12.0)),
        (Move::UpSideFlash, r(30.0, 20.0)),
        (Move::RopeLift, r(3.0, 15.0)),
        (Move::Teleport, r(25.0, 12.0)),
    ]);
    let mut b = Sim::patrol(plats, pos, &[]).with_reach(reach);
    (b.flash, b.double, b.up, b.rope, b.tele, b.walk) = (37.0, 45.0, 22.0, 22.0, (44.0, 22.0), 0.0);
    b
}

struct Run {
    events: Vec<String>,
    statuses: Vec<MeasureStatus>,
    results: serde_json::Map<String, serde_json::Value>,
}

impl Run {
    fn has(&self, needle: &str) -> bool {
        self.events.iter().any(|e| e.contains(needle))
    }
}

fn run_with(
    b: &mut Sim,
    mode: Mode,
    only: Option<&str>,
    rec: Option<Box<dyn Recorder + Send>>,
) -> Run {
    let events = Arc::new(Mutex::new(Vec::new()));
    let statuses = Arc::new(Mutex::new(Vec::new()));
    let (stop, stop_on) = (b.stop.clone(), b.stop_on.clone());
    let ev = events.clone();
    let st = statuses.clone();
    let mut m = MoveMeasurer::new(
        rec,
        Box::new(move |_, msg| ev.lock().unwrap().push(msg.to_owned())),
        Box::new(move |s: MeasureStatus| {
            if s.current.is_some() && s.current == stop_on {
                stop.store(true, Ordering::SeqCst);
            }
            st.lock().unwrap().push(s);
        }),
    );
    m.run(b, mode, only);
    let events = std::mem::take(&mut *events.lock().unwrap());
    let statuses = std::mem::take(&mut *statuses.lock().unwrap());
    Run {
        events,
        statuses,
        results: m.results,
    }
}

fn run(b: &mut Sim) -> Run {
    run_with(b, Mode::Moves, None, None)
}

fn measured(b: &Sim) -> Vec<&str> {
    b.state
        .reach
        .measured
        .iter()
        .map(|m| m.0.as_str())
        .collect()
}

#[test]
fn measured_moves_calibrate_reach() {
    let mut b = mbot(&[FLOOR, LEDGE, HIGH], (30.0, 100.0));
    run(&mut b);
    assert_eq!(b.state.reach.get(Move::Flash).dx, 37.0); // grew from 30
    assert_eq!(b.state.reach.get(Move::UpFlash).rise, 22.0); // grew from 12
    assert_eq!(b.state.reach.get(Move::RopeLift).rise, 22.0); // grew from 15
    assert!(b.moves.contains(&"up_flash".into()) && b.moves.contains(&"rope_lift".into()));
}

#[test]
fn vertical_moves_are_skipped_without_a_platform_above() {
    let mut b = mbot(&[FLOOR], (30.0, 100.0));
    run(&mut b);
    assert!(!b.moves.contains(&"up_flash".into()) && !b.moves.contains(&"rope_lift".into()));
    assert_eq!(b.state.reach.get(Move::Flash).dx, 37.0); // horizontal still ran
}

#[test]
fn horizontal_moves_are_skipped_without_room() {
    let mut b = mbot(&[[0.0, 100.0, 20.0, 100.0]], (10.0, 100.0));
    run(&mut b);
    assert!(!b.moves.contains(&"flash".into()));
}

#[test]
fn an_unfocused_game_stops_the_measurement() {
    let mut b = mbot(&[FLOOR], (30.0, 100.0));
    b.focus = false;
    assert!(run(&mut b).has("stopped"));
}

#[test]
fn a_teleport_class_measures_teleport_not_flashes() {
    let mut b = mbot(&[FLOOR, LEDGE, HIGH], (30.0, 100.0));
    b.cfg.class_travel = ClassTravel::Teleport;
    b.cfg.teleport_key = Some("shift".into());
    run(&mut b);
    assert!(!b.moves.contains(&"flash".into()) && !b.moves.contains(&"up_flash".into()));
    assert!(b.moves.contains(&"teleport:right".into()));
    assert!(b.moves.contains(&"teleport:up".into()));
    let tp = b.state.reach.get(Move::Teleport);
    assert_eq!((tp.dx, tp.rise), (44.0, 22.0)); // both grew from 25/12
}

#[test]
fn teleport_waits_out_its_cooldown() {
    let mut b = mbot(&[FLOOR], (30.0, 100.0));
    b.cfg.class_travel = ClassTravel::Teleport;
    b.cfg.teleport_key = Some("shift".into());
    b.teleport_cd = 0.8;
    run(&mut b);
    assert!(b.moves.contains(&"teleport:right".into()));
}

#[test]
fn a_walk_class_measures_only_jump_and_rope_lift() {
    let mut b = mbot(&[FLOOR, LEDGE], (30.0, 100.0));
    b.cfg.class_travel = ClassTravel::Walk;
    run(&mut b);
    assert_eq!(b.moves, ["rope_lift"]);
    assert_eq!(b.state.reach.get(Move::Jump).dx, 8.0); // measured: lowered
    assert_eq!(b.state.reach.get(Move::Flash).dx, 30.0); // untouched
}

#[test]
fn flash_disabled_skips_flash_moves() {
    let mut b = mbot(&[FLOOR], (30.0, 100.0));
    b.cfg.flash_jump_enabled = false;
    run(&mut b);
    assert!(!b.moves.contains(&"flash".into()));
}

#[test]
fn results_and_status_are_published() {
    let mut b = mbot(&[FLOOR], (30.0, 100.0));
    let r = run(&mut b);
    let last = r.statuses.last().unwrap();
    assert!(!last.running);
    assert_eq!(last.results["flash"], serde_json::json!({"dx": 37.0}));
    assert!(last.results["rope_lift"]["skipped"]
        .as_str()
        .unwrap()
        .contains("no platform above"));
    assert!(measured(&b).contains(&"flash"));
    assert!(!measured(&b).contains(&"rope_lift"));
}

#[test]
fn a_stopped_run_keeps_finished_moves() {
    let mut b = mbot(&[FLOOR], (30.0, 100.0));
    b.stop_on = Some("double_flash".into()); // stop arrives during the 2nd move
    run(&mut b);
    assert!(measured(&b).contains(&"flash"));
    assert!(!measured(&b).contains(&"double_flash"));
    assert_eq!(b.state.reach.get(Move::Flash).dx, 37.0);
}

#[test]
fn falling_off_a_ledge_is_reported() {
    let mut b = mbot(
        &[[0.0, 100.0, 60.0, 100.0], [0.0, 130.0, 200.0, 130.0]],
        (20.0, 100.0),
    );
    b.flash = 50.0; // overshoots the ledge
    assert!(run(&mut b).has("fell off a ledge"));
}

#[test]
fn one_move_measures_only_that_move() {
    let mut b = mbot(&[FLOOR, LEDGE, HIGH], (60.0, 100.0));
    let r = run_with(&mut b, Mode::Moves, Some("rope_lift"), None);
    assert_eq!(b.moves, ["rope_lift"]);
    assert_eq!(r.results.keys().collect::<Vec<_>>(), ["rope_lift"]);
    assert_eq!(b.state.reach.get(Move::RopeLift).rise, 22.0);
    assert_eq!(b.state.reach.get(Move::Flash).dx, 30.0); // untouched
    assert_eq!(measured(&b), ["rope_lift"]);
}

#[test]
fn one_move_outside_the_kit_is_refused() {
    let mut b = mbot(&[FLOOR], (30.0, 100.0));
    let r = run_with(&mut b, Mode::Moves, Some("teleport"), None); // a flash class
    assert!(b.moves.is_empty());
    assert!(r.has("isn't one of this class's moves"));
}

#[test]
fn no_platforms_warns() {
    let mut b = mbot(&[], (30.0, 100.0));
    assert!(run(&mut b).has("draw the platforms"));
}

// -- Recorded up flashes -----------------------------------------------------------

/// Fake physics: the later the re-press (to 0.25s), the higher.
fn peak_for(delay: Option<f64>) -> f64 {
    match delay {
        None => 30.0,
        Some(d) => 12.0 + 40.0 * d.min(0.25) - 20.0 * (d - 0.3).max(0.0),
    }
}

/// Turns the sim's last takeoff into an arc of the matching height.
struct FakeRecorder {
    flights: Arc<Mutex<Vec<String>>>,
    n: usize,
    marks: HashMap<String, f64>,
}

impl FakeRecorder {
    fn new(b: &Sim) -> Self {
        FakeRecorder {
            flights: b.flights.clone(),
            n: 0,
            marks: HashMap::new(),
        }
    }
}

impl Recorder for FakeRecorder {
    fn start(&mut self) {
        self.n = self.flights.lock().unwrap().len();
    }
    fn mark(&mut self, name: &str) {
        self.marks
            .insert(name.into(), if name == "jump" { 0.1 } else { 0.3 });
    }
    fn stop(&mut self) -> Flight {
        let flights = self.flights.lock().unwrap();
        let Some(last) = flights.get(self.n..).and_then(|f| f.last()) else {
            return flight_arc(0.0, None);
        };
        let delay = match last.as_str() {
            "jump" | "none" => None,
            d => d.parse().ok(),
        };
        let marks = (!self.marks.is_empty()).then(|| self.marks.clone());
        flight_arc(peak_for(delay), marks)
    }
}

/// The flight tests' arc (stand, jump at 0.1s, peak at 0.25s, land at 0.6s).
fn flight_arc(rise: f64, marks: Option<HashMap<String, f64>>) -> Flight {
    let samples = (0..54)
        .map(|i| {
            let a = i as f64 / 60.0 - 0.1;
            let h = if a <= 0.0 || a >= 0.6 {
                0.0
            } else if a <= 0.25 {
                rise * (1.0 - (1.0 - a / 0.25).powi(2))
            } else {
                rise * (1.0 - ((a - 0.25) / 0.35).powi(2))
            };
            (i as f64 / 60.0, 50, (100.0 - h).round() as i32)
        })
        .collect();
    Flight {
        samples,
        marks: marks.unwrap_or_else(|| [("jump".to_owned(), 0.1)].into()),
    }
}

fn measure_recorded(b: &mut Sim, mode: Mode) -> Run {
    let rec = FakeRecorder::new(b);
    run_with(b, mode, None, Some(Box::new(rec)))
}

#[test]
fn the_up_flash_rise_is_the_recorded_peak_not_the_ledge() {
    let mut b = mbot(&[FLOOR, LEDGE, HIGH], (30.0, 100.0));
    measure_recorded(&mut b, Mode::Moves);
    // The ledge above is 22px up; the patrol-timed arc peaks at 30px.
    assert_eq!(b.state.reach.get(Move::UpFlash).rise, 30.0);
    assert!(measured(&b).contains(&"up_flash"));
}

#[test]
fn a_recorded_up_flash_needs_no_platform_above() {
    let mut b = mbot(&[FLOOR], (30.0, 100.0));
    let r = measure_recorded(&mut b, Mode::Moves);
    assert!(b.moves.contains(&"up_flash".into()));
    assert_eq!(r.results["up_flash"], serde_json::json!({"rise": 30.0}));
}

#[test]
fn the_sweep_records_every_delay_and_keeps_the_envelope() {
    let mut b = mbot(&[FLOOR], (30.0, 100.0));
    let before = b.state.reach.get(Move::UpFlash).rise;
    let r = measure_recorded(&mut b, Mode::UpFlashProfile);
    let rows = b.state.reach.profiles["up_flash"]["rows"]
        .as_array()
        .unwrap()
        .clone();
    let delays: Vec<Option<f64>> = rows.iter().map(|r| r["delay"].as_f64()).collect();
    let want: Vec<Option<f64>> = std::iter::once(None)
        .chain(picobot_core::bot::measure::PROFILE_DELAYS.map(Some))
        .collect();
    assert_eq!(delays, want);
    assert!(rows
        .iter()
        .all(|r| r["n"] == picobot_core::bot::measure::PROFILE_REPS));
    let by = |d: Option<f64>| {
        rows.iter()
            .find(|r| r["delay"].as_f64() == d)
            .unwrap()
            .clone()
    };
    assert_eq!(by(Some(0.08))["rise"], 15.0);
    assert_eq!(by(Some(0.25))["rise"], 22.0);
    assert_eq!(by(None)["rise"], 30.0); // plain-jump baseline
    assert!((by(Some(0.2))["gap"].as_f64().unwrap() - 0.2).abs() < 1e-9);
    assert_eq!(b.state.reach.get(Move::UpFlash).rise, before);
    assert!(!measured(&b).contains(&"up_flash"));
    assert!(r.has("sweep saved"));
}

#[test]
fn the_sweep_refuses_a_low_ceiling() {
    let mut b = mbot(&[FLOOR, LEDGE], (60.0, 100.0));
    let r = measure_recorded(&mut b, Mode::UpFlashProfile);
    assert!(b.moves.is_empty());
    assert!(r.has("22px overhead"));
}

#[test]
fn the_sweep_stops_when_it_lands_higher() {
    let mut b = mbot(&[FLOOR, [0.0, 50.0, 200.0, 50.0]], (30.0, 100.0)); // 50px up: allowed
    b.up = 60.0; // ...but reached
    let r = measure_recorded(&mut b, Mode::UpFlashProfile);
    assert!(r.has("landed 50px higher"));
    assert!(b.flights.lock().unwrap().len() <= 2);
}

#[test]
fn the_sweep_is_for_flash_classes() {
    let mut b = mbot(&[FLOOR], (30.0, 100.0));
    b.cfg.class_travel = ClassTravel::Teleport;
    measure_recorded(&mut b, Mode::UpFlashProfile);
    assert!(b.moves.is_empty());
}

#[test]
fn the_status_carries_profiles() {
    let mut b = mbot(&[FLOOR], (30.0, 100.0));
    let r = measure_recorded(&mut b, Mode::UpFlashProfile);
    let last = serde_json::to_value(r.statuses.last().unwrap()).unwrap();
    assert_eq!(last["mode"], "up_flash_profile");
    assert!(last["profiles"].get("up_flash").is_some());
    assert!(!last["profile"].as_array().unwrap().is_empty());
}

#[test]
fn a_class_without_double_flash_does_not_measure_it() {
    use picobot_core::bot::measure::plan_for;
    let mut cfg = picobot_core::config::BotConfig::default();
    assert!(plan_for(&cfg).contains(&"double_flash"));
    cfg.double_flash = false;
    assert!(!plan_for(&cfg).contains(&"double_flash"));
    assert!(plan_for(&cfg).contains(&"flash"));
}

#[test]
fn the_tap_sweep_measures_taps_and_pace() {
    let mut b = mbot(&[[0.0, 100.0, 200.0, 100.0]], (100.0, 100.0));
    b.walk = 4.0;
    let mut m = MoveMeasurer::new(None, Box::new(|_, _| {}), Box::new(|_| {}));
    m.run(&mut b, Mode::WalkTaps, None);
    let table = b.state.reach.tap_table().expect("taps saved");
    assert_eq!(table.rows().len(), 6);
    // 50 px/s: a 120 ms tap carries 6 px, and the turn-only first tap of
    // each direction change is not counted.
    let r = table
        .rows()
        .iter()
        .find(|r| (r.secs - 0.12).abs() < 1e-9)
        .unwrap();
    assert!((r.dx - 6.0).abs() < 0.6, "{r:?}");
    // 4 px per 0.1 s sleep while a key is held.
    let w = b.state.reach.walk_stats().expect("pace saved");
    assert!((w.speed - 40.0).abs() < 4.0, "{w:?}");
}

#[test]
fn nudge_closes_in_with_measured_taps() {
    let mut b = mbot(&[[0.0, 100.0, 200.0, 100.0]], (100.0, 100.0));
    assert!(!b.nudge_to(113.0, 1.0)); // no table yet
    let rows: Vec<serde_json::Value> = [(30, 1.5), (50, 2.5), (120, 6.0), (260, 13.0)]
        .iter()
        .map(|(ms, dx)| serde_json::json!({"ms": ms, "n": 4, "dx": dx, "sd": 0.3}))
        .collect();
    b.state.reach.set_profile("walk_taps", rows);
    assert!(b.nudge_to(113.0, 1.0));
    assert!((b.pos.0 - 113.0).abs() <= 1.5, "{}", b.pos.0);
    assert!(b.nudge_to(104.0, 1.0)); // back the other way (a turn tap first)
    assert!((b.pos.0 - 104.0).abs() <= 1.5, "{}", b.pos.0);
}
