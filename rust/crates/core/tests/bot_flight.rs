//! The flight recorder and its arc analysis.

use std::collections::HashMap;
use std::sync::atomic::{AtomicI32, Ordering};
use std::sync::Arc;
use std::time::Duration;

use picobot_core::bot::{Flight, FlightRecorder};

/// A synthetic flight: stand, jump at `lead` s, peak, land back on `y0`.
pub struct Hop {
    pub y0: f64,
    pub rise: f64,
    pub peak_t: f64,
    pub air: f64,
    pub lead: f64,
    pub tail: f64,
    pub glitch: Option<usize>,
    pub marks: Option<HashMap<String, f64>>,
}

impl Default for Hop {
    fn default() -> Self {
        Hop {
            y0: 100.0,
            rise: 20.0,
            peak_t: 0.25,
            air: 0.6,
            lead: 0.1,
            tail: 0.2,
            glitch: None,
            marks: None,
        }
    }
}

impl Hop {
    pub fn flight(&self) -> Flight {
        let rate = 60.0;
        let n = ((self.lead + self.air + self.tail) * rate) as usize;
        let mut samples: Vec<(f64, i32, i32)> = (0..n)
            .map(|i| {
                let t = i as f64 / rate;
                let a = t - self.lead;
                let h = if a <= 0.0 || a >= self.air {
                    0.0
                } else if a <= self.peak_t {
                    self.rise * (1.0 - (1.0 - a / self.peak_t).powi(2))
                } else {
                    self.rise * (1.0 - ((a - self.peak_t) / (self.air - self.peak_t)).powi(2))
                };
                (t, 50, (self.y0 - h).round() as i32)
            })
            .collect();
        if let Some(g) = self.glitch {
            samples[g].2 = (self.y0 - self.rise.trunc() - 15.0) as i32;
        }
        let marks = self
            .marks
            .clone()
            .unwrap_or_else(|| [("jump".to_owned(), self.lead)].into());
        Flight { samples, marks }
    }
}

#[test]
fn peak_rise_and_time_after_the_jump() {
    let (t, rise) = Hop::default().flight().peak().unwrap();
    assert_eq!(rise, 20.0);
    assert!((t - 0.25).abs() <= 0.05);
}

#[test]
fn a_single_stray_sample_is_not_the_peak() {
    let f = Hop {
        glitch: Some(15),
        ..Default::default()
    }
    .flight();
    assert_eq!(f.peak().unwrap().1, 20.0);
}

#[test]
fn landing_back_on_the_start_row() {
    let (t, rise) = Hop::default().flight().landing().unwrap();
    assert_eq!(rise, 0.0);
    assert!((t - 0.6).abs() <= 0.05);
}

#[test]
fn no_landing_while_still_falling() {
    let mut f = Hop {
        tail: 0.0,
        ..Default::default()
    }
    .flight();
    f.samples.truncate((0.55 * 60.0) as usize);
    assert!(f.landing().is_none());
}

#[test]
fn the_start_row_is_the_standing_median() {
    assert_eq!(
        Hop {
            y0: 87.0,
            ..Default::default()
        }
        .flight()
        .start_y(),
        Some(87.0)
    );
}

#[test]
fn gap_between_marks() {
    let marks = [("jump".to_owned(), 0.1), ("rejump".to_owned(), 0.3)].into();
    let f = Hop {
        marks: Some(marks),
        ..Default::default()
    }
    .flight();
    assert!((f.gap("jump", "rejump").unwrap() - 0.2).abs() < 1e-9);
    assert!(Hop::default().flight().gap("jump", "rejump").is_none());
}

#[test]
fn an_empty_flight() {
    let f = Flight::default();
    assert!(f.peak().is_none() && f.landing().is_none());
}

#[test]
fn samples_on_its_own_thread_with_marks() {
    let y = Arc::new(AtomicI32::new(100));
    let yc = y.clone();
    let mut rec = FlightRecorder::new(
        Box::new(move || Some((5, yc.fetch_sub(1, Ordering::SeqCst).max(1)))),
        0.005,
    );
    rec.start();
    std::thread::sleep(Duration::from_millis(30));
    rec.mark("jump");
    std::thread::sleep(Duration::from_millis(30));
    let f = rec.stop();
    assert!(f.samples.len() > 3);
    assert!(f.marks.contains_key("jump"));
    assert!(f.samples.windows(2).all(|w| w[0].0 <= w[1].0));
    // Stopped: no more captures.
    let after = y.load(Ordering::SeqCst);
    std::thread::sleep(Duration::from_millis(20));
    assert_eq!(y.load(Ordering::SeqCst), after);
}

#[test]
fn misses_are_skipped_and_a_restart_starts_fresh() {
    let calls = Arc::new(AtomicI32::new(0));
    let c = calls.clone();
    let mut rec = FlightRecorder::new(
        Box::new(move || {
            if c.fetch_add(1, Ordering::SeqCst) < 3 {
                None
            } else {
                Some((1, 1))
            }
        }),
        0.005,
    );
    rec.start();
    while calls.load(Ordering::SeqCst) < 2 {
        std::thread::sleep(Duration::from_millis(1));
    }
    rec.mark("jump");
    let first = rec.stop();
    assert!(first.samples.len() <= 1);
    rec.start();
    std::thread::sleep(Duration::from_millis(30));
    let second = rec.stop();
    assert!(!second.samples.is_empty());
    assert!(second.marks.is_empty());
}
