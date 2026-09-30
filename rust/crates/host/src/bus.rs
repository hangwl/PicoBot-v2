//! The event bus: every log line the dashboard shows flows through here.
//!
//! Events are `{t, kind, level, msg, data?}`. A short history is kept so
//! a client that connects late sees recent context; per-keystroke chatter
//! (`debug`) has its own buffer so it can't push the rest out.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

pub const LEVELS: [&str; 4] = ["debug", "info", "warn", "error"];

/// Default level for a kind: anything unlisted is `info`.
fn kind_level(kind: &str) -> &'static str {
    match kind {
        "hid" => "debug",
        "notify" | "safety" => "warn",
        "error" => "error",
        _ => "info",
    }
}

pub type Subscriber = Arc<dyn Fn(&Value) + Send + Sync>;

pub struct Bus {
    buffers: Mutex<(VecDeque<Value>, VecDeque<Value>)>,
    subscribers: Mutex<Vec<Subscriber>>,
    history: usize,
    debug_history: usize,
}

impl Default for Bus {
    fn default() -> Self {
        Bus::new(300, 100)
    }
}

fn epoch() -> f64 {
    let t = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();
    (t * 1000.0).round() / 1000.0
}

impl Bus {
    pub fn new(history: usize, debug_history: usize) -> Self {
        Bus {
            buffers: Mutex::default(),
            subscribers: Mutex::default(),
            history,
            debug_history,
        }
    }

    pub fn emit(&self, kind: &str, msg: &str) {
        self.emit_full(kind, msg, None, None);
    }

    pub fn emit_level(&self, kind: &str, msg: &str, level: &str) {
        self.emit_full(kind, msg, Some(level), None);
    }

    /// Record and fan out one event. Subscribers run after the lock is
    /// released (they broadcast, which takes other locks).
    pub fn emit_full(
        &self,
        kind: &str,
        msg: &str,
        level: Option<&str>,
        data: Option<Value>,
    ) -> Value {
        let level = level
            .filter(|l| LEVELS.contains(l))
            .unwrap_or_else(|| kind_level(kind));
        let mut event = json!({"t": epoch(), "kind": kind, "level": level, "msg": msg});
        if let Some(d) = data.filter(|d| !d.is_null()) {
            event["data"] = d;
        }
        {
            let mut b = self.buffers.lock().unwrap();
            let (buf, cap) = if level == "debug" {
                (&mut b.1, self.debug_history)
            } else {
                (&mut b.0, self.history)
            };
            buf.push_back(event.clone());
            while buf.len() > cap {
                buf.pop_front();
            }
        }
        let subs: Vec<Subscriber> = self.subscribers.lock().unwrap().clone();
        for s in subs {
            s(&event);
        }
        event
    }

    pub fn subscribe(&self, f: impl Fn(&Value) + Send + Sync + 'static) {
        self.subscribers.lock().unwrap().push(Arc::new(f));
    }

    /// Recent events, oldest first (debug ones merged in by time).
    pub fn history(&self) -> Vec<Value> {
        let mut items: Vec<Value> = {
            let b = self.buffers.lock().unwrap();
            b.0.iter().chain(b.1.iter()).cloned().collect()
        };
        items.sort_by(|a, b| {
            a["t"]
                .as_f64()
                .unwrap_or(0.0)
                .total_cmp(&b["t"].as_f64().unwrap_or(0.0))
        });
        items
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn levels_default_by_kind_and_debug_has_its_own_buffer() {
        let bus = Bus::new(2, 1);
        assert_eq!(bus.emit_full("hid", "k", None, None)["level"], "debug");
        assert_eq!(bus.emit_full("error", "e", None, None)["level"], "error");
        assert_eq!(
            bus.emit_full("x", "m", Some("bogus"), None)["level"],
            "info"
        );
        bus.emit("x", "a");
        bus.emit("hid", "k2");
        let msgs: Vec<String> = bus
            .history()
            .iter()
            .map(|e| e["msg"].as_str().unwrap().to_owned())
            .collect();
        // Two kept of the non-debug events, one of the debug ones.
        assert_eq!(msgs, ["m", "a", "k2"]);
    }

    #[test]
    fn subscribers_see_every_event() {
        let bus = Bus::default();
        let n = Arc::new(AtomicUsize::new(0));
        let c = n.clone();
        bus.subscribe(move |e| {
            assert!(e["t"].as_f64().is_some());
            c.fetch_add(1, Ordering::SeqCst);
        });
        bus.emit("a", "1");
        bus.emit_full("b", "2", None, Some(json!({"k": 1})));
        assert_eq!(n.load(Ordering::SeqCst), 2);
        assert_eq!(bus.history()[1]["data"]["k"], 1);
    }
}
