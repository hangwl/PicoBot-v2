//! Human-like timing.
//!
//! Bots give themselves away by *how regularly* they act. Every delay here
//! is log-normal (a long right tail, like human timing) and scaled by a
//! session **tempo**: each session plays a little faster or slower than the
//! last, and the pace drifts slowly within it. The tempo moves medians only —
//! `human_between` still stays within its bounds, so game input windows
//! (the flash-jump re-press) always hold. Bounded draws are redrawn, not
//! clamped: a clamp piles samples onto the exact bound, a repeated value
//! that is itself a tell.
//!
//! The `*_with` functions take the random source and pace explicitly (for
//! tests and simulations); the plain ones use the thread's RNG and the
//! global [`tempo`].

use std::sync::{Mutex, OnceLock};
use std::time::Instant;

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use rand_distr::{Distribution, LogNormal, Normal};

/// Seconds on a monotonic clock (since first use).
pub fn monotonic() -> f64 {
    static START: OnceLock<Instant> = OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

struct TempoState {
    rng: StdRng,
    base: f64,
    /// Log of the drift factor (an Ornstein–Uhlenbeck walk).
    x: f64,
    t: f64,
}

/// Session pace: a random base times a slow, mean-reverting drift.
pub struct Tempo {
    base_sigma: f64,
    drift_sigma: f64,
    tau: f64,
    clock: Box<dyn Fn() -> f64 + Send + Sync>,
    state: Mutex<TempoState>,
}

impl Tempo {
    pub fn new() -> Self {
        Tempo::with(StdRng::from_os_rng(), Box::new(monotonic))
    }

    /// A tempo with a given RNG and clock (seconds) — for tests.
    pub fn with(rng: StdRng, clock: Box<dyn Fn() -> f64 + Send + Sync>) -> Self {
        let t = clock();
        let tempo = Tempo {
            base_sigma: 0.07,
            drift_sigma: 0.05,
            tau: 240.0,
            clock,
            state: Mutex::new(TempoState {
                rng,
                base: 1.0,
                x: 0.0,
                t,
            }),
        };
        tempo.new_session();
        tempo
    }

    /// Draw a fresh session pace (when the bot starts).
    pub fn new_session(&self) {
        let now = (self.clock)();
        let mut s = self.state.lock().unwrap();
        let base = LogNormal::new(0.0, self.base_sigma)
            .unwrap()
            .sample(&mut s.rng);
        s.base = base;
        s.x = 0.0;
        s.t = now;
    }

    pub fn base(&self) -> f64 {
        self.state.lock().unwrap().base
    }

    /// The current pace multiplier.
    pub fn factor(&self) -> f64 {
        let now = (self.clock)();
        let mut s = self.state.lock().unwrap();
        let dt = (now - s.t).max(0.0);
        s.t = now;
        if dt > 0.0 {
            let decay = (-dt / self.tau).exp();
            let spread = self.drift_sigma * (1.0 - decay * decay).sqrt();
            let step = Normal::new(0.0, spread).unwrap().sample(&mut s.rng);
            s.x = s.x * decay + step;
        }
        s.base * s.x.exp()
    }
}

impl Default for Tempo {
    fn default() -> Self {
        Tempo::new()
    }
}

/// The process-wide session tempo.
pub fn tempo() -> &'static Tempo {
    static TEMPO: OnceLock<Tempo> = OnceLock::new();
    TEMPO.get_or_init(Tempo::new)
}

/// Draw a fresh session pace (call when the bot starts).
pub fn new_session() {
    tempo().new_session();
}

fn lognormal<R: Rng + ?Sized>(rng: &mut R, sigma: f64) -> f64 {
    LogNormal::new(0.0, sigma).unwrap().sample(rng)
}

/// Log-normal delay with median `mean` seconds at `pace`, never below `minimum`.
pub fn human_delay_with<R: Rng + ?Sized>(
    rng: &mut R,
    pace: f64,
    mean: f64,
    sigma: f64,
    minimum: f64,
) -> f64 {
    (lognormal(rng, sigma) * mean * pace).max(minimum)
}

/// Log-normal delay with median `mean` at `pace`, truncated to `[lo, hi]`
/// — for gaps that must stay inside a game input window. Out-of-range
/// draws are redrawn; a window the distribution barely reaches falls back
/// to a uniform draw inside it.
pub fn human_between_with<R: Rng + ?Sized>(
    rng: &mut R,
    pace: f64,
    mean: f64,
    lo: f64,
    hi: f64,
    sigma: f64,
) -> f64 {
    if hi <= lo {
        return lo;
    }
    for _ in 0..16 {
        let v = lognormal(rng, sigma) * mean * pace;
        if (lo..=hi).contains(&v) {
            return v;
        }
    }
    rng.random_range(lo..=hi)
}

pub fn human_delay(mean: f64, sigma: f64, minimum: f64) -> f64 {
    human_delay_with(&mut rand::rng(), tempo().factor(), mean, sigma, minimum)
}

pub fn human_between(mean: f64, lo: f64, hi: f64, sigma: f64) -> f64 {
    human_between_with(&mut rand::rng(), tempo().factor(), mean, lo, hi, sigma)
}

/// Keys held a little longer than a tapped letter.
fn long_hold(key: Option<&str>) -> bool {
    matches!(
        key,
        Some("up" | "down" | "left" | "right" | "shift" | "ctrl" | "alt" | "space")
    )
}

/// How long a tapped key stays down: ~85ms (a bit longer for arrows and
/// modifiers), within [0.045, 0.22]s.
pub fn human_hold(key: Option<&str>) -> f64 {
    let median = 0.085 * if long_hold(key) { 1.15 } else { 1.0 };
    human_between(median, 0.045, 0.22, 0.28)
}

/// Minimum spacing between two key events: ~25ms, within [0.01, 0.07]s.
pub fn key_gap() -> f64 {
    human_between(0.025, 0.01, 0.07, 0.5)
}

/// Time to notice something unexpected and respond: ~0.22s.
pub fn human_reaction() -> f64 {
    human_between(0.22, 0.13, 0.55, 0.3)
}

/// How late a held key is let go after the goal is seen: ~40ms.
pub fn release_lag() -> f64 {
    human_between(0.04, 0.015, 0.1, 0.4)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::Arc;

    fn median(mut v: Vec<f64>) -> f64 {
        v.sort_by(f64::total_cmp);
        v[v.len() / 2]
    }

    #[test]
    fn delays_respect_their_floor_and_ballpark() {
        let mut rng = StdRng::seed_from_u64(1);
        for _ in 0..50 {
            assert!(human_delay_with(&mut rng, 1.0, 0.0, 0.35, 0.05) >= 0.05);
        }
        let samples: Vec<f64> = (0..500)
            .map(|_| human_delay_with(&mut rng, 1.0, 0.5, 0.35, 0.02))
            .collect();
        let mean = samples.iter().sum::<f64>() / samples.len() as f64;
        assert!(mean > 0.2 && mean < 1.2);
    }

    #[test]
    fn between_is_clamped_and_centred() {
        let mut rng = StdRng::seed_from_u64(2);
        let s: Vec<f64> = (0..500)
            .map(|_| human_between_with(&mut rng, 1.0, 0.17, 0.11, 0.26, 0.3))
            .collect();
        assert!(s.iter().all(|v| (0.11..=0.26).contains(v)));
        let above = s.iter().filter(|v| **v > 0.17).count();
        assert!(above > 150 && above < 350);
        let mut distinct: Vec<i64> = s.iter().map(|v| (v * 1000.0).round() as i64).collect();
        distinct.sort();
        distinct.dedup();
        assert!(distinct.len() > 50);
    }

    #[test]
    fn a_fast_or_slow_pace_never_breaks_a_clamp() {
        let mut rng = StdRng::seed_from_u64(3);
        for pace in [0.5, 2.0] {
            for _ in 0..200 {
                let v = human_between_with(&mut rng, pace, 0.17, 0.11, 0.26, 0.3);
                assert!((0.11..=0.26).contains(&v));
            }
        }
    }

    #[test]
    fn tight_bounds_do_not_pile_samples_onto_the_bound() {
        // A clamp would put ~25% of these on exactly 0.06 or 0.12.
        let mut rng = StdRng::seed_from_u64(4);
        for pace in [0.8, 1.0, 1.3] {
            let s: Vec<f64> = (0..2000)
                .map(|_| human_between_with(&mut rng, pace, 0.08, 0.06, 0.12, 0.3))
                .collect();
            assert!(s.iter().all(|v| (0.06..=0.12).contains(v)));
            let on_bound = s.iter().filter(|v| **v == 0.06 || **v == 0.12).count();
            assert_eq!(on_bound, 0);
        }
    }

    #[test]
    fn holds_stay_in_range_run_longer_for_arrows_and_skew_right() {
        for key in [None, Some("a"), Some("left")] {
            for _ in 0..200 {
                let v = human_hold(key);
                assert!((0.045..=0.22).contains(&v));
            }
        }
        let arrows = median((0..2000).map(|_| human_hold(Some("left"))).collect());
        let letters = median((0..2000).map(|_| human_hold(Some("a"))).collect());
        assert!(arrows > letters);
        let s: Vec<f64> = (0..4000).map(|_| human_hold(Some("a"))).collect();
        assert!(s.iter().sum::<f64>() / s.len() as f64 > median(s.clone()));
    }

    #[test]
    fn tempo_differs_per_session_and_drifts_boundedly() {
        // The clock is shared with the test through an atomic (f64 bits).
        let clock = Arc::new(AtomicU64::new(0f64.to_bits()));
        let c = clock.clone();
        let t = Tempo::with(
            StdRng::seed_from_u64(3),
            Box::new(move || f64::from_bits(c.load(Ordering::Relaxed))),
        );
        let mut bases = Vec::new();
        for _ in 0..5 {
            t.new_session();
            bases.push((t.base() * 1e4).round() as i64);
        }
        bases.sort();
        bases.dedup();
        assert_eq!(bases.len(), 5);
        let mut factors = Vec::new();
        for i in 1..=2000 {
            clock.store((i as f64 * 5.0).to_bits(), Ordering::Relaxed);
            factors.push(t.factor());
        }
        let (lo, hi) = factors
            .iter()
            .fold((f64::MAX, f64::MIN), |(a, b), f| (a.min(*f), b.max(*f)));
        assert!(hi - lo > 0.02); // it drifts
        assert!(factors.iter().all(|f| (0.7..1.4).contains(&(f / t.base()))));
    }
}
