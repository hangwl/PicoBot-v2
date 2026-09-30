//! The host: shared state, the serial writer, and the dashboard command
//! worker. WebSocket tasks only route messages; anything that can block
//! (files, serial handshakes, joining threads) runs on the
//! `DashboardCommands` thread, in order.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU16, AtomicU64, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use picobot_core::anchor_stats::AnchorStats;
use picobot_core::bot::measure::plan_for;
use picobot_core::bot::{LegViz, MeasureStatus, Mode, Viz};
use picobot_core::config::{AppConfig, BotConfig, ClassTravel};
use picobot_core::identity::MapIdentity;
use picobot_core::maps::{MapEntry, MapStore, PlayerRule};
use picobot_core::navgraph::{GraphCache, GraphOptions, NavGraph};
use picobot_core::platform_fit::{PlatformFit, SegKey};
use picobot_core::reach::{base_reach, ReachModel};
use picobot_core::rotation::resolve_coord;
use picobot_core::skills::Skill;
use picobot_core::timing::monotonic;
use picobot_core::vision::Region;
use picobot_io::ocr::{find_model, TitleReader};
use picobot_io::serial::{discover_data_port, list_ports, SerialLink};
use serde_json::{json, Map, Value};

use crate::botbody::{epoch, spawn_bot, spawn_measure, BotRun};
use crate::bus::Bus;
use crate::clients::Clients;
use crate::commands::UndoStep;
use crate::feed::{Feed, TitleJob};
use crate::frames::Overlay;
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
        self.enqueue_hid(msg);
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
                None => self.log("ERR: serial write: no serial port"),
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

    /// Open `port`, keeping the current link until the new one is up.
    pub fn open_serial(&self, port: &str) -> bool {
        let port = port.trim();
        if port.is_empty() {
            return false;
        }
        if self
            .serial
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|l| l.port_name == port && l.is_open())
        {
            return true;
        }
        let link = match SerialLink::open(port) {
            Ok(l) => l,
            Err(e) => {
                self.log(&format!("serial connect failed on {port}: {e}"));
                self.bus.emit("status", "Remote: Serial error");
                return false;
            }
        };
        let bus = self.bus.clone();
        link.on_line(move |line| {
            bus.emit_level("remote", &format!("RX: {line}"), "debug");
        });
        let bus = self.bus.clone();
        let telegram = self.telegram.clone();
        link.on_lost(move |why| {
            bus.emit(
                "remote",
                &format!("ERR: serial port lost ({why}) — reconnect it in Connection"),
            );
            bus.emit("status", "Remote: Serial lost");
            telegram.send_async(&format!("Serial port lost ({why})"));
        });
        let old = self.serial.lock().unwrap().replace(Arc::new(link));
        drop(old);
        self.log(&format!("serial connected on {port}"));
        self.bus.emit("status", "Remote: Serial connected");
        true
    }

    pub(crate) fn connect_serial(self: &Arc<Self>, port: &str) {
        let port = port.trim();
        if port != "auto" {
            self.finish_serial_connect(port);
            return;
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

    pub(crate) fn finish_serial_connect(&self, port: &str) {
        if self.open_serial(port) {
            self.config.lock().unwrap().serial_port = port.to_owned();
            self.save_config();
            self.bus.emit("host", &format!("serial: {port}"));
        } else {
            self.bus
                .emit("error", &format!("serial connect failed: {port}"));
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
                self.bus.emit("error", &e);
                self.notify(&format!("Bot failed to start: {e}"));
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

    /// A safety alert: the event log, and Telegram when configured.
    pub fn notify(&self, msg: &str) {
        self.bus.emit("notify", msg);
        self.telegram.send_async(msg);
    }

    // -- Commands ---------------------------------------------------------------------

    /// Run one dashboard command; false when it isn't one.
    pub fn handle_command(self: &Arc<Self>, msg: &str) -> bool {
        let arg = |n: usize| msg.splitn(n + 1, '|').nth(n).unwrap_or("").to_owned();
        match msg {
            "events|history" => self.send_history(),
            "config|get" => self.send_config(),
            "host|state" => self.send_host_state(),
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
        let ports: Vec<Value> = list_ports()
            .into_iter()
            .map(|(d, desc)| json!({"device": d, "desc": desc}))
            .collect();
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
