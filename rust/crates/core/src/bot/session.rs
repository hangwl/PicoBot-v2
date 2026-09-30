//! What one bot run got done: anchor visits, misses, skips and pauses,
//! for the periodic heartbeat and the stop summary.

#[derive(Debug, Clone, Default)]
pub struct Session {
    started: f64,
    pub visits: u32,
    pub misses: u32,
    pub skips: u32,
    pub pauses: u32,
    paused_secs: f64,
    paused_at: Option<f64>,
    last_beat: f64,
    /// Legs run by move kind: (landed, missed).
    moves: std::collections::BTreeMap<String, (u32, u32)>,
}

impl Session {
    pub fn new(now: f64) -> Self {
        Session {
            started: now,
            last_beat: now,
            ..Default::default()
        }
    }

    /// Count an anchor outcome (`visit`, `miss`, else a skip).
    pub fn record(&mut self, kind: &str) {
        match kind {
            "visit" => self.visits += 1,
            "miss" => self.misses += 1,
            _ => self.skips += 1,
        }
    }

    /// Count one leg of `kind` (`flash`, `walk`, `climb_up`, …).
    pub fn record_move(&mut self, kind: &str, landed: bool) {
        let m = self.moves.entry(kind.to_owned()).or_default();
        if landed {
            m.0 += 1;
        } else {
            m.1 += 1;
        }
    }

    /// `flash 41 · walk 12 · climb_up 3 (1 missed)`, most used first.
    pub fn moves_line(&self) -> String {
        let mut v: Vec<(&String, &(u32, u32))> = self.moves.iter().collect();
        v.sort_by(|a, b| (b.1 .0 + b.1 .1).cmp(&(a.1 .0 + a.1 .1)).then(a.0.cmp(b.0)));
        v.iter()
            .map(|(k, (ok, bad))| {
                if *bad > 0 {
                    format!("{k} {ok} ({bad} missed)")
                } else {
                    format!("{k} {ok}")
                }
            })
            .collect::<Vec<_>>()
            .join(" · ")
    }

    pub fn pause_begin(&mut self, now: f64) {
        self.pauses += 1;
        self.paused_at = Some(now);
    }

    pub fn pause_end(&mut self, now: f64) {
        if let Some(t) = self.paused_at.take() {
            self.paused_secs += now - t;
        }
    }

    /// True once per `every_s` seconds (0 = never).
    pub fn beat_due(&mut self, now: f64, every_s: f64) -> bool {
        if every_s <= 0.0 || now - self.last_beat < every_s {
            return false;
        }
        self.last_beat = now;
        true
    }

    /// The dashboard's view of the run so far.
    pub fn snapshot(&self, now: f64) -> serde_json::Value {
        serde_json::json!({
            "up": (now - self.started).max(0.0).round(),
            "visits": self.visits,
            "misses": self.misses,
            "skips": self.skips,
            "pauses": self.pauses,
            "paused": (self.paused_secs + self.paused_at.map_or(0.0, |t| now - t)).round(),
            "moves": self.moves.iter().map(|(k, (ok, bad))| (k.clone(), serde_json::json!({"ok": ok, "failed": bad}))).collect::<serde_json::Map<_, _>>(),
        })
    }

    pub fn summary(&self, now: f64) -> String {
        let paused = self.paused_secs + self.paused_at.map_or(0.0, |t| now - t);
        let tries = self.visits + self.misses;
        let rate = if tries > 0 {
            format!(" ({:.0}%)", 100.0 * self.misses as f64 / tries as f64)
        } else {
            String::new()
        };
        let moves = if self.moves.is_empty() {
            String::new()
        } else {
            format!("; moves: {}", self.moves_line())
        };
        format!(
            "up {}, {} visits, {} misses{rate}, {} skips, {} pauses ({}){moves}",
            hms(now - self.started),
            self.visits,
            self.misses,
            self.skips,
            self.pauses,
            hms(paused),
        )
    }
}

fn hms(secs: f64) -> String {
    let s = secs.max(0.0) as u64;
    match (s / 3600, s / 60 % 60) {
        (0, 0) => format!("{s}s"),
        (0, m) => format!("{m}m{:02}s", s % 60),
        (h, m) => format!("{h}h{m:02}m"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_summary_counts_outcomes_and_pause_time() {
        let mut s = Session::new(100.0);
        for k in ["visit", "visit", "visit", "miss", "skip"] {
            s.record(k);
        }
        s.pause_begin(200.0);
        s.pause_end(260.0);
        s.pause_begin(300.0); // still paused at the summary
        assert_eq!(
            s.summary(400.0),
            "up 5m00s, 3 visits, 1 misses (25%), 1 skips, 2 pauses (2m40s)"
        );
    }

    #[test]
    fn the_beat_fires_once_per_period_and_never_at_zero() {
        let mut s = Session::new(0.0);
        assert!(!s.beat_due(59.0, 60.0));
        assert!(s.beat_due(60.0, 60.0));
        assert!(!s.beat_due(61.0, 60.0));
        assert!(!s.beat_due(9999.0, 0.0));
    }

    #[test]
    fn durations_read_naturally() {
        assert_eq!(hms(42.0), "42s");
        assert_eq!(hms(125.0), "2m05s");
        assert_eq!(hms(7500.0), "2h05m");
    }
}

#[cfg(test)]
mod move_tests {
    use super::*;

    #[test]
    fn moves_are_counted_by_kind_most_used_first() {
        let mut s = Session::new(0.0);
        for _ in 0..4 {
            s.record_move("flash", true);
        }
        s.record_move("walk", true);
        s.record_move("climb_up", true);
        s.record_move("climb_up", false);
        s.record_move("climb_up", false);
        assert_eq!(s.moves_line(), "flash 4 · climb_up 1 (2 missed) · walk 1");
        let snap = s.snapshot(1.0);
        assert_eq!(snap["moves"]["climb_up"]["failed"], 2);
    }
}
