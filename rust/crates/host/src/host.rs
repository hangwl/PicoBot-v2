//! The host: shared state, the serial writer, and the dashboard command
//! worker. WebSocket tasks only route messages; anything that can block
//! (files, serial handshakes, joining threads) runs on the
//! `DashboardCommands` thread, in order.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU16, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use picobot_core::config::{AppConfig, BotConfig};
use picobot_io::serial::{discover_data_port, list_ports, SerialLink};
use serde_json::{json, Value};

use crate::bus::Bus;
use crate::clients::Clients;

/// WS message prefixes handled by the host; anything else is HID input
/// for the Pico.
const DASHBOARD_PREFIXES: [&str; 13] = [
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
];

pub struct Host {
    pub bus: Arc<Bus>,
    pub clients: Arc<Clients>,
    pub root: PathBuf,
    config: Mutex<AppConfig>,
    bot_config: Mutex<BotConfig>,
    window_title: Mutex<String>,
    serial: Mutex<Option<Arc<SerialLink>>>,
    serial_tx: Sender<String>,
    jobs: Mutex<Option<Sender<String>>>,
    ws_port: AtomicU16,
    http_port: AtomicU16,
}

fn dash(payload: Value) -> String {
    format!("dash|{payload}")
}

impl Host {
    pub fn new(root: PathBuf, config: AppConfig, window_title: String) -> Arc<Host> {
        let bus = Arc::new(Bus::default());
        let clients = Arc::new(Clients::default());
        let c = clients.clone();
        bus.subscribe(move |e| {
            let mut p = e.clone();
            p["event"] = "evt".into();
            // `event` first, like the Python payload.
            let mut o = serde_json::Map::new();
            o.insert("event".into(), "evt".into());
            for (k, v) in p
                .as_object()
                .into_iter()
                .flatten()
                .filter(|(k, _)| *k != "event")
            {
                o.insert(k.clone(), v.clone());
            }
            c.broadcast(&dash(Value::Object(o)));
        });
        let bot_config = BotConfig::from_json(&config.bot).unwrap_or_else(|e| {
            bus.emit("error", &format!("config: {e} — using defaults"));
            BotConfig::default()
        });
        let (serial_tx, serial_rx) = channel();
        let host = Arc::new(Host {
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
    pub fn http_port(&self) -> u16 {
        self.http_port.load(Ordering::Relaxed)
    }
    pub fn set_http_port(&self, p: u16) {
        self.http_port.store(p, Ordering::Relaxed);
    }

    fn log(&self, msg: &str) {
        let level = if msg.starts_with("TX:") || msg.starts_with("RX:") {
            "debug"
        } else {
            "info"
        };
        self.bus.emit_level("remote", msg, level);
    }

    fn save_config(&self) {
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

    fn submit(&self, msg: &str) {
        if let Some(tx) = self.jobs.lock().unwrap().as_ref() {
            let _ = tx.send(msg.to_owned());
        }
    }

    fn command_loop(self: Arc<Self>, rx: Receiver<String>) {
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

    fn serial_writer(self: Arc<Self>, rx: Receiver<String>) {
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

    fn serial_port(&self) -> String {
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
        link.on_lost(move |why| {
            bus.emit(
                "remote",
                &format!("ERR: serial port lost ({why}) — reconnect it in Connection"),
            );
            bus.emit("status", "Remote: Serial lost");
        });
        let old = self.serial.lock().unwrap().replace(Arc::new(link));
        drop(old);
        self.log(&format!("serial connected on {port}"));
        self.bus.emit("status", "Remote: Serial connected");
        true
    }

    fn connect_serial(self: &Arc<Self>, port: &str) {
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

    fn finish_serial_connect(&self, port: &str) {
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

    // -- Bot lifecycle (wired in M7c) ------------------------------------------------

    pub fn is_bot_running(&self) -> bool {
        false
    }

    fn start_bot(&self) {
        self.bus
            .emit("error", "the Rust host can't run the bot yet");
        self.clients
            .broadcast(&dash(json!({"event": "bot", "running": false})));
    }

    fn stop_bot(&self) {}

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
            _ => return false,
        }
        true
    }

    fn send_history(&self) {
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

    fn set_window(&self, title: &str) {
        let title = title.trim();
        if title.is_empty() || *self.window_title.lock().unwrap() == title {
            return;
        }
        if self.is_bot_running() {
            self.bus
                .emit("error", "stop the bot before switching windows");
            self.send_host_state();
            return;
        }
        *self.window_title.lock().unwrap() = title.to_owned();
        self.config.lock().unwrap().default_target_window = title.to_owned();
        self.save_config();
        self.bus.emit("host", &format!("window: {title}"));
        self.send_host_state();
    }

    fn set_fps(&self, payload: &str) {
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
}
