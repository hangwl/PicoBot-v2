//! Map identity: the title read off the screen, and the user's pin.
//!
//! Resolution order: a confident title match for a *different* stored map
//! overrides the pin; otherwise the pin stands; otherwise the title match;
//! otherwise unknown. Reads are request-driven (startup, arrival, pin,
//! panel moved) and voted: a strong match is taken at once, else two
//! agreeing reads, else the last readable one after `max_reads`.
//!
//! This type does no I/O and starts no threads: the owner captures the
//! band when [`MapIdentity::wants_band`] says so, reads it (OCR) on a
//! worker, and hands the text to [`MapIdentity::finish_read`]. Methods
//! return the messages to announce, so the caller can log them with no
//! lock held.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use crate::maps::{MapEntry, MapStore};
use crate::title::normalize_name;

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Resolution {
    /// The resolved map (its alias).
    pub name: Option<String>,
    /// `"ocr"` or `"pin"`.
    pub via: Option<&'static str>,
    /// The accepted title text.
    pub title: Option<String>,
    /// Match score of `title`.
    pub score: f64,
    /// The stored map the title matched.
    pub title_map: Option<String>,
}

#[derive(Debug, Clone)]
struct Read {
    key: String,
    text: Option<String>,
    entry: Option<String>,
    score: f64,
}

#[derive(Default)]
struct Inner {
    want: bool,
    busy: bool,
    gen: u64,
    next_at: f64,
    reads: Vec<Read>,
    title: Option<Read>,
    current: Resolution,
    pin: Option<String>,
    pin_warned: bool,
}

pub struct MapIdentity {
    store: Arc<Mutex<MapStore>>,
    pub ocr_enabled: bool,
    pub retry_s: f64,
    pub max_reads: usize,
    pub strong_score: f64,
    inner: Mutex<Inner>,
    version: AtomicU64,
}

/// The stored map a title names, and the match score. Exact (normalised)
/// titles only; fuzzy scoring comes with the OCR reader.
pub fn match_title(store: &mut MapStore, text: &str) -> (Option<String>, f64) {
    let want = normalize_name(text);
    if want.is_empty() {
        return (None, 0.0);
    }
    let hit = store.load_all().iter().find(|e| {
        e.map_name
            .as_deref()
            .is_some_and(|m| normalize_name(m) == want)
    });
    match hit {
        Some(e) => (Some(e.name.clone()), 1.0),
        None => (None, 0.0),
    }
}

impl MapIdentity {
    pub fn new(
        store: Arc<Mutex<MapStore>>,
        pin: Option<String>,
        ocr_enabled: bool,
    ) -> (Self, Vec<String>) {
        let id = MapIdentity {
            store,
            ocr_enabled,
            retry_s: 0.3,
            max_reads: 5,
            strong_score: 0.97,
            inner: Mutex::new(Inner {
                pin: pin.filter(|p| !p.is_empty()),
                ..Default::default()
            }),
            version: AtomicU64::new(0),
        };
        let notes = id.recompute(&mut id.inner.lock().unwrap());
        (id, notes)
    }

    pub fn current(&self) -> Resolution {
        self.inner.lock().unwrap().current.clone()
    }

    /// Bumped on every change of the resolution.
    pub fn version(&self) -> u64 {
        self.version.load(Ordering::Relaxed)
    }

    pub fn pin(&self) -> Option<String> {
        self.inner.lock().unwrap().pin.clone()
    }

    /// A read is wanted or running.
    pub fn pending(&self) -> bool {
        let s = self.inner.lock().unwrap();
        s.want || s.busy
    }

    /// The resolved map's stored entry (fresh after saves).
    pub fn entry(&self) -> Option<MapEntry> {
        let name = self.inner.lock().unwrap().current.name.clone()?;
        self.store.lock().unwrap().get(&name).cloned()
    }

    /// Ask for a fresh title read. `clear` drops the previous title at once
    /// (arrival on a new map: the old title is stale).
    pub fn request(&self, clear: bool) -> Vec<String> {
        let mut s = self.inner.lock().unwrap();
        s.gen += 1;
        s.want = self.ocr_enabled;
        s.reads.clear();
        s.next_at = 0.0;
        if clear {
            s.title = None;
            return self.recompute(&mut s);
        }
        Vec::new()
    }

    pub fn set_pin(&self, name: Option<&str>) -> Vec<String> {
        let mut notes = {
            let mut s = self.inner.lock().unwrap();
            s.pin = name.filter(|n| !n.is_empty()).map(str::to_owned);
            s.pin_warned = false;
            self.recompute(&mut s)
        };
        notes.extend(self.request(false));
        notes
    }

    /// Re-resolve against the store (after saves and reloads).
    pub fn refresh(&self) -> Vec<String> {
        let mut s = self.inner.lock().unwrap();
        if let Some(t) = s.title.clone() {
            if let Some(text) = &t.text {
                let (entry, score) = match_title(&mut self.store.lock().unwrap(), text);
                s.title = Some(Read { entry, score, ..t });
            }
        }
        self.recompute(&mut s)
    }

    pub fn wants_band(&self, now: f64) -> bool {
        let s = self.inner.lock().unwrap();
        s.want && !s.busy && now >= s.next_at
    }

    /// Claim the next read: its generation, or None when one is running.
    pub fn begin_read(&self) -> Option<u64> {
        let mut s = self.inner.lock().unwrap();
        if s.busy {
            return None;
        }
        s.busy = true;
        Some(s.gen)
    }

    /// The text read for generation `gen` (None: unreadable).
    pub fn finish_read(&self, gen: u64, text: Option<String>, now: f64) -> Vec<String> {
        let (entry, score) = match &text {
            Some(t) => match_title(&mut self.store.lock().unwrap(), t),
            None => (None, 0.0),
        };
        let key = match (&entry, &text) {
            (Some(e), _) => e.clone(),
            (None, Some(t)) => format!("?{}", normalize_name(t)),
            (None, None) => String::new(),
        };
        let read = Read {
            key,
            text,
            entry: entry.clone(),
            score,
        };
        let mut s = self.inner.lock().unwrap();
        s.busy = false;
        if gen != s.gen {
            return Vec::new();
        }
        s.reads.push(read.clone());
        let n = s.reads.len();
        let strong = entry.is_some() && score >= self.strong_score;
        let agreed = n >= 2 && !read.key.is_empty() && s.reads[n - 2].key == read.key;
        let accepted = if strong || agreed {
            Some(read)
        } else if n >= self.max_reads {
            Some(
                s.reads
                    .iter()
                    .rev()
                    .find(|r| !r.key.is_empty())
                    .cloned()
                    .unwrap_or(read),
            )
        } else {
            None
        };
        let Some(acc) = accepted else {
            s.next_at = now + self.retry_s;
            return Vec::new();
        };
        s.want = false;
        let unreadable = acc.key.is_empty();
        s.title = (!unreadable).then_some(acc);
        let mut notes = if unreadable {
            vec!["map title unreadable".to_owned()]
        } else {
            Vec::new()
        };
        notes.extend(self.recompute(&mut s));
        notes
    }

    fn recompute(&self, s: &mut Inner) -> Vec<String> {
        let mut notes = Vec::new();
        let title_map = s.title.as_ref().and_then(|t| t.entry.clone());
        let pinned = s
            .pin
            .as_ref()
            .and_then(|p| self.store.lock().unwrap().get(p).map(|e| e.name.clone()));
        if let (Some(pin), None) = (&s.pin, &pinned) {
            if !s.pin_warned {
                s.pin_warned = true;
                notes.push(format!("pinned map '{pin}' not found"));
            }
        }
        let (name, via) = match (&pinned, &title_map) {
            (Some(p), Some(t)) if t != p => (Some(t.clone()), Some("ocr")),
            (Some(p), t) => (
                Some(p.clone()),
                Some(if t.as_ref() == Some(p) { "ocr" } else { "pin" }),
            ),
            (None, Some(t)) => (Some(t.clone()), Some("ocr")),
            (None, None) => (None, None),
        };
        let res = Resolution {
            name: name.clone(),
            via,
            title: s.title.as_ref().and_then(|t| t.text.clone()),
            score: s
                .title
                .as_ref()
                .map_or(0.0, |t| (t.score * 1000.0).round() / 1000.0),
            title_map,
        };
        if res != s.current {
            let prev = std::mem::replace(&mut s.current, res.clone());
            self.version.fetch_add(1, Ordering::Relaxed);
            if res.name != prev.name || res.title != prev.title {
                let mut detail = res
                    .title
                    .as_ref()
                    .map(|t| format!(" — title \"{t}\""))
                    .unwrap_or_default();
                if let Some(p) = pinned
                    .as_ref()
                    .filter(|p| via == Some("ocr") && name.as_ref() != Some(*p))
                {
                    detail += &format!(" (overrides pin '{p}')");
                }
                notes.push(format!(
                    "map: {} ({}){detail}",
                    name.as_deref().unwrap_or("–"),
                    via.unwrap_or("unknown")
                ));
            }
        }
        notes
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (Arc<Mutex<MapStore>>, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "pb-identity-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let mut s = MapStore::new(&dir);
        for (name, title) in [("East", "Limina : 1-5 East"), ("West", "Limina : 1-5 West")] {
            let mut e = MapEntry::new(name);
            e.map_name = Some(title.into());
            s.save(e).unwrap();
        }
        (Arc::new(Mutex::new(s)), dir)
    }

    #[test]
    fn the_pin_stands_until_a_title_names_another_map() {
        let (st, dir) = store();
        let (id, notes) = MapIdentity::new(st, Some("East".into()), true);
        assert_eq!(notes, ["map: East (pin)"]);
        assert_eq!(id.current().via, Some("pin"));
        id.request(false);
        assert!(id.wants_band(0.0));
        let g = id.begin_read().unwrap();
        assert!(id.begin_read().is_none()); // one at a time
        let notes = id.finish_read(g, Some("LIMINA 1-5 West".into()), 0.0);
        assert_eq!(
            notes,
            ["map: West (ocr) — title \"LIMINA 1-5 West\" (overrides pin 'East')"]
        );
        assert!(!id.pending());
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn unknown_titles_need_two_agreeing_reads() {
        let (st, dir) = store();
        let (id, _) = MapIdentity::new(st, None, true);
        id.request(false);
        let g = id.begin_read().unwrap();
        assert!(id.finish_read(g, Some("Somewhere".into()), 1.0).is_empty());
        assert!(!id.wants_band(1.1)); // retry delay
        let g = id.begin_read().unwrap();
        let notes = id.finish_read(g, Some("somewhere!".into()), 1.5);
        assert_eq!(notes, ["map: – (unknown) — title \"somewhere!\""]);
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn a_stale_read_is_dropped_and_a_missing_pin_warns_once() {
        let (st, dir) = store();
        let (id, notes) = MapIdentity::new(st, Some("Nope".into()), true);
        assert_eq!(notes, ["pinned map 'Nope' not found"]);
        id.request(false);
        let g = id.begin_read().unwrap();
        id.request(true); // arrival: new generation
        assert!(id
            .finish_read(g, Some("Limina 1-5 East".into()), 0.0)
            .is_empty());
        assert!(id.current().name.is_none());
        let v = id.version();
        assert!(id
            .set_pin(Some("East"))
            .contains(&"map: East (pin)".to_owned()));
        assert!(id.version() > v);
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn without_ocr_nothing_is_read() {
        let (st, dir) = store();
        let (id, _) = MapIdentity::new(st, None, false);
        id.request(false);
        assert!(!id.wants_band(0.0) && !id.pending());
        std::fs::remove_dir_all(dir).ok();
    }
}
