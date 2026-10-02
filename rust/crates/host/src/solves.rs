//! Rune-solve recordings: the dataset for reading the rune's arrow puzzle.
//!
//! While the bot pauses at a rune, the `RuneRecorder` thread keeps a short
//! pre-roll of game-window frames. The first dashboard key starts a
//! recording; the keys pressed (the interact key, then the arrows) are its
//! labels. It ends when the bot resumes farming (solved), stops or pauses
//! for something else (interrupted), or goes quiet (unsolved), and is saved
//! under `debug/frames/<ts>_runesolve/`:
//!
//! - `frames/NNN.jpg` — the window from `PRE_ROLL_S` before the first key
//!   to `POST_ROLL_S` after the last, listed with times in `frames.json`;
//! - `key_NN_<key>.png` — the last frame before each arrow key-down,
//!   lossless (the arrows as they were read);
//! - `keys.json` — every key event, times relative to the first key;
//! - `meta.json` — outcome, the arrow sequence, window size, frame rate.

use std::collections::VecDeque;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::Duration;

use picobot_core::timing::monotonic;
use picobot_core::vision::Image;
use serde_json::json;

use crate::evidence::{prune, stamp, write_png};
use crate::feed::Eyes;
use crate::frames::encode_jpeg;
use crate::host::Host;

const DIR: &str = "debug/frames";
const SUFFIX: &str = "_runesolve";
/// Newest recordings kept.
const KEEP: usize = 30;
/// Frames per second while armed or recording.
const FPS: f64 = 8.0;
const PRE_ROLL_S: f64 = 2.0;
const POST_ROLL_S: f64 = 1.0;
/// A recording ends after this long without a key …
const IDLE_S: f64 = 8.0;
/// … or this long in all.
const MAX_S: f64 = 30.0;
/// Stopped or paused for something else this long ends a recording: the
/// bot passes through a bare PAUSE (a reaction delay) on its way back to
/// farming after a solve.
const OTHER_GRACE_S: f64 = 1.5;
const JPEG_QUALITY: u8 = 90;
const ARROWS: [&str; 4] = ["up", "down", "left", "right"];

/// What the bot is doing, as the recorder cares.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    /// Paused at a rune: a solve may happen.
    AtRune,
    /// Farming again (the rune is gone).
    Farming,
    /// Stopped, or paused for something else.
    Other,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    Solved,
    Unsolved,
    Interrupted,
}

impl Outcome {
    fn as_str(self) -> &'static str {
        match self {
            Outcome::Solved => "solved",
            Outcome::Unsolved => "unsolved",
            Outcome::Interrupted => "interrupted",
        }
    }
}

/// One key event from the dashboard: (time, `down`|`up`, key).
pub type KeyEvent = (f64, String, String);

pub struct Recording {
    pub frames: Vec<(f64, Image)>,
    pub keys: Vec<KeyEvent>,
    pub outcome: Outcome,
}

impl Recording {
    /// The arrow key-downs, in order.
    pub fn arrows(&self) -> Vec<&str> {
        self.keys
            .iter()
            .filter(|(_, ev, k)| ev == "down" && ARROWS.contains(&k.as_str()))
            .map(|(_, _, k)| k.as_str())
            .collect()
    }

    /// The arrows of each attempt: a non-arrow key-down (the interact
    /// key) starts one, and the arrow key-downs after it are its answer.
    pub fn attempts(&self) -> Vec<Vec<&str>> {
        let mut out: Vec<Vec<&str>> = Vec::new();
        for (_, ev, k) in &self.keys {
            if ev != "down" {
                continue;
            }
            if ARROWS.contains(&k.as_str()) {
                if let Some(cur) = out.last_mut() {
                    cur.push(k.as_str());
                }
            } else {
                out.push(Vec::new());
            }
        }
        out.retain(|a| !a.is_empty());
        out
    }
}

/// The recording logic, fed one tick at a time (no screen, no disk).
#[derive(Default)]
pub struct Recorder {
    pre: VecDeque<(f64, Image)>,
    cur: Option<Recording>,
    other_since: Option<f64>,
}

impl Recorder {
    pub fn recording(&self) -> bool {
        self.cur.is_some()
    }

    /// One tick: a finished recording when this tick ends one.
    pub fn tick(
        &mut self,
        now: f64,
        phase: Phase,
        frame: Option<Image>,
        keys: Vec<KeyEvent>,
    ) -> Option<Recording> {
        let Some(rec) = self.cur.as_mut() else {
            if phase != Phase::AtRune {
                self.pre.clear();
                return None;
            }
            if let Some(f) = frame {
                self.pre.push_back((now, f));
            }
            while self.pre.front().is_some_and(|(t, _)| now - t > PRE_ROLL_S) {
                self.pre.pop_front();
            }
            if keys.is_empty() {
                return None;
            }
            self.cur = Some(Recording {
                frames: self.pre.drain(..).collect(),
                keys,
                outcome: Outcome::Unsolved,
            });
            return None;
        };
        if let Some(f) = frame {
            rec.frames.push((now, f));
        }
        rec.keys.extend(keys);
        let first = rec.keys.first().map_or(now, |k| k.0);
        let last = rec.keys.last().map_or(now, |k| k.0);
        let other_for = match phase {
            Phase::Other => now - *self.other_since.get_or_insert(now),
            _ => {
                self.other_since = None;
                0.0
            }
        };
        let outcome = match phase {
            Phase::Farming => Some(Outcome::Solved),
            Phase::Other if other_for >= OTHER_GRACE_S => Some(Outcome::Interrupted),
            Phase::Other => None,
            Phase::AtRune if now - last > IDLE_S || now - first > MAX_S => Some(Outcome::Unsolved),
            Phase::AtRune => None,
        }?;
        let mut rec = self.cur.take()?;
        self.other_since = None;
        rec.outcome = outcome;
        // Only the frames around the keys: before, during, just after.
        rec.frames
            .retain(|(t, _)| *t >= first - PRE_ROLL_S && *t <= last + POST_ROLL_S);
        Some(rec)
    }
}

/// Write `rec` under `root`; returns its folder.
pub fn save(root: &std::path::Path, rec: &Recording) -> std::io::Result<PathBuf> {
    let dir = root.join(format!("{}{SUFFIX}", stamp()));
    fs::create_dir_all(dir.join("frames"))?;
    let t0 = rec.keys.first().map_or(0.0, |k| k.0);
    let rel = |t: f64| ((t - t0) * 1000.0).round() / 1000.0;
    let mut listed = Vec::new();
    for (i, (t, img)) in rec.frames.iter().enumerate() {
        let name = format!("{i:03}.jpg");
        if let Some(jpg) = encode_jpeg(img, JPEG_QUALITY) {
            fs::write(dir.join("frames").join(&name), jpg)?;
            listed.push(json!({ "file": name, "t": rel(*t) }));
        }
    }
    let mut n = 0;
    for (t, ev, key) in &rec.keys {
        if ev != "down" || !ARROWS.contains(&key.as_str()) {
            continue;
        }
        if let Some((_, img)) = rec.frames.iter().rev().find(|(ft, _)| ft < t) {
            write_png(&dir.join(format!("key_{n:02}_{key}.png")), img)?;
        }
        n += 1;
    }
    let keys: Vec<_> = rec
        .keys
        .iter()
        .map(|(t, ev, k)| json!({ "t": rel(*t), "event": ev, "key": k }))
        .collect();
    let size = rec.frames.first().map(|(_, i)| [i.width, i.height]);
    let meta = json!({
        "outcome": rec.outcome.as_str(),
        "arrows": rec.arrows(),
        "attempts": rec.attempts(),
        "frames": rec.frames.len(),
        "fps": FPS,
        "window": size,
    });
    let pretty = |v: &serde_json::Value| serde_json::to_string_pretty(v).unwrap_or_default();
    fs::write(dir.join("frames.json"), pretty(&json!(listed)))?;
    fs::write(dir.join("keys.json"), pretty(&json!(keys)))?;
    fs::write(dir.join("meta.json"), pretty(&meta))?;
    prune(root, SUFFIX, KEEP);
    Ok(dir)
}

/// The bot's phase, from what it last published.
fn phase(host: &Host) -> Phase {
    match host.bot_viz() {
        Some(v) if v.state == "PAUSE" && v.hazard.as_deref() == Some("rune") => Phase::AtRune,
        Some(v) if matches!(v.state.as_str(), "GRIND" | "TRAVEL" | "RUNE") => Phase::Farming,
        _ => Phase::Other,
    }
}

pub struct RuneRecorder {
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl RuneRecorder {
    pub fn start(host: Arc<Host>) -> RuneRecorder {
        let stop = Arc::new(AtomicBool::new(false));
        let s = stop.clone();
        let thread = std::thread::Builder::new()
            .name("RuneRecorder".into())
            .spawn(move || run(host, s))
            .expect("spawn the rune recorder");
        RuneRecorder {
            stop,
            thread: Some(thread),
        }
    }
}

impl Drop for RuneRecorder {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

fn run(host: Arc<Host>, stop: Arc<AtomicBool>) {
    let mut eyes: Option<Eyes> = None;
    let mut rec = Recorder::default();
    while !stop.load(Ordering::Relaxed) {
        let started = monotonic();
        let on = host.bot_config.lock().unwrap().record_rune_solves;
        let phase = if on { phase(&host) } else { Phase::Other };
        let keys = host.take_remote_keys();
        let frame = if phase == Phase::AtRune || rec.recording() {
            let title = host.window_title();
            if eyes.as_ref().is_some_and(|e| e.window.title != title) {
                eyes = None;
            }
            if eyes.is_none() {
                eyes = Eyes::open(&title);
            }
            eyes.as_mut().and_then(Eyes::window_img)
        } else {
            None
        };
        if let Some(done) = rec.tick(started, phase, frame, keys) {
            let arrows = done.arrows().join(" ");
            let msg = match save(std::path::Path::new(DIR), &done) {
                Ok(dir) => format!(
                    "Rune solve recorded ({}, arrows: {arrows}) → {}",
                    done.outcome.as_str(),
                    dir.display()
                ),
                Err(e) => format!("Rune solve recording failed: {e}"),
            };
            host.log(&msg);
        }
        let idle = if phase == Phase::AtRune || rec.recording() {
            1.0 / FPS
        } else {
            0.25
        };
        let left = idle - (monotonic() - started);
        if left > 0.0 {
            std::thread::sleep(Duration::from_secs_f64(left));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(t: f64, ev: &str, k: &str) -> KeyEvent {
        (t, ev.into(), k.into())
    }

    /// Ticks at 8/s from `t0` for `secs`, in `phase`.
    fn ticks(r: &mut Recorder, t0: f64, secs: f64, phase: Phase) -> Option<Recording> {
        let mut t = t0;
        while t < t0 + secs {
            if let Some(done) = r.tick(t, phase, Some(Image::new(4, 4)), Vec::new()) {
                return Some(done);
            }
            t += 1.0 / FPS;
        }
        None
    }

    #[test]
    fn a_solve_is_recorded_from_the_pre_roll_to_the_resume() {
        let mut r = Recorder::default();
        assert!(ticks(&mut r, 0.0, 5.0, Phase::AtRune).is_none());
        assert!(!r.recording());
        r.tick(5.0, Phase::AtRune, None, vec![key(5.0, "down", "space")]);
        assert!(r.recording());
        let presses = ["left", "up", "up", "right"];
        for (i, k) in presses.iter().enumerate() {
            let t = 6.0 + i as f64 * 0.5;
            r.tick(
                t,
                Phase::AtRune,
                Some(Image::new(4, 4)),
                vec![key(t, "down", k), key(t + 0.1, "up", k)],
            );
        }
        assert!(ticks(&mut r, 8.0, 3.0, Phase::AtRune).is_none());
        let done = r
            .tick(11.0, Phase::Farming, None, Vec::new())
            .expect("finished");
        assert_eq!(done.outcome, Outcome::Solved);
        assert_eq!(done.arrows(), presses);
        let (first, last) = (done.frames[0].0, done.frames.last().unwrap().0);
        assert!((5.0 - PRE_ROLL_S - 1e-9..3.2).contains(&first), "{first}");
        assert!(last <= 7.6 + POST_ROLL_S + 1e-9, "{last}");
    }

    #[test]
    fn nothing_is_kept_away_from_a_rune_and_quiet_or_cut_solves_say_so() {
        let mut r = Recorder::default();
        assert!(r
            .tick(
                0.0,
                Phase::Farming,
                Some(Image::new(4, 4)),
                vec![key(0.0, "down", "left")]
            )
            .is_none());
        assert!(!r.recording());

        r.tick(1.0, Phase::AtRune, None, vec![key(1.0, "down", "space")]);
        let done = ticks(&mut r, 1.0, IDLE_S + 1.0, Phase::AtRune).expect("timed out");
        assert_eq!(done.outcome, Outcome::Unsolved);

        r.tick(20.0, Phase::AtRune, None, vec![key(20.0, "down", "space")]);
        assert!(r.tick(20.5, Phase::Other, None, Vec::new()).is_none());
        let done = r
            .tick(20.5 + OTHER_GRACE_S, Phase::Other, None, Vec::new())
            .expect("cut");
        assert_eq!(done.outcome, Outcome::Interrupted);
    }

    #[test]
    fn the_pause_on_the_way_back_to_farming_still_counts_as_solved() {
        let mut r = Recorder::default();
        r.tick(0.0, Phase::AtRune, None, vec![key(0.0, "down", "y")]);
        r.tick(1.0, Phase::AtRune, None, vec![key(1.0, "down", "up")]);
        assert!(r.tick(5.0, Phase::Other, None, Vec::new()).is_none()); // reaction delay
        let done = r.tick(5.3, Phase::Farming, None, Vec::new()).expect("done");
        assert_eq!(done.outcome, Outcome::Solved);
    }

    #[test]
    fn each_interact_press_starts_an_attempt() {
        let keys = [
            "y", "up", "down", "left", "up", "y", "up", "right", "left", "down",
        ];
        let rec = Recording {
            frames: Vec::new(),
            keys: keys
                .iter()
                .enumerate()
                .map(|(i, k)| key(i as f64, "down", k))
                .collect(),
            outcome: Outcome::Solved,
        };
        assert_eq!(
            rec.attempts(),
            [
                vec!["up", "down", "left", "up"],
                vec!["up", "right", "left", "down"]
            ]
        );
    }

    #[test]
    fn a_recording_is_saved_with_its_labels() {
        let root = std::env::temp_dir().join(format!("runesolve-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let rec = Recording {
            frames: vec![(0.9, Image::new(8, 6)), (1.2, Image::new(8, 6))],
            keys: vec![
                key(1.0, "down", "space"),
                key(1.1, "down", "left"),
                key(1.15, "up", "left"),
            ],
            outcome: Outcome::Solved,
        };
        let dir = save(&root, &rec).unwrap();
        assert!(dir.join("frames/000.jpg").exists() && dir.join("frames/001.jpg").exists());
        assert!(dir.join("key_00_left.png").exists());
        let meta: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(dir.join("meta.json")).unwrap()).unwrap();
        assert_eq!(meta["outcome"], "solved");
        assert_eq!(meta["arrows"], json!(["left"]));
        let keys: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(dir.join("keys.json")).unwrap()).unwrap();
        assert_eq!(keys[0]["t"], 0.0);
        let _ = fs::remove_dir_all(&root);
    }
}
