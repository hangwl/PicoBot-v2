//! Connected dashboard clients.
//!
//! Each WebSocket gets an outgoing text channel (drained by its own task)
//! and a one-frame slot for the view stream: a client still sending its
//! previous frame skips the next one, so a slow link drops frames instead
//! of queueing them ahead of control messages. Only bookkeeping happens
//! under the lock — never logging or broadcasting.

use std::collections::{BTreeSet, HashMap};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tokio::sync::{mpsc, Notify};

/// A frame still unsent after this closes the client (a frozen tab).
pub const FRAME_STALL: Duration = Duration::from_secs(10);

pub enum Out {
    Text(String),
    Close(u16, &'static str),
}

/// The latest frame for one client, and when the one being sent started.
#[derive(Default)]
pub struct FrameSlot {
    pub frame: Mutex<Option<Arc<Vec<u8>>>>,
    pub sending_since: Mutex<Option<Instant>>,
    pub ready: Notify,
}

struct Client {
    tx: mpsc::UnboundedSender<Out>,
    peer: String,
    frames: bool,
    held: BTreeSet<String>,
    slot: Arc<FrameSlot>,
}

#[derive(Default)]
pub struct Clients {
    map: Mutex<HashMap<u64, Client>>,
    next: AtomicU64,
}

pub struct Registered {
    pub id: u64,
    pub rx: mpsc::UnboundedReceiver<Out>,
    pub slot: Arc<FrameSlot>,
}

impl Clients {
    pub fn register(&self, peer: &str) -> Registered {
        let id = self.next.fetch_add(1, Ordering::Relaxed) + 1;
        let (tx, rx) = mpsc::unbounded_channel();
        let slot = Arc::new(FrameSlot::default());
        let client = Client {
            tx,
            peer: peer.into(),
            frames: false,
            held: BTreeSet::new(),
            slot: slot.clone(),
        };
        self.map.lock().unwrap().insert(id, client);
        Registered { id, rx, slot }
    }

    /// Forget a client; returns the keys it still held down.
    pub fn unregister(&self, id: u64) -> BTreeSet<String> {
        self.map
            .lock()
            .unwrap()
            .remove(&id)
            .map(|c| c.held)
            .unwrap_or_default()
    }

    pub fn peer(&self, id: u64) -> String {
        self.map
            .lock()
            .unwrap()
            .get(&id)
            .map(|c| c.peer.clone())
            .unwrap_or_default()
    }

    pub fn send(&self, id: u64, text: String) {
        if let Some(c) = self.map.lock().unwrap().get(&id) {
            let _ = c.tx.send(Out::Text(text));
        }
    }

    /// Every client gets it (replies and the view are shared).
    pub fn broadcast(&self, text: &str) {
        let text = text.trim();
        if text.is_empty() {
            return;
        }
        for c in self.map.lock().unwrap().values() {
            let _ = c.tx.send(Out::Text(text.to_owned()));
        }
    }

    pub fn subscribe_frames(&self, id: u64) {
        if let Some(c) = self.map.lock().unwrap().get_mut(&id) {
            c.frames = true;
        }
    }

    pub fn has_frame_clients(&self) -> bool {
        self.map.lock().unwrap().values().any(|c| c.frames)
    }

    /// Hand a frame to every subscriber that is free; returns the peers
    /// closed for a stalled send.
    pub fn broadcast_frame(&self, data: Arc<Vec<u8>>) -> Vec<String> {
        let now = Instant::now();
        let mut stalled = Vec::new();
        for c in self.map.lock().unwrap().values_mut() {
            if !c.frames {
                continue;
            }
            let since = *c.slot.sending_since.lock().unwrap();
            if let Some(t) = since {
                if now - t > FRAME_STALL {
                    c.frames = false;
                    let _ = c.tx.send(Out::Close(1011, "frame send stalled"));
                    stalled.push(c.peer.clone());
                }
                continue;
            }
            *c.slot.frame.lock().unwrap() = Some(data.clone());
            c.slot.ready.notify_one();
        }
        stalled
    }

    /// Track remote key presses so a dropped client's keys get released.
    pub fn track_key(&self, id: u64, msg: &str) {
        let parts: Vec<&str> = msg.split('|').collect();
        let parts = if parts.first() == Some(&"hid") {
            &parts[1..]
        } else {
            &parts[..]
        };
        if parts.len() < 3 || parts[0] != "key" {
            return;
        }
        if let Some(c) = self.map.lock().unwrap().get_mut(&id) {
            match parts[1] {
                "down" => {
                    c.held.insert(parts[2].to_owned());
                }
                "up" => {
                    c.held.remove(parts[2]);
                }
                _ => {}
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn held_keys_are_returned_on_unregister() {
        let c = Clients::default();
        let r = c.register("p");
        c.track_key(r.id, "key|down|left");
        c.track_key(r.id, "hid|key|down|alt");
        c.track_key(r.id, "key|up|alt");
        assert_eq!(c.unregister(r.id).into_iter().collect::<Vec<_>>(), ["left"]);
        assert!(c.unregister(r.id).is_empty());
    }

    #[test]
    fn frames_go_only_to_free_subscribers_and_stalls_close() {
        let c = Clients::default();
        let mut a = c.register("a");
        let b = c.register("b");
        c.subscribe_frames(a.id);
        assert!(c.has_frame_clients());
        c.broadcast_frame(Arc::new(vec![1]));
        assert!(a.slot.frame.lock().unwrap().is_some());
        assert!(b.slot.frame.lock().unwrap().is_none());
        *a.slot.sending_since.lock().unwrap() = Some(Instant::now() - FRAME_STALL * 2);
        assert_eq!(c.broadcast_frame(Arc::new(vec![2])), ["a"]);
        assert!(matches!(a.rx.try_recv(), Ok(Out::Close(1011, _))));
        assert!(!c.has_frame_clients());
    }
}
