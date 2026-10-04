//! The Pico line protocol over a vendor-defined HID interface: the TinyUSB
//! firmware in `firmware/phase-e/k75` instead of a CDC serial port.
//!
//! Lines are newline-terminated text, as on the serial link, cut into
//! 64-byte reports (zero-padded; the receiver drops the NULs). The host
//! writes them as output reports, which reach the firmware as SET_REPORT —
//! the device has no OUT pipe — and reads its replies as input reports.
//! Everything above (`SerialLink`: numbering, ACKs, keepalive, the
//! watchdog) is unchanged.
//!
//! The spec is `hid` (the K75's IDs) or `hid:<vid>:<pid>` in hex.

use std::io;
use std::sync::Arc;
use std::time::Duration;

use hidapi::{HidApi, HidDevice};

use crate::serial::{LineReader, LineWriter, SerialLink};

pub const DEFAULT_VID: u16 = 0x0C45;
pub const DEFAULT_PID: u16 = 0x8006;
/// Usage page of the vendor channel (interface 2 of the K75).
pub const VENDOR_PAGE: u16 = 0xFF13;
/// Bytes in one report.
pub const REPORT: usize = 64;
/// How long a read waits before the reader gets a turn to do upkeep.
const READ_WAIT: Duration = Duration::from_millis(50);

/// A pipe of fixed-size reports.
pub trait ReportPort: Send {
    fn write_report(&mut self, report: &[u8; REPORT]) -> io::Result<()>;
    /// The next report, or `None` when none came within `wait`.
    fn read_report(&mut self, wait: Duration) -> io::Result<Option<Vec<u8>>>;
}

/// `line` and its newline, in zero-padded reports.
pub fn frame(line: &str) -> Vec<[u8; REPORT]> {
    let mut bytes = line.as_bytes().to_vec();
    bytes.push(b'\n');
    bytes
        .chunks(REPORT)
        .map(|c| {
            let mut r = [0u8; REPORT];
            r[..c.len()].copy_from_slice(c);
            r
        })
        .collect()
}

pub struct HidReader<P> {
    port: P,
    buf: Vec<u8>,
}

impl<P: ReportPort> HidReader<P> {
    pub fn new(port: P) -> Self {
        HidReader {
            port,
            buf: Vec::new(),
        }
    }
}

impl<P: ReportPort> LineReader for HidReader<P> {
    fn read_line(&mut self) -> io::Result<Option<String>> {
        loop {
            if let Some(i) = self.buf.iter().position(|b| *b == b'\n') {
                let line: Vec<u8> = self.buf.drain(..=i).collect();
                return Ok(Some(String::from_utf8_lossy(&line).trim_end().to_owned()));
            }
            match self.port.read_report(READ_WAIT)? {
                None => return Ok(None),
                // NULs are padding (and Windows' report-ID byte); text has none.
                Some(r) => self.buf.extend(r.iter().filter(|b| **b != 0)),
            }
        }
    }
}

pub struct HidWriter<P> {
    port: P,
}

impl<P: ReportPort> HidWriter<P> {
    pub fn new(port: P) -> Self {
        HidWriter { port }
    }
}

impl<P: ReportPort> LineWriter for HidWriter<P> {
    fn write_line(&mut self, line: &str) -> io::Result<()> {
        for report in frame(line) {
            self.port.write_report(&report)?;
        }
        Ok(())
    }
}

/// One open handle on the vendor interface.
struct HidPort {
    dev: HidDevice,
    _api: Arc<HidApi>,
}

impl ReportPort for HidPort {
    fn write_report(&mut self, report: &[u8; REPORT]) -> io::Result<()> {
        // Report ID 0 leads on Windows. The interface has no OUT pipe, so
        // an output write goes over EP0; a feature report always does.
        let mut buf = [0u8; REPORT + 1];
        buf[1..].copy_from_slice(report);
        if self.dev.write(&buf).is_ok() {
            return Ok(());
        }
        self.dev.send_feature_report(&buf).map_err(io::Error::other)
    }

    fn read_report(&mut self, wait: Duration) -> io::Result<Option<Vec<u8>>> {
        let mut raw = [0u8; REPORT + 1];
        match self.dev.read_timeout(&mut raw, wait.as_millis() as i32) {
            Ok(0) => Ok(None),
            Ok(n) => Ok(Some(raw[..n].to_vec())),
            Err(e) => Err(io::Error::other(e)),
        }
    }
}

/// A vendor channel on this machine: what to put in `serial_port`, what the
/// device calls itself, and how many devices share those IDs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Channel {
    pub spec: String,
    pub product: String,
    pub count: usize,
}

/// The spec that names `vid:pid` (`hid` for the K75's own IDs).
pub fn spec_for(vid: u16, pid: u16) -> String {
    if (vid, pid) == (DEFAULT_VID, DEFAULT_PID) {
        "hid".into()
    } else {
        format!("hid:{vid:04x}:{pid:04x}")
    }
}

/// Every vendor channel present, grouped by IDs.
pub fn channels() -> Vec<Channel> {
    let Ok(api) = HidApi::new() else {
        return Vec::new();
    };
    let mut found: Vec<Channel> = Vec::new();
    for d in api.device_list().filter(|d| d.usage_page() == VENDOR_PAGE) {
        let spec = spec_for(d.vendor_id(), d.product_id());
        match found.iter_mut().find(|c| c.spec == spec) {
            Some(c) => c.count += 1,
            None => found.push(Channel {
                spec,
                product: d.product_string().unwrap_or("HID device").to_owned(),
                count: 1,
            }),
        }
    }
    found
}

/// `hid` / `hid:<vid>:<pid>` → the IDs, or `None` for anything else.
pub fn parse_spec(spec: &str) -> Option<(u16, u16)> {
    let spec = spec.trim().to_ascii_lowercase();
    if spec == "hid" {
        return Some((DEFAULT_VID, DEFAULT_PID));
    }
    let ids = spec.strip_prefix("hid:")?;
    let (vid, pid) = ids.split_once(':')?;
    Some((
        u16::from_str_radix(vid.trim_start_matches("0x"), 16).ok()?,
        u16::from_str_radix(pid.trim_start_matches("0x"), 16).ok()?,
    ))
}

/// Open the vendor channel named by `spec` and start a link over it. Two
/// handles, one for each direction: a read waiting for input must not hold
/// the writes.
pub fn open(spec: &str) -> io::Result<SerialLink> {
    let (vid, pid) =
        parse_spec(spec).ok_or_else(|| io::Error::other(format!("not a HID spec: {spec:?}")))?;
    let api = Arc::new(HidApi::new().map_err(io::Error::other)?);
    let paths: Vec<_> = api
        .device_list()
        .filter(|d| d.vendor_id() == vid && d.product_id() == pid && d.usage_page() == VENDOR_PAGE)
        .map(|d| d.path().to_owned())
        .collect();
    let path = match paths.as_slice() {
        [one] => one,
        [] => {
            return Err(io::Error::other(format!(
                "no vendor HID channel for {vid:04x}:{pid:04x} (is the TinyUSB firmware running?)"
            )))
        }
        _ => {
            return Err(io::Error::other(format!(
                "more than one device matches {vid:04x}:{pid:04x}: unplug the real keyboard"
            )))
        }
    };
    let open = || -> io::Result<HidPort> {
        Ok(HidPort {
            dev: api.open_path(path).map_err(io::Error::other)?,
            _api: api.clone(),
        })
    };
    let (reader, writer) = (open()?, open()?);
    Ok(SerialLink::start(
        spec.trim(),
        Box::new(HidReader::new(reader)),
        Box::new(HidWriter::new(writer)),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc::{channel, Receiver, Sender};
    use std::time::Duration;

    #[test]
    fn spec_names_the_device() {
        assert_eq!(parse_spec("hid"), Some((0x0C45, 0x8006)));
        assert_eq!(parse_spec(" HID:258a:0501 "), Some((0x258A, 0x0501)));
        assert_eq!(parse_spec("hid:0x0c45:0x8006"), Some((0x0C45, 0x8006)));
        assert_eq!(parse_spec("COM8"), None);
        assert_eq!(parse_spec("hid:zz:1"), None);
    }

    #[test]
    fn a_spec_round_trips_through_its_ids() {
        assert_eq!(spec_for(0x0C45, 0x8006), "hid");
        let other = spec_for(0x258A, 0x0501);
        assert_eq!(other, "hid:258a:0501");
        assert_eq!(parse_spec(&other), Some((0x258A, 0x0501)));
    }

    #[test]
    fn lines_are_cut_into_padded_reports() {
        let short = frame("1:hid|held");
        assert_eq!(short.len(), 1);
        assert_eq!(&short[0][..11], b"1:hid|held\n");
        assert!(short[0][11..].iter().all(|b| *b == 0));
        let long = frame(&"x".repeat(100));
        assert_eq!(long.len(), 2);
        assert_eq!(long[1][100 - REPORT], b'\n');
    }

    /// One half of an in-memory device: writes go out, reads come in.
    struct Half {
        tx: Option<Sender<Vec<u8>>>,
        rx: Option<Receiver<Vec<u8>>>,
    }

    impl ReportPort for Half {
        fn write_report(&mut self, report: &[u8; REPORT]) -> io::Result<()> {
            let tx = self.tx.as_ref().expect("write half");
            tx.send(report.to_vec())
                .map_err(|_| io::Error::other("closed"))
        }
        fn read_report(&mut self, wait: Duration) -> io::Result<Option<Vec<u8>>> {
            let rx = self.rx.as_ref().expect("read half");
            Ok(rx.recv_timeout(wait).ok())
        }
    }

    /// A stand-in firmware: reassembles lines from reports and answers like
    /// the real one (`PICO_READY v2`, numbered ACKs, a long `hid|keys`).
    fn fake_firmware(from_host: Receiver<Vec<u8>>, to_host: Sender<Vec<u8>>) {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            let say = |line: String| {
                for r in frame(&line) {
                    let _ = to_host.send(r.to_vec());
                }
            };
            while let Ok(report) = from_host.recv() {
                buf.extend(report.iter().filter(|b| **b != 0));
                while let Some(i) = buf.iter().position(|b| *b == b'\n') {
                    let line = String::from_utf8_lossy(&buf[..i]).to_string();
                    buf.drain(..=i);
                    if line.starts_with("hello") {
                        say("PICO_READY v2".into());
                    } else if let Some((seq, cmd)) = line.split_once(':') {
                        if cmd == "hid|keys" {
                            let keys: Vec<String> = (0..60).map(|i| format!("key{i}")).collect();
                            say(format!("ACK {seq} {}", keys.join("|")));
                        } else {
                            say(format!("ACK {seq}"));
                        }
                    }
                }
            }
        });
    }

    fn linked() -> SerialLink {
        let (host_tx, fw_rx) = channel();
        let (fw_tx, host_rx) = channel();
        fake_firmware(fw_rx, fw_tx);
        SerialLink::start(
            "hid",
            Box::new(HidReader::new(Half {
                tx: None,
                rx: Some(host_rx),
            })),
            Box::new(HidWriter::new(Half {
                tx: Some(host_tx),
                rx: None,
            })),
        )
    }

    #[test]
    fn a_link_over_reports_handshakes_and_acks() {
        let link = linked();
        assert!(link.wait_ready(Duration::from_secs(2)));
        assert!(link.numbered());
        link.send_acked("hid|key|down|a|3000", Duration::from_secs(1))
            .unwrap();
        link.send_acked("hid|key|up|a", Duration::from_secs(1))
            .unwrap();
    }

    #[test]
    fn a_reply_spanning_several_reports_is_reassembled() {
        let link = linked();
        assert!(link.wait_ready(Duration::from_secs(2)));
        let keys = link.query("hid|keys", Duration::from_secs(1)).unwrap();
        assert!(keys.len() > 3 * REPORT, "{} bytes", keys.len());
        assert!(keys.starts_with("key0|key1|") && keys.ends_with("|key59"));
    }
}
