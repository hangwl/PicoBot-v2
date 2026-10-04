//! The serial link to the Pico.
//!
//! Wire protocol (one line each way):
//! - host → Pico: `<seq>:hid|key|down|<name>` (also `up`, `hid|mouse|…`,
//!   `hid|move|dx|dy`, `hid|scroll|dx|dy`); `hello|handshake` (answered with
//!   `PICO_READY`); `ka` keepalive when idle, no reply.
//! - Pico → host: `ACK <seq>` / `NACK <seq>`.
//!
//! Replies are matched by number, so one that arrives after its sender gave
//! up can never be credited to a later command. Firmware that announces
//! `PICO_READY v2` gets numbered commands and keepalives (its watchdog
//! releases every key after 2s of silence); older firmware gets plain
//! commands, and its bare `ACK`s are matched first-in-first-out.
//!
//! One reader thread owns the incoming side: it resolves replies, notes
//! READY lines, sends the keepalive when the link is idle, and reports a
//! port that fails underneath it.

use std::collections::{HashMap, VecDeque};
use std::io::{self, Read, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, SyncSender};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

pub const HANDSHAKE: &str = "hello|handshake";
pub const KEEPALIVE: &str = "ka";
/// Send `ka` when nothing else went out for this long.
pub const KEEPALIVE_EVERY: Duration = Duration::from_millis(400);
const BAUD: u32 = 115_200;
/// Outstanding replies kept for FIFO matching before the oldest is dropped.
const MAX_PENDING: usize = 256;

/// The incoming side of a link: one line at a time.
pub trait LineReader: Send {
    /// The next line without its newline; `Ok(None)` when nothing arrived
    /// within the transport's read timeout (so the reader can do upkeep).
    fn read_line(&mut self) -> io::Result<Option<String>>;
}

/// The outgoing side of a link.
pub trait LineWriter: Send {
    fn write_line(&mut self, line: &str) -> io::Result<()>;
}

/// Why a send didn't get an ACK.
#[derive(Debug, PartialEq, Eq)]
pub enum SendError {
    /// The port isn't open (never opened, closed, or lost).
    Closed,
    /// Writing failed.
    Write(String),
    /// No reply within the timeout.
    Timeout,
    /// The firmware rejected it (unknown key or command).
    Rejected,
}

impl std::fmt::Display for SendError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SendError::Closed => f.write_str("serial port is not open"),
            SendError::Write(e) => write!(f, "serial write failed: {e}"),
            SendError::Timeout => f.write_str("no ACK in time"),
            SendError::Rejected => f.write_str("the Pico rejected the command"),
        }
    }
}

impl std::error::Error for SendError {}

/// Replies not yet received: by number, and in send order (for firmware
/// that answers with a bare ACK).
#[derive(Default)]
struct Pending {
    by_seq: HashMap<u32, SyncSender<Reply>>,
    order: VecDeque<u32>,
}

/// ACK or NACK, and whatever the firmware put after the number.
type Reply = (bool, String);

impl Pending {
    fn resolve(&mut self, seq: Option<u32>, reply: Reply) {
        let seq = match seq {
            Some(s) => {
                self.order.retain(|x| *x != s);
                s
            }
            None => match self.order.pop_front() {
                Some(s) => s,
                None => return,
            },
        };
        if let Some(tx) = self.by_seq.remove(&seq) {
            let _ = tx.try_send(reply); // the sender may have given up
        }
    }

    fn forget(&mut self, seq: u32) {
        self.by_seq.remove(&seq);
        self.order.retain(|x| *x != seq);
    }

    /// Wake every waiter with a failure (the port is gone).
    fn fail_all(&mut self) {
        for (_, tx) in self.by_seq.drain() {
            let _ = tx.try_send((false, String::new()));
        }
        self.order.clear();
    }
}

// Arc, so the reader can clone them out and call them with no lock held.
type LineCallback = Arc<dyn Fn(&str) + Send + Sync>;
type LostCallback = Arc<dyn Fn(&str) + Send + Sync>;

struct Shared {
    writer: Mutex<Option<Box<dyn LineWriter>>>,
    pending: Mutex<Pending>,
    next_seq: Mutex<u32>,
    last_tx: Mutex<Instant>,
    numbered: AtomicBool,
    stop: AtomicBool,
    ready: (Mutex<Option<Instant>>, Condvar),
    on_line: Mutex<Vec<LineCallback>>,
    on_lost: Mutex<Option<LostCallback>>,
}

/// A persistent session with the Pico.
pub struct SerialLink {
    pub port_name: String,
    shared: Arc<Shared>,
    reader: Option<JoinHandle<()>>,
}

impl SerialLink {
    /// Open a real serial port, toggle DTR to wake the firmware's data
    /// mode, send the handshake and start the reader.
    pub fn open(port_name: &str) -> io::Result<Self> {
        let mut port = serialport::new(port_name, BAUD)
            .timeout(Duration::from_millis(200))
            .open()
            .map_err(io::Error::other)?;
        let _ = port.write_data_terminal_ready(false);
        std::thread::sleep(Duration::from_millis(50));
        let _ = port.write_data_terminal_ready(true);
        let _ = port.write_request_to_send(false);
        let reader = port.try_clone().map_err(io::Error::other)?;
        Ok(SerialLink::start(
            port_name,
            Box::new(PortReader {
                port: reader,
                buf: Vec::new(),
            }),
            Box::new(PortWriter { port }),
        ))
    }

    /// A link over any transport (tests use an in-memory firmware).
    pub fn start(
        port_name: &str,
        reader: Box<dyn LineReader>,
        writer: Box<dyn LineWriter>,
    ) -> Self {
        let shared = Arc::new(Shared {
            writer: Mutex::new(Some(writer)),
            pending: Mutex::new(Pending::default()),
            next_seq: Mutex::new(0),
            last_tx: Mutex::new(Instant::now()),
            numbered: AtomicBool::new(false),
            stop: AtomicBool::new(false),
            ready: (Mutex::new(None), Condvar::new()),
            on_line: Mutex::new(Vec::new()),
            on_lost: Mutex::new(None),
        });
        let link_shared = shared.clone();
        let handle = std::thread::Builder::new()
            .name(format!("SerialLink[{port_name}]"))
            .spawn(move || reader_loop(link_shared, reader))
            .expect("spawn the serial reader");
        let link = SerialLink {
            port_name: port_name.into(),
            shared,
            reader: Some(handle),
        };
        let _ = link.write_raw(HANDSHAKE);
        link
    }

    pub fn is_open(&self) -> bool {
        self.shared.writer.lock().unwrap().is_some()
    }

    /// Whether the firmware speaks v2 (numbered commands + watchdog).
    pub fn numbered(&self) -> bool {
        self.shared.numbered.load(Ordering::Relaxed)
    }

    /// Every received line (for logging).
    pub fn on_line(&self, f: impl Fn(&str) + Send + Sync + 'static) {
        self.shared.on_line.lock().unwrap().push(Arc::new(f));
    }

    /// Called (from the reader thread) when the port fails underneath us.
    pub fn on_lost(&self, f: impl Fn(&str) + Send + Sync + 'static) {
        *self.shared.on_lost.lock().unwrap() = Some(Arc::new(f));
    }

    /// Wait for a `PICO_READY` (one seen in the last second counts).
    pub fn wait_ready(&self, timeout: Duration) -> bool {
        let (lock, cv) = &self.shared.ready;
        let fresh = |t: &Option<Instant>| t.is_some_and(|t| t.elapsed() < Duration::from_secs(1));
        let guard = lock.lock().unwrap();
        if fresh(&guard) {
            return true;
        }
        let (guard, _) = cv
            .wait_timeout_while(guard, timeout, |t| !fresh(t))
            .unwrap();
        fresh(&guard)
    }

    fn write_raw(&self, line: &str) -> Result<(), SendError> {
        let mut w = self.shared.writer.lock().unwrap();
        let w = w.as_mut().ok_or(SendError::Closed)?;
        w.write_line(line)
            .map_err(|e| SendError::Write(e.to_string()))?;
        *self.shared.last_tx.lock().unwrap() = Instant::now();
        Ok(())
    }

    /// Send a payload without waiting for its reply.
    pub fn send(&self, payload: &str) -> Result<(), SendError> {
        self.send_inner(payload, None).map(|_| ())
    }

    /// Send a payload and wait up to `timeout` for its ACK.
    pub fn send_acked(&self, payload: &str, timeout: Duration) -> Result<(), SendError> {
        self.send_inner(payload, Some(timeout)).map(|_| ())
    }

    /// Send a payload and return the data its ACK carries (`ACK <seq>
    /// <data>`); needs v2 firmware, older replies carry none.
    pub fn query(&self, payload: &str, timeout: Duration) -> Result<String, SendError> {
        self.send_inner(payload, Some(timeout))
    }

    fn send_inner(&self, payload: &str, wait: Option<Duration>) -> Result<String, SendError> {
        let body = payload.trim_end_matches('\n');
        if body.starts_with("hello") {
            // Handshakes are answered with PICO_READY, never an ACK.
            return self.write_raw(body).map(|()| String::new());
        }
        let (tx, rx): (SyncSender<Reply>, Receiver<Reply>) = sync_channel(1);
        let seq = {
            let mut next = self.shared.next_seq.lock().unwrap();
            *next = *next % 999_999 + 1;
            *next
        };
        {
            // Every command gets an entry: its reply is dropped when nobody
            // waits, and bare-ACK firmware needs one per command in order.
            let mut p = self.shared.pending.lock().unwrap();
            p.by_seq.insert(seq, tx);
            p.order.push_back(seq);
            while p.order.len() > MAX_PENDING {
                if let Some(old) = p.order.pop_front() {
                    p.by_seq.remove(&old);
                }
            }
        }
        let line = if self.numbered() {
            format!("{seq}:{body}")
        } else {
            body.to_owned()
        };
        if let Err(e) = self.write_raw(&line) {
            self.shared.pending.lock().unwrap().forget(seq);
            return Err(e);
        }
        let Some(timeout) = wait else {
            return Ok(String::new());
        };
        let got = rx.recv_timeout(timeout);
        self.shared.pending.lock().unwrap().forget(seq);
        match got {
            Ok((true, data)) => Ok(data),
            Ok((false, _)) if !self.is_open() => Err(SendError::Closed),
            Ok((false, _)) => Err(SendError::Rejected),
            Err(_) => Err(SendError::Timeout),
        }
    }

    pub fn close(&mut self) {
        self.shared.stop.store(true, Ordering::Relaxed);
        self.shared.writer.lock().unwrap().take();
        if let Some(h) = self.reader.take() {
            let _ = h.join();
        }
        self.shared.pending.lock().unwrap().fail_all();
    }
}

impl Drop for SerialLink {
    fn drop(&mut self) {
        self.close();
    }
}

/// `ACK 17 left|x` → (17, (true, "left|x")); bare `NACK` → (None,
/// (false, "")); anything else → None. Data comes only after a number.
fn parse_reply(line: &str) -> Option<(Option<u32>, Reply)> {
    let (word, rest) = line.split_once(' ').unwrap_or((line, ""));
    let ok = match word {
        "ACK" => true,
        "NACK" => false,
        _ => return None,
    };
    let (num, data) = rest.trim().split_once(' ').unwrap_or((rest.trim(), ""));
    let seq = num.parse().ok();
    let data = if seq.is_some() { data.trim() } else { "" };
    Some((seq, (ok, data.to_owned())))
}

fn reader_loop(shared: Arc<Shared>, mut reader: Box<dyn LineReader>) {
    let lost = loop {
        if shared.stop.load(Ordering::Relaxed) {
            break None;
        }
        keepalive(&shared);
        match reader.read_line() {
            Ok(None) => continue,
            Err(e) => break Some(e.to_string()),
            Ok(Some(line)) => {
                let line = line.trim();
                if line.is_empty() {
                    continue;
                }
                if let Some((seq, reply)) = parse_reply(line) {
                    shared.pending.lock().unwrap().resolve(seq, reply);
                } else if line.starts_with("PICO_READY") {
                    shared
                        .numbered
                        .store(line.ends_with(" v2"), Ordering::Relaxed);
                    let (lock, cv) = &shared.ready;
                    *lock.lock().unwrap() = Some(Instant::now());
                    cv.notify_all();
                }
                let callbacks = shared.on_line.lock().unwrap().clone();
                for cb in callbacks {
                    cb(line);
                }
            }
        }
    };
    if let Some(why) = lost {
        // The port died (unplugged, driver reset): report closed and fail
        // every waiting sender now instead of at its timeout.
        shared.writer.lock().unwrap().take();
        shared.pending.lock().unwrap().fail_all();
        let cb = shared.on_lost.lock().unwrap().clone();
        if let Some(cb) = cb {
            cb(&why);
        }
    }
}

fn keepalive(shared: &Shared) {
    if !shared.numbered.load(Ordering::Relaxed) {
        return; // older firmware would NACK it
    }
    if shared.last_tx.lock().unwrap().elapsed() < KEEPALIVE_EVERY {
        return;
    }
    if let Some(w) = shared.writer.lock().unwrap().as_mut() {
        if w.write_line(KEEPALIVE).is_ok() {
            *shared.last_tx.lock().unwrap() = Instant::now();
        }
    }
}

struct PortReader {
    port: Box<dyn serialport::SerialPort>,
    buf: Vec<u8>,
}

/// How long one `read_line` waits for input before returning (the reader
/// loop then checks for a stop and sends keepalives).
const READ_WAIT: Duration = Duration::from_millis(200);
/// Poll interval while no input is waiting.
const POLL: Duration = Duration::from_millis(1);

impl LineReader for PortReader {
    /// Reads only bytes already waiting. The writer shares this handle,
    /// and Windows serialises synchronous I/O on it: a read left blocking
    /// for input would hold every write until it timed out.
    fn read_line(&mut self) -> io::Result<Option<String>> {
        let since = Instant::now();
        loop {
            if let Some(i) = self.buf.iter().position(|b| *b == b'\n') {
                let line: Vec<u8> = self.buf.drain(..=i).collect();
                return Ok(Some(String::from_utf8_lossy(&line).trim_end().to_owned()));
            }
            let waiting = self.port.bytes_to_read().map_err(io::Error::other)?;
            if waiting == 0 {
                if since.elapsed() >= READ_WAIT {
                    return Ok(None);
                }
                std::thread::sleep(POLL);
                continue;
            }
            let mut chunk = [0u8; 256];
            let want = (waiting as usize).min(chunk.len());
            match self.port.read(&mut chunk[..want]) {
                Ok(0) => return Ok(None),
                Ok(n) => self.buf.extend_from_slice(&chunk[..n]),
                Err(e) if e.kind() == io::ErrorKind::TimedOut => return Ok(None),
                Err(e) => return Err(e),
            }
        }
    }
}

struct PortWriter {
    port: Box<dyn serialport::SerialPort>,
}

impl LineWriter for PortWriter {
    fn write_line(&mut self, line: &str) -> io::Result<()> {
        self.port.write_all(format!("{line}\n").as_bytes())?;
        self.port.flush()
    }
}

/// Every serial port on the machine, with its description.
pub fn list_ports() -> Vec<(String, String)> {
    serialport::available_ports()
        .unwrap_or_default()
        .into_iter()
        .map(|p| {
            let desc = match p.port_type {
                serialport::SerialPortType::UsbPort(u) => u.product.unwrap_or_default(),
                _ => String::new(),
            };
            (p.port_name, desc)
        })
        .collect()
}

/// Probe ports for the Pico's DATA channel: it answers the handshake with
/// `PICO_READY` (the console port prints a REPL banner instead). Skips
/// `exclude` — Windows ports are exclusive, so the one in use can't be
/// probed anyway.
pub fn discover_data_port(exclude: Option<&str>, handshake_timeout: Duration) -> Option<String> {
    let names: Vec<String> = list_ports()
        .into_iter()
        .map(|(name, _)| name)
        .filter(|name| Some(name.as_str()) != exclude)
        .collect();
    find_data_port(&names, handshake_timeout)
}

/// The first of `names` that answers as the Pico's DATA port.
pub fn find_data_port(names: &[String], handshake_timeout: Duration) -> Option<String> {
    names
        .iter()
        .find(|name| is_data_port(name, handshake_timeout))
        .cloned()
}

fn is_data_port(name: &str, handshake_timeout: Duration) -> bool {
    let Ok(mut port) = serialport::new(name, BAUD)
        .timeout(Duration::from_millis(200))
        .open()
    else {
        return false;
    };
    let _ = port.write_data_terminal_ready(false);
    std::thread::sleep(Duration::from_millis(50));
    let _ = port.write_data_terminal_ready(true);
    let mut reader = PortReader {
        port,
        buf: Vec::new(),
    };
    let probe = |send: bool, r: &mut PortReader| -> Option<bool> {
        if send {
            let _ = r.port.write_all(b"hello|handshake\n");
            let _ = r.port.flush();
        }
        let end = Instant::now() + handshake_timeout;
        while Instant::now() < end {
            let Ok(Some(line)) = r.read_line() else {
                continue;
            };
            let lower = line.to_lowercase();
            if lower.contains("circuitpython") || lower.contains("repl") || lower.starts_with(">>>")
            {
                return Some(false); // the console, not DATA
            }
            if line.starts_with("PICO_READY") {
                return Some(true);
            }
        }
        None
    };
    match probe(false, &mut reader) {
        Some(v) => v,
        None => probe(true, &mut reader) == Some(true),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replies_parse_with_and_without_numbers() {
        let r = |ok: bool, d: &str| (ok, d.to_owned());
        assert_eq!(parse_reply("ACK 17"), Some((Some(17), r(true, ""))));
        assert_eq!(parse_reply("NACK 3"), Some((Some(3), r(false, ""))));
        assert_eq!(parse_reply("ACK"), Some((None, r(true, ""))));
        assert_eq!(parse_reply("PICO_READY v2"), None);
    }

    #[test]
    fn numbered_replies_carry_data() {
        let r = |ok: bool, d: &str| (ok, d.to_owned());
        assert_eq!(
            parse_reply("ACK 5 left|page up|,"),
            Some((Some(5), r(true, "left|page up|,")))
        );
        assert_eq!(parse_reply("ACK x y"), Some((None, r(true, ""))));
    }

    #[test]
    fn bare_replies_resolve_in_send_order_and_numbered_ones_by_number() {
        let mut p = Pending::default();
        let (a, ra) = sync_channel(1);
        let (b, rb) = sync_channel(1);
        p.by_seq.insert(1, a);
        p.order.push_back(1);
        p.by_seq.insert(2, b);
        p.order.push_back(2);
        p.resolve(Some(2), (false, String::new()));
        assert_eq!(rb.try_recv(), Ok((false, String::new())));
        p.resolve(None, (true, String::new()));
        assert_eq!(ra.try_recv(), Ok((true, String::new())));
        assert!(p.by_seq.is_empty() && p.order.is_empty());
    }
}
