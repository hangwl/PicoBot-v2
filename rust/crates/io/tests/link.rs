//! `SerialLink` against an in-memory Pico firmware.

use std::io;
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use picobot_io::hid::HidController;
use picobot_io::serial::{LineReader, LineWriter, SendError, SerialLink};

/// What the host reads: firmware lines, or a port failure.
struct ChanReader(Receiver<io::Result<String>>);

impl LineReader for ChanReader {
    fn read_line(&mut self) -> io::Result<Option<String>> {
        match self.0.recv_timeout(Duration::from_millis(20)) {
            Ok(Ok(line)) => Ok(Some(line)),
            Ok(Err(e)) => Err(e),
            Err(RecvTimeoutError::Timeout) => Ok(None),
            Err(RecvTimeoutError::Disconnected) => Err(io::ErrorKind::BrokenPipe.into()),
        }
    }
}

struct ChanWriter(Sender<String>);

impl LineWriter for ChanWriter {
    fn write_line(&mut self, line: &str) -> io::Result<()> {
        self.0
            .send(line.to_owned())
            .map_err(|_| io::ErrorKind::BrokenPipe.into())
    }
}

/// How the simulated firmware behaves.
#[derive(Clone, Copy)]
struct Firmware {
    /// v2 (numbered replies, `PICO_READY v2`) or the older protocol.
    v2: bool,
}

struct Rig {
    link: SerialLink,
    /// Every line the host wrote, in order.
    wire: Arc<Mutex<Vec<String>>>,
    /// Inject a port failure into the host's reader.
    fail: Sender<io::Result<String>>,
}

/// Keys the firmware knows; `slow` is answered after 200ms, `mute` never.
fn rig(fw: Firmware) -> Rig {
    let (to_host, host_rx) = channel::<io::Result<String>>();
    let (host_tx, from_host) = channel::<String>();
    let wire = Arc::new(Mutex::new(Vec::new()));
    let (w, back) = (wire.clone(), to_host.clone());
    std::thread::spawn(move || {
        let ready = if fw.v2 { "PICO_READY v2" } else { "PICO_READY" };
        while let Ok(line) = from_host.recv() {
            w.lock().unwrap().push(line.clone());
            if line.starts_with("hello") {
                let _ = back.send(Ok(ready.to_owned()));
                continue;
            }
            if line == "ka" {
                continue;
            }
            let (seq, cmd) = match line.split_once(':') {
                Some((s, c)) if s.chars().all(|c| c.is_ascii_digit()) => {
                    (Some(s.to_owned()), c.to_owned())
                }
                _ => (None, line.clone()),
            };
            let key = cmd.rsplit('|').next().unwrap_or("").to_owned();
            if key == "mute" {
                continue;
            }
            let word = if ["a", "b", "left", "slow"].contains(&key.as_str()) {
                "ACK"
            } else {
                "NACK"
            };
            let reply = match seq {
                Some(s) if fw.v2 => format!("{word} {s}"),
                _ => word.to_owned(),
            };
            let back = back.clone();
            let delay = if key == "slow" { 200 } else { 0 };
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(delay));
                let _ = back.send(Ok(reply));
            });
        }
    });
    let link = SerialLink::start(
        "SIM",
        Box::new(ChanReader(host_rx)),
        Box::new(ChanWriter(host_tx)),
    );
    assert!(link.wait_ready(Duration::from_secs(1)));
    Rig {
        link,
        wire,
        fail: to_host,
    }
}

const T: Duration = Duration::from_millis(500);

#[test]
fn v2_firmware_gets_numbered_commands_and_their_acks() {
    let r = rig(Firmware { v2: true });
    assert!(r.link.numbered());
    assert_eq!(r.link.send_acked("hid|key|down|a", T), Ok(()));
    let wire = r.wire.lock().unwrap();
    let last = wire.iter().rev().find(|l| l.contains("hid|")).unwrap();
    let (seq, cmd) = last.split_once(':').unwrap();
    assert!(seq.parse::<u32>().is_ok());
    assert_eq!(cmd, "hid|key|down|a");
}

#[test]
fn a_late_reply_is_never_credited_to_the_next_command() {
    let r = rig(Firmware { v2: true });
    // Times out; its ACK arrives ~150ms later ...
    assert_eq!(
        r.link
            .send_acked("hid|key|down|slow", Duration::from_millis(50)),
        Err(SendError::Timeout)
    );
    // ... while this one (never answered) waits: it must not take it.
    assert_eq!(
        r.link
            .send_acked("hid|key|down|mute", Duration::from_millis(400)),
        Err(SendError::Timeout)
    );
}

#[test]
fn nack_fails_the_sender() {
    let r = rig(Firmware { v2: true });
    assert_eq!(
        r.link.send_acked("hid|key|down|zz", T),
        Err(SendError::Rejected)
    );
}

#[test]
fn older_firmware_gets_plain_commands_matched_in_order_and_no_keepalive() {
    let r = rig(Firmware { v2: false });
    assert!(!r.link.numbered());
    assert_eq!(r.link.send_acked("hid|key|down|a", T), Ok(()));
    assert_eq!(r.link.send_acked("hid|key|up|a", T), Ok(()));
    std::thread::sleep(Duration::from_millis(600));
    let wire = r.wire.lock().unwrap();
    assert!(wire.contains(&"hid|key|down|a".to_owned()));
    assert!(!wire.contains(&"ka".to_owned()));
}

#[test]
fn an_idle_v2_link_sends_keepalives() {
    let r = rig(Firmware { v2: true });
    std::thread::sleep(Duration::from_millis(1000));
    let n = r.wire.lock().unwrap().iter().filter(|l| *l == "ka").count();
    assert!(n >= 1, "{n} keepalives");
}

#[test]
fn a_lost_port_closes_the_link_and_fails_waiters_at_once() {
    let r = rig(Firmware { v2: true });
    let lost = Arc::new(Mutex::new(None));
    let l = lost.clone();
    r.link
        .on_lost(move |why| *l.lock().unwrap() = Some(why.to_owned()));
    // A command that is never answered is waiting when the port dies.
    let fail = r.fail.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(100));
        let _ = fail.send(Err(io::Error::other("device gone")));
    });
    let t0 = Instant::now();
    assert_eq!(
        r.link
            .send_acked("hid|key|down|mute", Duration::from_secs(5)),
        Err(SendError::Closed)
    );
    assert!(t0.elapsed() < Duration::from_secs(2));
    assert!(!r.link.is_open());
    assert_eq!(lost.lock().unwrap().as_deref(), Some("device gone"));
    assert_eq!(r.link.send("hid|key|up|a"), Err(SendError::Closed));
}

#[test]
fn a_controller_over_the_link_releases_its_keys_when_dropped() {
    let r = rig(Firmware { v2: true });
    let link = Arc::new(r.link);
    let mut hid = HidController::for_link(link.clone(), T);
    assert!(hid.key_down("left"));
    drop(hid);
    let wire = r.wire.lock().unwrap();
    let cmds: Vec<&str> = wire
        .iter()
        .filter_map(|l| l.split_once(':').map(|(_, c)| c))
        .collect();
    assert_eq!(cmds, ["hid|key|down|left", "hid|key|up|left"]);
}
