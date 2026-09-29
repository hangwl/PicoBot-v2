//! Flight recorder: the player dot's whole path through one move.
//!
//! A landing only says where a move ended. The recorder samples the dot on
//! its own thread at a fixed rate while a move runs, so its peak and airtime
//! are seen too. Marks timestamp key events on the same clock ("jump",
//! "rejump").

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

/// (seconds since start, x, y)
pub type Sample = (f64, i32, i32);

pub const STABLE_SAMPLES: usize = 4;

#[derive(Debug, Clone, Default)]
pub struct Flight {
    pub samples: Vec<Sample>,
    pub marks: HashMap<String, f64>,
}

fn median(v: &mut [f64]) -> f64 {
    v.sort_by(f64::total_cmp);
    let n = v.len();
    if n % 2 == 1 {
        v[n / 2]
    } else {
        (v[n / 2 - 1] + v[n / 2]) / 2.0
    }
}

impl Flight {
    fn t0(&self) -> f64 {
        self.marks.get("jump").copied().unwrap_or(0.0)
    }

    /// Standing row before takeoff (median of the pre-jump samples).
    pub fn start_y(&self) -> Option<f64> {
        let first = self.samples.first()?;
        let t0 = self.t0();
        let mut before: Vec<f64> = self
            .samples
            .iter()
            .filter(|s| s.0 <= t0)
            .map(|s| s.2 as f64)
            .collect();
        if before.is_empty() {
            before.push(first.2 as f64);
        }
        Some(median(&mut before))
    }

    /// Median-of-3 on y: one stray detection can't fake a peak.
    fn smoothed(&self) -> Vec<Sample> {
        let s = &self.samples;
        if s.len() < 3 {
            return s.clone();
        }
        let mut out = vec![s[0]];
        for i in 1..s.len() - 1 {
            let mut ys = [s[i - 1].2, s[i].2, s[i + 1].2];
            ys.sort_unstable();
            out.push((s[i].0, s[i].1, ys[1]));
        }
        out.push(s[s.len() - 1]);
        out
    }

    /// (seconds after the jump, rise in px) at the highest point.
    pub fn peak(&self) -> Option<(f64, f64)> {
        let y0 = self.start_y()?;
        let t0 = self.t0();
        let (t, _, y) = self
            .smoothed()
            .into_iter()
            .filter(|s| s.0 >= t0)
            .min_by(|a, b| a.2.cmp(&b.2).then(a.0.total_cmp(&b.0)))?;
        Some((t - t0, y0 - y as f64))
    }

    /// (seconds after the jump, rise in px) where the dot comes to rest
    /// after the peak — None if it never settles.
    pub fn landing(&self) -> Option<(f64, f64)> {
        let pk = self.peak()?;
        let y0 = self.start_y()?;
        let t0 = self.t0();
        let after: Vec<(f64, i32)> = self
            .samples
            .iter()
            .filter(|s| s.0 >= t0 + pk.0)
            .map(|s| (s.0, s.2))
            .collect();
        after.windows(STABLE_SAMPLES).find_map(|run| {
            let (lo, hi) = run
                .iter()
                .fold((i32::MAX, i32::MIN), |(l, h), r| (l.min(r.1), h.max(r.1)));
            let rise = y0 - run[0].1 as f64;
            (hi - lo <= 1 && rise < pk.1 - 1.0).then_some((run[0].0 - t0, rise))
        })
    }

    pub fn gap(&self, first: &str, second: &str) -> Option<f64> {
        Some(self.marks.get(second)? - self.marks.get(first)?)
    }
}

type Capture = Box<dyn FnMut() -> Option<(i32, i32)> + Send>;

/// Samples a capture closure on its own thread between `start` and `stop`.
pub struct FlightRecorder {
    capture: Arc<Mutex<Capture>>,
    pub period: f64,
    t0: Instant,
    samples: Arc<Mutex<Vec<Sample>>>,
    marks: HashMap<String, f64>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl FlightRecorder {
    pub fn new(capture: Capture, period: f64) -> Self {
        FlightRecorder {
            capture: Arc::new(Mutex::new(capture)),
            period,
            t0: Instant::now(),
            samples: Arc::default(),
            marks: HashMap::new(),
            stop: Arc::default(),
            thread: None,
        }
    }

    pub fn start(&mut self) {
        self.stop_thread();
        self.samples.lock().unwrap().clear();
        self.marks.clear();
        self.t0 = Instant::now();
        self.stop.store(false, Ordering::SeqCst);
        let (capture, samples, stop) = (
            self.capture.clone(),
            self.samples.clone(),
            self.stop.clone(),
        );
        let (t0, period) = (self.t0, self.period);
        self.thread = Some(
            std::thread::Builder::new()
                .name("FlightRecorder".into())
                .spawn(move || {
                    let mut capture = capture.lock().unwrap();
                    while !stop.load(Ordering::SeqCst) {
                        let started = Instant::now();
                        if let Some((x, y)) = capture() {
                            let t = started.duration_since(t0).as_secs_f64();
                            samples.lock().unwrap().push((t, x, y));
                        }
                        let rest = (period - started.elapsed().as_secs_f64()).max(0.002);
                        std::thread::sleep(Duration::from_secs_f64(rest));
                    }
                })
                .expect("spawn FlightRecorder"),
        );
    }

    pub fn mark(&mut self, name: &str) {
        self.marks
            .insert(name.to_owned(), self.t0.elapsed().as_secs_f64());
    }

    fn stop_thread(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }

    pub fn stop(&mut self) -> Flight {
        self.stop_thread();
        Flight {
            samples: self.samples.lock().unwrap().clone(),
            marks: self.marks.clone(),
        }
    }
}

impl Drop for FlightRecorder {
    fn drop(&mut self) {
        self.stop_thread();
    }
}
