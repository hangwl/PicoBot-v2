//! Phase E: talk the Pico line protocol over the K75 clone's vendor HID
//! channel (interface 2, usage page 0xFF13, 64-byte reports).
//!
//!     cargo run --release -p picobot-io --example hid_echo [list | rtt [count] | type [key name]]
//!
//! `list` shows every HID interface of the device, `rtt` times numbered
//! command round trips (`N:hid|held` -> `ACK N`), `type` taps a key by
//! name (default "a") on the keyboard interface.

use std::time::{Duration, Instant};

use hidapi::{HidApi, HidDevice};

const VID: u16 = 0x0C45;
const PID: u16 = 0x8006;
const VENDOR_PAGE: u16 = 0xFF13; // the K75's own vendor collection (if2)

fn open_vendor(api: &HidApi) -> HidDevice {
    let info = api
        .device_list()
        .find(|d| d.vendor_id() == VID && d.product_id() == PID && d.usage_page() == VENDOR_PAGE)
        .expect("no vendor-defined HID interface for 0C45:8006 (is k75.uf2 flashed?)");
    api.open_path(info.path())
        .expect("open the vendor interface")
}

fn send(dev: &HidDevice, line: &str) {
    // Report ID 0 goes first on Windows; the report itself is 64 bytes.
    // The clone has no interrupt-OUT pipe (like the real K75): an output
    // write reaches EP0 as SET_REPORT, a feature report always does.
    let payload = format!("{line}\n");
    let mut buf = [0u8; 65];
    let n = payload.len().min(64);
    buf[1..1 + n].copy_from_slice(&payload.as_bytes()[..n]);
    if dev.write(&buf).is_err() {
        dev.send_feature_report(&buf).expect("feature report");
    }
}

/// Accumulate incoming bytes (Windows may prefix a report-ID 0 byte; NULs
/// are stream padding) until a newline arrives; returns the line.
fn recv_line(dev: &HidDevice, buf: &mut Vec<u8>) -> Option<String> {
    let mut raw = [0u8; 65];
    loop {
        if let Some(i) = buf.iter().position(|b| *b == b'\n') {
            let line: Vec<u8> = buf.drain(..=i).collect();
            return Some(String::from_utf8_lossy(&line).trim_end().to_owned());
        }
        match dev.read_timeout(&mut raw, 300) {
            Ok(0) | Err(_) => return None,
            Ok(n) => {
                let from = usize::from(raw[0] == 0);
                buf.extend(raw[from..n].iter().filter(|b| **b != 0));
            }
        }
    }
}

fn main() {
    let mut args = std::env::args().skip(1);
    let cmd = args.next().unwrap_or_else(|| "list".into());
    let api = HidApi::new().expect("hidapi");
    match cmd.as_str() {
        "list" => {
            for d in api
                .device_list()
                .filter(|d| d.vendor_id() == VID && d.product_id() == PID)
            {
                println!(
                    "interface {:>2}  usage page {:#06x}  usage {:#04x}  {:?}  serial {:?}",
                    d.interface_number(),
                    d.usage_page(),
                    d.usage(),
                    d.product_string().unwrap_or("?"),
                    d.serial_number()
                );
            }
        }
        "rtt" => {
            let count: usize = args.next().and_then(|s| s.parse().ok()).unwrap_or(300);
            let dev = open_vendor(&api);
            send(&dev, "hello|handshake");
            let mut buf = Vec::new();
            match recv_line(&dev, &mut buf).as_deref() {
                Some("PICO_READY v2") => println!("PICO_READY v2"),
                other => println!("handshake: {other:?}"),
            }
            let mut ms = Vec::new();
            let mut lost = 0;
            for i in 0..count {
                let t = Instant::now();
                send(&dev, &format!("{}:hid|held", i + 1));
                match recv_line(&dev, &mut buf) {
                    Some(l) if l.starts_with(&format!("ACK {}", i + 1)) => {
                        ms.push(t.elapsed().as_secs_f64() * 1e3)
                    }
                    _ => lost += 1,
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            ms.sort_by(f64::total_cmp);
            if ms.is_empty() {
                println!("no ACKs ({lost} lost)");
                return;
            }
            println!(
                "{} ACKs ({} lost): median {:.1} ms, p90 {:.1} ms, max {:.1} ms",
                ms.len(),
                lost,
                ms[ms.len() / 2],
                ms[ms.len() * 9 / 10],
                ms[ms.len() - 1]
            );
        }
        "type" => {
            let key = args.next().unwrap_or_else(|| "a".into());
            let dev = open_vendor(&api);
            let mut buf = Vec::new();
            println!("typing in 3 s: focus an empty Notepad");
            std::thread::sleep(Duration::from_secs(3));
            send(&dev, &format!("1:hid|key|down|{key}"));
            println!("down: {:?}", recv_line(&dev, &mut buf));
            std::thread::sleep(Duration::from_millis(60));
            send(&dev, &format!("2:hid|key|up|{key}"));
            println!("up:   {:?}", recv_line(&dev, &mut buf));
        }
        other => println!("unknown command {other:?}"),
    }
}
