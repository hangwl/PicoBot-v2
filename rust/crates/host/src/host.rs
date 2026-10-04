//! The host: shared state, the serial writer, and the dashboard command
//! worker. WebSocket tasks only route messages; anything that can block
//! (files, serial handshakes, joining threads) runs on the
//! `DashboardCommands` thread, in order.

use std::collections::{BTreeSet, HashMap, HashSet, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU16, AtomicU64, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex, Weak};
use std::time::Duration;

use picobot_core::anchor_stats::AnchorStats;
use picobot_core::bot::measure::plan_for;
use picobot_core::bot::{LegViz, MeasureStatus, Mode, Viz};
use picobot_core::config::{AppConfig, BotConfig, ClassTravel};
use picobot_core::identity::MapIdentity;
use picobot_core::keys::{self, PICO_KEYS};
use picobot_core::maps::{MapEntry, MapStore, PlayerRule};
use picobot_core::navgraph::{GraphCache, GraphOptions, NavGraph};
use picobot_core::platform_fit::{PlatformFit, SegKey};
use picobot_core::reach::{base_reach, ReachModel};
use picobot_core::rotation::resolve_coord;
use picobot_core::skills::Skill;
use picobot_core::timing::monotonic;
use picobot_core::vision::Region;
use picobot_io::hid::{HELD, KEYS, RELEASE_ALL};
use picobot_io::ocr::{find_model, TitleReader};
use picobot_io::serial::{discover_data_port, find_data_port, list_ports, SendError, SerialLink};
use serde_json::{json, Map, Value};

use crate::botbody::{epoch, spawn_bot, spawn_measure, BotRun};
use crate::bus::Bus;
use crate::clients::Clients;
use crate::commands::UndoStep;
use crate::feed::{Feed, TitleJob};
use crate::frames::Overlay;
use crate::solves::KeyEvent;
use crate::telegram::Telegram;

/// WS message prefixes handled by the host; anything else is HID input
/// for the Pico.
const DASHBOARD_PREFIXES: [&str; 16] = [
    "bot|",
    "map|",
    "measure|",
    "dash|",
    "host|",
    "events|",
    "config|",
    "layout|",
    "skills|",
    "movekeys|",
    "nav|",
    "class|",
    "patrol|",
    "safety|",
    "notify|",
    "attacks|",
];

pub struct Host {
    pub bus: Arc<Bus>,
    pub clients: Arc<Clients>,
    pub root: PathBuf,
    pub(crate) config: Mutex<AppConfig>,
    pub(crate) bot_config: Mutex<BotConfig>,
    pub(crate) window_title: Mutex<String>,
    pub(crate) serial: Mutex<Option<Arc<SerialLink>>>,
    pub(crate) serial_tx: Sender<String>,
    /// A `SerialReconnect` thread is retrying a lost port.
    pub(crate) reconnecting: AtomicBool,
    pub(crate) jobs: Mutex<Option<Sender<String>>>,
    pub(crate) ws_port: AtomicU16,
    pub(crate) http_port: AtomicU16,
    pub maps: Arc<Mutex<MapStore>>,
    pub identity: Arc<MapIdentity>,
    pub(crate) reach: Mutex<ReachModel>,
    pub(crate) graphs: Mutex<GraphCache>,
    pub(crate) platfit: Mutex<PlatformFit>,
    pub(crate) anchor_stats: Mutex<AnchorStats>,
    pub(crate) feed: Mutex<Option<Arc<Feed>>>,
    /// The streamer drops its window handle and grabber on the next tick.
    pub(crate) eyes_reset: AtomicBool,
    pub(crate) view_mode: Mutex<String>,
    /// Identity version of the last `maps` payload, plus one (0: never).
    pub(crate) maps_sent: AtomicU64,
    pub(crate) nav_show: AtomicBool,
    pub(crate) stall_warned: Mutex<f64>,
    pub(crate) bot: Mutex<Option<BotRun>>,
    pub(crate) measurer: Mutex<Option<BotRun>>,
    pub(crate) last_measure: Mutex<Option<MeasureStatus>>,
    pub(crate) bot_viz: Mutex<Option<Viz>>,
    /// Dashboard key events, newest last, for the rune-solve recorder.
    pub(crate) remote_keys: Mutex<VecDeque<KeyEvent>>,
    pub(crate) pending_skills: Mutex<Option<Vec<Skill>>>,
    /// Bumped by every map save (the bot re-reads its entry).
    pub(crate) map_edits: AtomicU64,
    pub(crate) maps_pushed: Mutex<f64>,
    /// Bumped by every settings edit (a running bot re-reads its config).
    pub(crate) config_version: AtomicU64,
    /// The route preview and when it lapses.
    pub(crate) nav_preview: Mutex<Option<(Vec<LegViz>, f64)>>,
    /// (map, field) -> the edits this session can undo.
    pub(crate) layout_undo: Mutex<HashMap<(String, &'static str), Vec<UndoStep>>>,
    /// Title bands for the TitleOCR worker (None without a reader).
    pub(crate) ocr_tx: Mutex<Option<Sender<TitleJob>>>,
    pub(crate) telegram: Telegram,
}

/// The title recogniser, if its model is found and loads.
fn load_reader(root: &std::path::Path, bus: &Bus) -> Option<TitleReader> {
    let Some(model) = find_model(root) else {
        bus.emit_level(
            "map",
            "title OCR off: PP-OCRv6_rec_small.onnx not found (models/ or the Python venv)",
            "warn",
        );
        return None;
    };
    match TitleReader::load(&model) {
        Ok(r) => Some(r),
        Err(e) => {
            bus.emit_level("map", &format!("title OCR off: {e}"), "warn");
            None
        }
    }
}

/// How often a lost port is retried.
const RECONNECT_EVERY: Duration = Duration::from_secs(2);

/// What to try for a lost port: `want` itself once it's back, otherwise
/// only ports that appeared since the loss (probing toggles DTR, which
/// resets some devices). `seen` forgets ports that went away, so one
/// that comes back is new again.
fn reconnect_candidates(want: &str, now: &[String], seen: &mut HashSet<String>) -> Vec<String> {
    seen.retain(|p| now.contains(p));
    if now.iter().any(|p| p == want) {
        return vec![want.to_owned()];
    }
    let fresh: Vec<String> = now.iter().filter(|p| !seen.contains(*p)).cloned().collect();
    seen.extend(fresh.iter().cloned());
    fresh
}

/// `left|page up|mouse:left` → its names (a key name may hold a comma).
fn parse_names(data: &str) -> BTreeSet<String> {
    data.split('|')
        .map(str::trim)
        .filter(|k| !k.is_empty())
        .map(str::to_owned)
        .collect()
}

fn port_names() -> Vec<String> {
    list_ports().into_iter().map(|(name, _)| name).collect()
}

/// Stop any run (its keys would all fail), then retry until a link is
/// open again: ours, or one picked in Connection meanwhile.
fn reconnect_serial(host: &Weak<Host>, want: &str) {
    let Some(h) = host.upgrade() else { return };
    if h.is_bot_running() {
        h.bus.emit("error", "Bot stopped: the Pico link was lost");
        h.stop_bot();
    }
    if h.is_measuring() {
        h.bus
            .emit("error", "Measurement stopped: the Pico link was lost");
        Host::stop_slot(&h.measurer);
    }
    drop(h);
    let mut seen: HashSet<String> = port_names().into_iter().collect();
    loop {
        std::thread::sleep(RECONNECT_EVERY);
        let Some(h) = host.upgrade() else { return };
        if h.serial_open() {
            return;
        }
        let candidates = if picobot_io::hid_transport::parse_spec(want).is_some() {
            vec![want.to_owned()]
        } else {
            reconnect_candidates(want, &port_names(), &mut seen)
        };
        let found = if candidates == [want] {
            Some(want.to_owned())
        } else {
            find_data_port(&candidates, Duration::from_millis(1500))
        };
        let Some(port) = found else { continue };
        if h.try_open_serial(&port).is_err() {
            continue;
        }
        if port != want {
            h.config.lock().unwrap().serial_port = port.clone();
            h.save_config();
        }
        if let Some(link) = h.serial_link() {
            // The firmware let go when the port dropped; this makes sure.
            if link.wait_ready(Duration::from_secs(5)) {
                let _ = link.send_acked(RELEASE_ALL, Duration::from_millis(1500));
            }
        }
        h.bus
            .emit("host", &format!("Pico link reconnected on {port}"));
        h.send_host_state();
        return;
    }
}

pub(crate) fn dash(payload: Value) -> String {
    format!("dash|{payload}")
}

impl Host {
    pub fn new(root: PathBuf, config: AppConfig, window_title: String) -> Arc<Host> {
        let bus = Arc::new(Bus::default());
        let clients = Arc::new(Clients::default());
        let c = clients.clone();
        bus.subscribe(move |e| {
            let mut o = Map::new();
            o.insert("event".into(), "evt".into());
            o.extend(
                e.as_object()
                    .into_iter()
                    .flatten()
                    .map(|(k, v)| (k.clone(), v.clone())),
            );
            c.broadcast(&dash(Value::Object(o)));
        });
        let bot_config = BotConfig::from_json(&config.bot).unwrap_or_else(|e| {
            bus.emit("error", &format!("config: {e} — using defaults"));
            BotConfig::default()
        });
        let maps = Arc::new(Mutex::new(MapStore::new(root.join(&bot_config.maps_dir))));
        let telegram = Telegram::new(&config.bot_token, &config.chat_id, bus.clone());
        let reader = if bot_config.name_ocr {
            load_reader(&root, &bus)
        } else {
            None
        };
        let (identity, notes) = MapIdentity::new(
            maps.clone(),
            bot_config.active_map.clone(),
            reader.is_some(),
        );
        for n in notes {
            bus.emit("map", &n);
        }
        let reach = ReachModel::load(base_reach(&bot_config), root.join(bot_config.reach_path()));
        let (serial_tx, serial_rx) = channel();
        let host = Arc::new(Host {
            maps,
            identity: Arc::new(identity),
            reach: Mutex::new(reach),
            graphs: Mutex::default(),
            platfit: Mutex::default(),
            anchor_stats: Mutex::default(),
            feed: Mutex::new(None),
            eyes_reset: AtomicBool::new(false),
            view_mode: Mutex::new("minimap".into()),
            maps_sent: AtomicU64::new(0),
            nav_show: AtomicBool::new(false),
            stall_warned: Mutex::new(f64::NEG_INFINITY),
            bot: Mutex::new(None),
            measurer: Mutex::new(None),
            last_measure: Mutex::new(None),
            bot_viz: Mutex::new(None),
            remote_keys: Mutex::default(),
            pending_skills: Mutex::new(None),
            map_edits: AtomicU64::new(0),
            maps_pushed: Mutex::new(f64::NEG_INFINITY),
            config_version: AtomicU64::new(0),
            nav_preview: Mutex::new(None),
            layout_undo: Mutex::default(),
            ocr_tx: Mutex::new(None),
            telegram,
            bus,
            clients,
            root,
            ws_port: AtomicU16::new(config.ws_port as u16),
            http_port: AtomicU16::new(config.http_port as u16),
            config: Mutex::new(config),
            bot_config: Mutex::new(bot_config),
            window_title: Mutex::new(window_title),
            serial: Mutex::new(None),
            serial_tx,
            reconnecting: AtomicBool::new(false),
            jobs: Mutex::new(None),
        });
        let h = host.clone();
        std::thread::Builder::new()
            .name("SerialWriter".into())
            .spawn(move || h.serial_writer(serial_rx))
            .expect("spawn the serial writer");
        if let Some(reader) = reader {
            let (tx, rx) = channel();
            *host.ocr_tx.lock().unwrap() = Some(tx);
            let h = host.clone();
            std::thread::Builder::new()
                .name("TitleOCR".into())
                .spawn(move || h.title_worker(reader, rx))
                .expect("spawn the title reader");
        }
        let (jobs_tx, jobs_rx) = channel();
        *host.jobs.lock().unwrap() = Some(jobs_tx);
        let h = host.clone();
        std::thread::Builder::new()
            .name("DashboardCommands".into())
            .spawn(move || h.command_loop(jobs_rx))
            .expect("spawn the command worker");
        host
    }

    pub fn ws_port(&self) -> u16 {
        self.ws_port.load(Ordering::Relaxed)
    }
    pub fn set_ws_port(&self, p: u16) {
        self.ws_port.store(p, Ordering::Relaxed);
    }
    pub fn set_http_port(&self, p: u16) {
        self.http_port.store(p, Ordering::Relaxed);
    }

    pub(crate) fn log(&self, msg: &str) {
        let level = if msg.starts_with("TX:") || msg.starts_with("RX:") {
            "debug"
        } else {
            "info"
        };
        self.bus.emit_level("remote", msg, level);
    }

    pub(crate) fn save_config(&self) {
        let cfg = self.config.lock().unwrap().clone();
        if let Err(e) = cfg.save(&self.root.join("config.json")) {
            self.bus
                .emit("error", &format!("saving config failed: {e}"));
        }
    }

    // -- WebSocket routing ------------------------------------------------------------

    pub fn client_connected(&self, id: u64) {
        let peer = self.clients.peer(id);
        self.log(&format!("WS: client connected {peer}"));
        let port = self.ws_port();
        self.bus.emit(
            "status",
            &format!("Remote: Connected (ws://0.0.0.0:{port})"),
        );
    }

    pub fn client_gone(&self, id: u64) {
        let held = self.clients.unregister(id);
        for key in &held {
            self.enqueue_hid(&format!("key|up|{key}"));
        }
        if !held.is_empty() {
            self.log(&format!(
                "WS: released held keys {:?}",
                held.iter().collect::<Vec<_>>()
            ));
        }
        self.log("WS: client disconnected");
        let port = self.ws_port();
        self.bus.emit(
            "status",
            &format!("Remote: Listening (ws://0.0.0.0:{port})"),
        );
    }

    /// One text message from a client (on the async runtime: never block).
    pub fn on_message(&self, id: u64, msg: &str) {
        if DASHBOARD_PREFIXES.iter().any(|p| msg.starts_with(p)) {
            match msg {
                "dash|subscribe|frames" => self.clients.subscribe_frames(id),
                "bot|query" => {
                    let running = self.is_bot_running();
                    self.clients
                        .send(id, dash(json!({"event": "bot", "running": running})));
                }
                _ => self.submit(msg),
            }
            return;
        }
        self.clients.track_key(id, msg);
        self.note_remote_key(msg);
        self.enqueue_hid(msg);
    }

    /// Keep a dashboard `key|down|x` / `key|up|x` for the solve recorder.
    fn note_remote_key(&self, msg: &str) {
        const KEEP: usize = 64;
        let mut parts = msg.split('|');
        if let (Some("key"), Some(ev @ ("down" | "up")), Some(key)) =
            (parts.next(), parts.next(), parts.next())
        {
            let mut keys = self.remote_keys.lock().unwrap();
            keys.push_back((monotonic(), ev.to_owned(), key.to_owned()));
            while keys.len() > KEEP {
                keys.pop_front();
            }
        }
    }

    /// The dashboard key events since the last call.
    pub fn take_remote_keys(&self) -> Vec<KeyEvent> {
        self.remote_keys.lock().unwrap().drain(..).collect()
    }

    pub(crate) fn submit(&self, msg: &str) {
        if let Some(tx) = self.jobs.lock().unwrap().as_ref() {
            let _ = tx.send(msg.to_owned());
        }
    }

    pub(crate) fn command_loop(self: Arc<Self>, rx: Receiver<String>) {
        for msg in rx {
            match msg.as_str() {
                "bot|start" => {
                    self.log("WS: bot|start received");
                    self.start_bot();
                }
                "bot|stop" => {
                    self.log("WS: bot|stop received");
                    self.stop_bot();
                }
                _ => {
                    if !self.handle_command(&msg) {
                        self.log(&format!("WS: unhandled dashboard command '{msg}'"));
                    }
                }
            }
        }
    }

    // -- Serial -----------------------------------------------------------------------

    /// Queue a payload for the Pico (`key|down|x` gets its `hid|` prefix).
    pub fn enqueue_hid(&self, payload: &str) {
        let msg = payload.trim();
        if msg.is_empty() {
            return;
        }
        let parts: Vec<&str> = msg.split('|').collect();
        let cmd = if !msg.starts_with("hid|")
            && parts.len() >= 3
            && ["key", "mouse", "scroll"].contains(&parts[0])
        {
            format!("hid|{msg}")
        } else {
            msg.to_owned()
        };
        let _ = self.serial_tx.send(cmd);
    }

    pub(crate) fn serial_writer(self: Arc<Self>, rx: Receiver<String>) {
        for cmd in rx {
            let link = self.serial.lock().unwrap().clone();
            self.log(&format!("TX: {cmd}"));
            match link {
                Some(l) => {
                    if let Err(e) = l.send(&cmd) {
                        self.log(&format!("ERR: serial write {e}"));
                    }
                }
                None => self.log("ERR: Pico write: no link"),
            }
        }
    }

    pub fn serial_open(&self) -> bool {
        self.serial
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|l| l.is_open())
    }

    pub(crate) fn serial_port(&self) -> String {
        self.config.lock().unwrap().serial_port.clone()
    }

    /// What the Pico says it holds (keys by name, buttons as
    /// `mouse:<name>`), or None when its firmware can't say.
    pub(crate) fn pico_held(&self) -> Result<Option<BTreeSet<String>>, String> {
        self.pico_names(HELD)
    }

    /// The key names the Pico's firmware knows, or None when it can't say.
    pub(crate) fn pico_keys(&self) -> Result<Option<BTreeSet<String>>, String> {
        self.pico_names(KEYS)
    }

    fn pico_names(&self, query: &str) -> Result<Option<BTreeSet<String>>, String> {
        let link = self.serial_link().ok_or("no Pico link")?;
        match link.query(query, Duration::from_millis(1500)) {
            Ok(data) => Ok(Some(parse_names(&data))),
            Err(SendError::Rejected) => Ok(None),
            Err(e) => Err(e.to_string()),
        }
    }

    /// Keys a run with `cfg` would press that the Pico can't: checked
    /// against its own list when it can say, else this host's.
    pub(crate) fn unpressable_keys(&self, cfg: &BotConfig) -> Vec<String> {
        let known = match self.pico_keys() {
            Ok(Some(keys)) => keys,
            _ => PICO_KEYS.iter().map(|k| k.to_string()).collect(),
        };
        keys::unpressable(cfg, &known)
    }

    /// On connect: warn when the firmware on the Pico knows other keys
    /// than this host (an old firmware build).
    fn check_key_map(&self) {
        let Some(link) = self.serial_link() else {
            return;
        };
        if !link.wait_ready(Duration::from_secs(3)) {
            return;
        }
        let pico = match self.pico_keys() {
            Ok(Some(pico)) => pico,
            Ok(None) => {
                self.bus.emit_level(
                    "remote",
                    "the Pico's firmware predates key-map checks — flash the current firmware (firmware/phase-e/k75)",
                    "warn",
                );
                return;
            }
            Err(_) => return,
        };
        let ours: BTreeSet<String> = PICO_KEYS.iter().map(|k| k.to_string()).collect();
        let list = |v: Vec<&String>| {
            if v.is_empty() {
                "none".to_owned()
            } else {
                v.into_iter().cloned().collect::<Vec<_>>().join(", ")
            }
        };
        let missing: Vec<&String> = ours.difference(&pico).collect();
        let extra: Vec<&String> = pico.difference(&ours).collect();
        if missing.is_empty() && extra.is_empty() {
            self.bus
                .emit_level("remote", "the Pico's key map matches", "debug");
            return;
        }
        self.bus.emit_level(
            "remote",
            &format!(
                "the Pico's key map differs from this host's (missing: {}; extra: {}) — flash the current firmware (firmware/phase-e/k75)",
                list(missing),
                list(extra)
            ),
            "warn",
        );
    }

    /// After a run: let go of whatever the Pico still holds that no
    /// dashboard finger is holding.
    pub(crate) fn release_strays(&self) {
        let Ok(Some(held)) = self.pico_held() else {
            return;
        };
        let fingers = self.clients.held_keys();
        let strays: Vec<String> = held.into_iter().filter(|k| !fingers.contains(k)).collect();
        if strays.is_empty() {
            return;
        }
        self.bus.emit_level(
            "remote",
            &format!(
                "the Pico still held {} after the run — releasing",
                strays.join(", ")
            ),
            "warn",
        );
        let Some(link) = self.serial_link() else {
            return;
        };
        for key in strays {
            let payload = match key.strip_prefix("mouse:") {
                Some(button) => format!("hid|mouse|up|{button}"),
                None => format!("hid|key|up|{key}"),
            };
            let _ = link.send_acked(&payload, Duration::from_millis(1500));
        }
    }

    /// `host|held`: what the Pico holds, as the dashboard's answer.
    fn report_held(&self) {
        match self.pico_held() {
            Ok(Some(held)) if held.is_empty() => self.bus.emit("notify", "The Pico holds no keys."),
            Ok(Some(held)) => self.bus.emit(
                "notify",
                &format!("The Pico holds: {}", held.into_iter().collect::<Vec<_>>().join(", ")),
            ),
            Ok(None) => self.bus.emit(
                "error",
                "This Pico firmware can't report held keys — flash the current firmware (firmware/phase-e/k75).",
            ),
            Err(e) => self.bus.emit("error", &format!("held-key check failed: {e}")),
        }
    }

    /// `host|release_all`: the firmware lets go of every key and button.
    fn release_pico(&self) {
        let sent = self
            .serial_link()
            .ok_or_else(|| "no Pico link".to_owned())
            .and_then(|l| {
                l.send_acked(RELEASE_ALL, Duration::from_millis(1500))
                    .map_err(|e| e.to_string())
            });
        match sent {
            Ok(()) => self
                .bus
                .emit("notify", "Released every key and button on the Pico."),
            Err(e) => self.bus.emit("error", &format!("release failed: {e}")),
        }
    }

    /// Open `port`, keeping the current link until the new one is up.
    pub fn open_serial(self: &Arc<Self>, port: &str) -> bool {
        match self.try_open_serial(port) {
            Ok(()) => true,
            Err(e) => {
                self.log(&format!("Pico connect failed on {port}: {e}"));
                self.bus.emit("status", "Remote: Pico link error");
                false
            }
        }
    }

    fn try_open_serial(self: &Arc<Self>, port: &str) -> Result<(), String> {
        let port = port.trim();
        if port.is_empty() {
            return Err("no port".into());
        }
        if self
            .serial
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|l| l.port_name == port && l.is_open())
        {
            return Ok(());
        }
        let link = SerialLink::open_spec(port).map_err(|e| e.to_string())?;
        let bus = self.bus.clone();
        link.on_line(move |line| {
            bus.emit_level("remote", &format!("RX: {line}"), "debug");
        });
        let host = Arc::downgrade(self);
        let name = port.to_owned();
        link.on_lost(move |why| {
            if let Some(h) = host.upgrade() {
                h.serial_lost(&name, why);
            }
        });
        let old = self.serial.lock().unwrap().replace(Arc::new(link));
        drop(old);
        self.log(&format!("Pico connected on {port}"));
        self.bus.emit("status", "Remote: Pico connected");
        let h = self.clone();
        std::thread::Builder::new()
            .name("KeyMapCheck".into())
            .spawn(move || h.check_key_map())
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    /// Runs on the dead link's reader thread, so the work (which drops
    /// that link) goes to a thread of its own.
    fn serial_lost(self: &Arc<Self>, port: &str, why: &str) {
        self.bus.emit(
            "remote",
            &format!("ERR: Pico link {port} lost ({why}) — reconnecting"),
        );
        self.bus.emit("status", "Remote: Pico link lost");
        if self.reconnecting.swap(true, Ordering::SeqCst) {
            return;
        }
        let host = Arc::downgrade(self);
        let port = port.to_owned();
        std::thread::Builder::new()
            .name("SerialReconnect".into())
            .spawn(move || {
                reconnect_serial(&host, &port);
                if let Some(h) = host.upgrade() {
                    h.reconnecting.store(false, Ordering::SeqCst);
                }
            })
            .expect("spawn the serial reconnect");
    }

    pub(crate) fn connect_serial(self: &Arc<Self>, port: &str) {
        let port = port.trim();
        if port != "auto" {
            self.finish_serial_connect(port);
            return;
        }
        // A TinyUSB device is found by looking, not probing.
        if let [one] = picobot_io::hid_transport::channels().as_slice() {
            if one.count == 1 {
                let spec = one.spec.clone();
                self.finish_serial_connect(&spec);
                return;
            }
        }
        self.bus.emit("host", "probing serial ports…");
        // COM ports are exclusive: skip the one we already hold.
        let current = self.serial_open().then(|| self.serial_port());
        let h = self.clone();
        std::thread::Builder::new()
            .name("PortProbe".into())
            .spawn(move || {
                match discover_data_port(current.as_deref(), Duration::from_millis(1500)) {
                    Some(found) => h.finish_serial_connect(&found),
                    None => {
                        match current {
                            Some(c) => h.bus.emit(
                                "host",
                                &format!("no other Pico port found — staying on {c}"),
                            ),
                            None => h.bus.emit("error", "no Pico DATA port discovered"),
                        };
                        h.send_host_state();
                    }
                }
            })
            .expect("spawn the port probe");
    }

    pub(crate) fn finish_serial_connect(self: &Arc<Self>, port: &str) {
        if self.open_serial(port) {
            self.config.lock().unwrap().serial_port = port.to_owned();
            self.save_config();
            self.bus.emit("host", &format!("Pico link: {port}"));
        } else {
            self.bus
                .emit("error", &format!("Pico connect failed: {port}"));
        }
        self.send_host_state();
    }

    // -- Bot and measurement -----------------------------------------------------------

    pub(crate) fn slot_running(slot: &Mutex<Option<BotRun>>) -> bool {
        slot.lock().unwrap().as_ref().is_some_and(BotRun::running)
    }

    pub fn is_bot_running(&self) -> bool {
        Host::slot_running(&self.bot)
    }

    pub(crate) fn is_measuring(&self) -> bool {
        Host::slot_running(&self.measurer)
    }

    pub fn send_bot_state(&self, running: bool) {
        self.clients
            .broadcast(&dash(json!({"event": "bot", "running": running})));
    }

    pub(crate) fn start_bot(self: &Arc<Self>) {
        if self.is_bot_running() {
            return;
        }
        if self.is_measuring() {
            self.bus
                .emit("error", "stop measuring before starting the bot");
            return;
        }
        match spawn_bot(self) {
            Ok(run) => *self.bot.lock().unwrap() = Some(run),
            Err(e) => {
                self.bus.emit("error", &format!("Bot failed to start: {e}"));
                self.send_bot_state(false);
            }
        }
    }

    /// Stop a run and wait for its thread (sleeps wake on the stop).
    pub(crate) fn stop_slot(slot: &Mutex<Option<BotRun>>) {
        let run = slot.lock().unwrap().take();
        if let Some(run) = run {
            run.stop.set();
            let _ = run.thread.join();
        }
    }

    pub(crate) fn stop_bot(&self) {
        Host::stop_slot(&self.bot);
    }

    pub fn shutdown(&self) {
        Host::stop_slot(&self.bot);
        Host::stop_slot(&self.measurer);
        if let Err(e) = self.reach.lock().unwrap().save(true) {
            eprintln!("saving reach failed: {e}");
        }
        self.drop_feed();
    }

    pub(crate) fn measure_start(self: &Arc<Self>, mode: Mode, only: Option<String>) {
        if self.is_bot_running() {
            self.bus
                .emit("error", "stop the bot before measuring moves");
            return;
        }
        if self.is_measuring() {
            self.bus.emit("measure", "already measuring");
            self.send_measure(None);
            return;
        }
        match spawn_measure(self, mode, only) {
            Ok(run) => *self.measurer.lock().unwrap() = Some(run),
            Err(e) => self.bus.emit("error", &e),
        }
    }

    /// The `measure` event: the given status, else the last one.
    pub fn send_measure(&self, status: Option<MeasureStatus>) {
        let ended = status.as_ref().is_some_and(|st| !st.running);
        let st = match status {
            Some(st) => {
                *self.last_measure.lock().unwrap() = Some(st.clone());
                st
            }
            None => self
                .last_measure
                .lock()
                .unwrap()
                .clone()
                .unwrap_or_else(|| MeasureStatus {
                    running: false,
                    current: None,
                    plan: plan_for(&self.bot_config.lock().unwrap())
                        .iter()
                        .map(|m| m.to_string())
                        .collect(),
                    results: Map::new(),
                    mode: Mode::Moves,
                    only: None,
                    profile: Vec::new(),
                    profiles: self.reach.lock().unwrap().profiles.clone(),
                }),
        };
        let mut payload = serde_json::to_value(&st).unwrap_or_default();
        let mut o = Map::new();
        o.insert("event".into(), "measure".into());
        if let Some(m) = payload.as_object_mut() {
            o.extend(std::mem::take(m));
        }
        self.clients.broadcast(&dash(Value::Object(o)));
        if ended {
            self.send_class(); // the measured-moves count changed
        }
    }

    // -- What the bot thread shares ----------------------------------------------------

    pub fn serial_link(&self) -> Option<Arc<SerialLink>> {
        self.serial.lock().unwrap().clone().filter(|l| l.is_open())
    }

    pub fn reach_clone(&self) -> ReachModel {
        self.reach.lock().unwrap().clone()
    }

    pub fn put_reach(&self, reach: ReachModel) {
        *self.reach.lock().unwrap() = reach;
    }

    pub fn set_bot_viz(&self, viz: Option<Viz>) {
        *self.bot_viz.lock().unwrap() = viz;
    }

    pub fn bot_viz(&self) -> Option<Viz> {
        self.bot_viz.lock().unwrap().clone()
    }

    pub fn take_pending_skills(&self) -> Option<Vec<Skill>> {
        self.pending_skills.lock().unwrap().take()
    }

    pub fn config_version(&self) -> u64 {
        self.config_version.load(Ordering::Relaxed)
    }

    pub fn map_edits(&self) -> u64 {
        self.map_edits.load(Ordering::Relaxed)
    }

    /// Re-send `maps` for a new fit sample or anchor stat, at most every 5s.
    pub(crate) fn push_maps_throttled(&self) {
        let now = monotonic();
        let due = {
            let mut t = self.maps_pushed.lock().unwrap();
            let due = now - *t >= 5.0;
            if due {
                *t = now;
            }
            due
        };
        if due {
            self.send_maps();
        }
    }

    pub fn observe_fit(&self, entry: Option<&MapEntry>, wh: Option<(f64, f64)>, pos: (f64, f64)) {
        let segs: Vec<[f64; 4]> = entry.and_then(|e| e.platforms.clone()).unwrap_or_default();
        let name = entry.map(|e| e.name.as_str());
        let changed = self
            .platfit
            .lock()
            .unwrap()
            .observe(name, &segs, wh, Some(pos), monotonic());
        if changed {
            self.push_maps_throttled();
        }
    }

    pub fn anchor_stat(&self, map: Option<&str>, kind: &str, anchor: &str, why: &str) {
        let changed = {
            let mut st = self.anchor_stats.lock().unwrap();
            match kind {
                "visit" => st.visit(map, anchor, epoch()),
                "miss" => st.miss(map, anchor),
                _ => st.skip(map, anchor, why),
            }
        };
        if changed {
            self.push_maps_throttled();
        }
    }

    /// Write a map file and re-resolve (the bot re-reads its entry).
    pub fn save_entry(&self, entry: MapEntry) {
        let saved = self.maps.lock().unwrap().save(entry);
        if let Err(e) = saved {
            self.bus
                .emit("error", &format!("saving the map failed: {e}"));
            return;
        }
        self.map_edits.fetch_add(1, Ordering::Relaxed);
        for n in self.identity.refresh() {
            self.bus.emit("map", &n);
        }
        self.send_maps();
    }

    /// A hazard alert (a hazard pause, a rune): the event log, and Telegram
    /// when configured. Health checks (watchdog, heartbeat, stops, crashes,
    /// a lost serial port) only reach the log.
    pub fn notify(&self, msg: &str) {
        self.bus.emit("notify", msg);
        self.telegram.send_async(msg);
    }

    /// `host|snapshot`: the whole game window to `debug/frames/*_snapshot/`.
    fn snapshot(&self) {
        let title = self.window_title();
        let Some(img) = crate::feed::Eyes::open(&title).and_then(|mut e| e.window_img()) else {
            self.bus
                .emit("error", &format!("Save window: can't capture \"{title}\""));
            return;
        };
        let viz = self.bot_viz();
        let meta = json!({
            "window": title,
            "size": [img.width, img.height],
            "map": self.identity.entry().map(|e| e.name),
            "bot_state": viz.as_ref().map(|v| v.state.clone()),
            "hazard": viz.and_then(|v| v.hazard),
        });
        match crate::evidence::snapshot(Path::new("debug/frames"), &img, &meta) {
            Ok(dir) => self.bus.emit(
                "notify",
                &format!(
                    "Window saved ({}x{}) → {}",
                    img.width,
                    img.height,
                    dir.display()
                ),
            ),
            Err(e) => self.bus.emit("error", &format!("Save window failed: {e}")),
        }
    }

    // -- Commands ---------------------------------------------------------------------

    /// Run one dashboard command; false when it isn't one.
    pub fn handle_command(self: &Arc<Self>, msg: &str) -> bool {
        let arg = |n: usize| msg.splitn(n + 1, '|').nth(n).unwrap_or("").to_owned();
        match msg {
            "events|history" => self.send_history(),
            "config|get" => self.send_config(),
            "host|state" => self.send_host_state(),
            "host|snapshot" => self.snapshot(),
            "host|held" => self.report_held(),
            "host|release_all" => self.release_pico(),
            _ if msg.starts_with("host|serial|") => self.connect_serial(&arg(2)),
            _ if msg.starts_with("host|window|") => self.set_window(&arg(2)),
            _ if msg.starts_with("dash|fps|") => self.set_fps(&arg(2)),
            _ if msg.starts_with("dash|view|") => self.set_view(&arg(2)),
            "map|list" => self.send_maps(),
            _ if msg.starts_with("map|set|") => self.set_map(&arg(2)),
            "measure|start" => self.measure_start(Mode::Moves, None),
            _ if msg.starts_with("measure|start|") => {
                self.measure_start(Mode::Moves, Some(arg(2).trim().to_owned()))
            }
            "measure|profile|up_flash" => self.measure_start(Mode::UpFlashProfile, None),
            "measure|profile|walk" => self.measure_start(Mode::WalkTaps, None),
            "measure|profile|effects" => self.measure_start(Mode::SkillEffects, None),
            _ if msg.starts_with("measure|effect|") => {
                self.measure_start(Mode::SkillEffects, Some(arg(2).trim().to_owned()))
            }
            "measure|stop" => Host::stop_slot(&self.measurer),
            "measure|status" => self.send_measure(None),
            _ => return self.handle_edit(msg),
        }
        true
    }

    pub(crate) fn send_history(&self) {
        let items: Vec<Value> = self
            .bus
            .history()
            .into_iter()
            .filter(|e| e["kind"] != "calstat")
            .collect();
        self.clients
            .broadcast(&dash(json!({"event": "history", "items": items})));
    }

    pub fn send_config(&self) {
        let mut data = self.bot_config.lock().unwrap().snapshot();
        data["view_fps"] = self.config.lock().unwrap().view_fps.into();
        self.clients
            .broadcast(&dash(json!({"event": "config", "config": data})));
    }

    pub fn send_host_state(&self) {
        let mut ports: Vec<Value> = picobot_io::hid_transport::channels()
            .into_iter()
            .map(|c| {
                let note = if c.count > 1 {
                    " · more than one device: unplug the real keyboard"
                } else {
                    ""
                };
                json!({"device": c.spec, "desc": format!("{} · HID channel{note}", c.product)})
            })
            .collect();
        ports.extend(
            list_ports()
                .into_iter()
                .map(|(d, desc)| json!({"device": d, "desc": desc})),
        );
        let mut windows: Vec<String> = picobot_io::window::list_windows()
            .into_iter()
            .map(|(_, t)| t.trim().to_owned())
            .filter(|t| !t.is_empty())
            .collect();
        windows.sort();
        windows.dedup();
        let serial = self.serial_port();
        let payload = json!({
            "event": "host",
            "serial": if serial.is_empty() { Value::Null } else { serial.into() },
            "serial_open": self.serial_open(),
            "window": *self.window_title.lock().unwrap(),
            "ports": ports,
            "windows": windows,
        });
        self.clients.broadcast(&dash(payload));
    }

    pub(crate) fn set_window(&self, title: &str) {
        let title = title.trim();
        if title.is_empty() || *self.window_title.lock().unwrap() == title {
            return;
        }
        if self.is_bot_running() || self.is_measuring() {
            self.bus
                .emit("error", "stop the bot before switching windows");
            self.send_host_state();
            return;
        }
        *self.window_title.lock().unwrap() = title.to_owned();
        self.config.lock().unwrap().default_target_window = title.to_owned();
        self.save_config();
        self.drop_feed();
        self.bus.emit("host", &format!("window: {title}"));
        self.send_host_state();
    }

    pub(crate) fn set_fps(&self, payload: &str) {
        let Ok(fps) = payload.trim().parse::<f64>() else {
            self.bus.emit("error", &format!("invalid fps: {payload:?}"));
            return;
        };
        let fps = fps.clamp(1.0, 30.0);
        self.config.lock().unwrap().view_fps = fps;
        self.save_config();
        self.bus.emit("host", &format!("view fps: {fps}"));
        self.send_config();
    }

    pub fn view_fps(&self) -> f64 {
        self.config.lock().unwrap().view_fps
    }

    // -- Vision feed and the view ----------------------------------------------------

    pub fn window_title(&self) -> String {
        self.window_title.lock().unwrap().clone()
    }

    pub fn bot_config(&self) -> BotConfig {
        self.bot_config.lock().unwrap().clone()
    }

    pub fn view_mode(&self) -> String {
        self.view_mode.lock().unwrap().clone()
    }

    pub(crate) fn set_view(&self, mode: &str) {
        if ["minimap", "window", "title"].contains(&mode) {
            *self.view_mode.lock().unwrap() = mode.to_owned();
        }
    }

    /// The shared feed, started on first use; None without the game.
    pub fn get_feed(&self) -> Option<Arc<Feed>> {
        let mut slot = self.feed.lock().unwrap();
        if slot.is_none() {
            let title = self.window_title();
            let cfg = self.bot_config();
            *slot = Feed::start(
                &title,
                &cfg,
                self.identity.clone(),
                self.bus.clone(),
                self.ocr_tx.lock().unwrap().clone(),
            )
            .map(Arc::new);
        }
        slot.clone()
    }

    /// Close the feed; the next `get_feed` builds a fresh one.
    pub(crate) fn drop_feed(&self) {
        let old = self.feed.lock().unwrap().take();
        drop(old);
        self.eyes_reset.store(true, Ordering::Relaxed);
    }

    pub fn take_eyes_reset(&self) -> bool {
        self.eyes_reset.swap(false, Ordering::Relaxed)
    }

    /// Captures failed for seconds straight (streamer thread): rebuild the
    /// feed unless the bot runs on it.
    pub fn stream_stalled(&self, why: &str) {
        let busy = self.is_bot_running() || self.is_measuring();
        let now = monotonic();
        let warn = {
            let mut t = self.stall_warned.lock().unwrap();
            let due = now - *t >= 30.0;
            if due {
                *t = now;
            }
            due
        };
        if warn {
            let tail = if busy {
                ""
            } else {
                " — rebuilding the vision feed"
            };
            self.bus.emit_level(
                "warn",
                &format!("live view: capture failing ({why}){tail}"),
                "warn",
            );
        }
        if !busy {
            self.drop_feed();
        }
    }

    pub(crate) fn live_region(&self) -> Option<Region> {
        self.feed
            .lock()
            .unwrap()
            .as_ref()
            .and_then(|f| f.analyzer.region())
    }

    pub(crate) fn graph_options(cfg: &BotConfig) -> GraphOptions {
        GraphOptions {
            rope_penalty: cfg.rope_penalty,
            allow_flash: cfg.class_travel == ClassTravel::Flash && cfg.flash_jump_enabled,
            allow_teleport: cfg.class_travel == ClassTravel::Teleport && cfg.teleport_key.is_some(),
            ..Default::default()
        }
    }

    pub(crate) fn nav_graph(
        &self,
        entry: Option<&MapEntry>,
        wh: Option<(f64, f64)>,
    ) -> Option<Arc<NavGraph>> {
        let opts = Host::graph_options(&self.bot_config.lock().unwrap());
        let reach = self.reach.lock().unwrap();
        self.graphs.lock().unwrap().get(entry, wh, &reach, opts)
    }

    /// The overlay and frame metadata of the resolved map: platforms,
    /// ropes, anchors, and identity (`map`, `map_via`, `map_conf`,
    /// `map_title`, `no_rotation`).
    pub fn map_meta(&self, region: Option<Region>) -> (Overlay, Map<String, Value>) {
        if self.maps_sent.load(Ordering::Relaxed) != self.identity.version() + 1 {
            self.send_maps();
        }
        let entry = self.identity.entry();
        let res = self.identity.current();
        let wh = region
            .filter(|r| r.2 > 0 && r.3 > 0)
            .map(|r| (r.2 as f64, r.3 as f64));
        let mut o = Overlay::default();
        if let (Some(e), Some((w, h))) = (&entry, wh) {
            o.anchors = e
                .rotation
                .anchors
                .iter()
                .map(|a| {
                    (
                        resolve_coord(a.x, w as i64) as f64,
                        resolve_coord(a.y, h as i64) as f64,
                    )
                })
                .collect();
            o.ropes = e
                .ropes
                .iter()
                .flatten()
                .map(|s| {
                    [
                        (s[0] * w).round(),
                        (s[1] * h).round(),
                        (s[2] * w).round(),
                        (s[3] * h).round(),
                    ]
                })
                .collect();
        }
        if let Some(g) = self.nav_graph(entry.as_ref(), wh) {
            o.platforms = g
                .platforms
                .iter()
                .map(|p| [p.x0, p.y0, p.x1, p.y1])
                .collect();
            if let Some((legs, until)) = self.nav_preview.lock().unwrap().clone() {
                if monotonic() < until {
                    o.nav_route = legs;
                }
            }
            if self.nav_show.load(Ordering::Relaxed) {
                o.nav_edges = g
                    .transfer_legs()
                    .iter()
                    .map(|l| (l.kind.as_str().to_owned(), l.x0, l.y0, l.x1, l.y1))
                    .collect();
            }
        }
        let rotation_empty = match &entry {
            Some(e) => e.rotation.anchors.is_empty(),
            None => self.bot_config.lock().unwrap().rotation.anchors.is_empty(),
        };
        let conf = entry
            .as_ref()
            .filter(|e| res.title_map.as_deref() == Some(e.name.as_str()))
            .map(|_| res.score);
        let mut meta = Map::new();
        meta.insert("map".into(), entry.as_ref().map(|e| e.name.clone()).into());
        meta.insert("map_via".into(), res.via.into());
        meta.insert("map_conf".into(), conf.into());
        meta.insert("map_title".into(), res.title.into());
        meta.insert("no_rotation".into(), rotation_empty.into());
        (o, meta)
    }

    // -- Maps -------------------------------------------------------------------------

    pub(crate) fn rope_rows(&self, entry: Option<&MapEntry>, region: Option<Region>) -> Vec<Value> {
        let (Some(e), Some(r)) = (entry, region) else {
            return Vec::new();
        };
        let (w, h) = (r.2 as f64, r.3 as f64);
        let mut rows: Vec<(i64, i64, Value)> = e
            .ropes
            .iter()
            .flatten()
            .map(|s| {
                let x = ((s[0] + s[2]) / 2.0 * w).round() as i64;
                let top = (s[1].min(s[3]) * h).round() as i64;
                let bottom = (s[1].max(s[3]) * h).round() as i64;
                (
                    x,
                    top,
                    json!({"key": SegKey::of(s).text(), "x": x, "top": top, "bottom": bottom}),
                )
            })
            .collect();
        rows.sort_by_key(|r| (r.0, r.1));
        rows.into_iter().map(|r| r.2).collect()
    }

    pub fn send_maps(&self) {
        self.maps_sent
            .store(self.identity.version() + 1, Ordering::Relaxed);
        let res = self.identity.current();
        let entry = self.identity.entry();
        let region = self.live_region();
        let wh = region.map(|r| (r.2 as f64, r.3 as f64));
        let names = self.maps.lock().unwrap().names();
        let name = entry.as_ref().map(|e| e.name.as_str());
        let segs: Vec<[f64; 4]> = entry
            .as_ref()
            .and_then(|e| e.platforms.clone())
            .unwrap_or_default();
        let fit = self.platfit.lock().unwrap().summary(name, &segs, wh);
        let anchors: Vec<String> = entry
            .iter()
            .flat_map(|e| e.rotation.anchors.iter().map(|a| a.name.clone()))
            .collect();
        let stats = self.anchor_stats.lock().unwrap().rows(name, &anchors);
        let payload = json!({
            "event": "maps",
            "maps": names,
            "active": self.identity.pin(),
            "detected": res.name,
            "via": res.via,
            "title": res.title,
            "score": res.title_map.as_ref().map(|_| res.score),
            "reading": self.identity.pending(),
            "platforms_n": segs.len(),
            "anchors_n": anchors.len(),
            "platform_fit": fit,
            "ropes": self.rope_rows(entry.as_ref(), region),
            "recorded_title": entry.as_ref().and_then(|e| e.map_name.clone()),
            "players_rule": PlayerRule::label(entry.as_ref().and_then(|e| e.other_players)),
            "anchor_stats": stats,
        });
        self.clients.broadcast(&dash(payload));
    }

    /// Pin a map ("" = auto-detect) for this run.
    pub(crate) fn set_map(&self, name: &str) {
        let name = name.trim();
        let pin = (!name.is_empty()).then(|| name.to_owned());
        {
            let mut cfg = self.bot_config.lock().unwrap();
            cfg.active_map = pin.clone();
            cfg.auto_select_map = pin.is_none();
        }
        for n in self.identity.set_pin(pin.as_deref()) {
            self.bus.emit("map", &n);
        }
        self.bus.emit(
            "map",
            &format!("active map: {}", pin.as_deref().unwrap_or("auto")),
        );
        self.send_maps();
    }

    // -- Title OCR --------------------------------------------------------------------

    /// Read title bands off the map monitor, one at a time (~40 ms each).
    fn title_worker(self: Arc<Self>, mut reader: TitleReader, rx: Receiver<TitleJob>) {
        for job in rx {
            let text = reader.read(&job.band);
            let before = self.identity.version();
            let notes = self.identity.finish_read(job.gen, text, monotonic());
            for n in notes {
                self.bus.emit("map", &n);
            }
            if self.identity.version() != before {
                self.send_maps();
            }
        }
    }

    /// Ask for a title read (startup, re-detect).
    pub fn request_title(&self) {
        for n in self.identity.request(false) {
            self.bus.emit("map", &n);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::DASHBOARD_PREFIXES;

    /// A command the dashboard handles but whose prefix is missing from
    /// `DASHBOARD_PREFIXES` would be sent to the Pico as HID input.
    #[test]
    fn every_dashboard_command_prefix_is_routed() {
        let mut found = Vec::new();
        for src in [include_str!("commands.rs"), include_str!("host.rs")] {
            for line in src.lines() {
                let quoted = line
                    .split("starts_with(\"")
                    .skip(1)
                    .map(|rest| rest.split('"').next().unwrap_or(""))
                    .chain(
                        line.trim_start()
                            .strip_prefix('"')
                            .filter(|_| line.contains("\" =>"))
                            .and_then(|rest| rest.split('"').next()),
                    );
                for q in quoted {
                    if let Some((head, _)) = q.split_once('|') {
                        found.push(format!("{head}|"));
                    }
                }
            }
        }
        found.sort();
        found.dedup();
        assert!(found.len() >= 10, "scan found too little: {found:?}");
        let missing: Vec<_> = found
            .iter()
            .filter(|p| p.as_str() != "hid|" && !DASHBOARD_PREFIXES.contains(&p.as_str()))
            .collect();
        assert!(
            missing.is_empty(),
            "not routed as dashboard commands: {missing:?}"
        );
    }
}

#[cfg(test)]
mod reconnect_tests {
    use super::*;

    fn names(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn the_lost_port_is_tried_alone_once_it_is_back() {
        let mut seen: HashSet<String> = names(&["COM1"]).into_iter().collect();
        assert_eq!(
            reconnect_candidates("COM6", &names(&["COM1"]), &mut seen),
            Vec::<String>::new()
        );
        assert_eq!(
            reconnect_candidates("COM6", &names(&["COM1", "COM6"]), &mut seen),
            ["COM6"]
        );
    }

    #[test]
    fn only_new_ports_are_probed_and_each_once() {
        let mut seen: HashSet<String> = names(&["COM1"]).into_iter().collect();
        let now = names(&["COM1", "COM9", "COM10"]);
        assert_eq!(
            reconnect_candidates("COM6", &now, &mut seen),
            ["COM9", "COM10"]
        );
        assert!(reconnect_candidates("COM6", &now, &mut seen).is_empty());
        // COM9 goes away and comes back: new again.
        reconnect_candidates("COM6", &names(&["COM1", "COM10"]), &mut seen);
        assert_eq!(reconnect_candidates("COM6", &now, &mut seen), ["COM9"]);
    }

    #[test]
    fn held_replies_split_on_bars_not_commas() {
        let held = parse_names("left|,|page up|mouse:left");
        assert_eq!(
            held.into_iter().collect::<Vec<_>>(),
            [",", "left", "mouse:left", "page up"]
        );
        assert!(parse_names("").is_empty());
    }
}
