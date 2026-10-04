//! Phase E spike: talk to the vendor HID channel of the spike firmware.
//!
//!     cargo run --release -p picobot-io --example hid_echo [list | rtt [count] | type [hid keycode]]
//!
//! `list` shows every HID interface of the device, `rtt` times 64-byte echo
//! round trips on the vendor channel, `type` presses and releases a key
//! (HID keycode in hex, default 04 = "a") on the keyboard interface so it
//! types into whatever has the focus.

use std::time::{Duration, Instant};

use hidapi::{HidApi, HidDevice};

const VID: u16 = 0x0C45;
const PID: u16 = 0x8006;
const VENDOR_PAGE: u16 = 0xFF00;

fn open_vendor(api: &HidApi) -> HidDevice {
    let info = api
        .device_list()
        .find(|d| d.vendor_id() == VID && d.product_id() == PID && d.usage_page() == VENDOR_PAGE)
        .expect("no vendor-defined HID interface for 0C45:8006 (is the spike flashed?)");
    api.open_path(info.path())
        .expect("open the vendor interface")
}

fn send(dev: &HidDevice, payload: &[u8]) {
    // Report ID 0 goes first on Windows; the report itself is 64 bytes.
    let mut buf = [0u8; 65];
    buf[1..1 + payload.len()].copy_from_slice(payload);
    dev.write(&buf).expect("write");
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
            let mut ms = Vec::new();
            let mut lost = 0;
            for i in 0..count {
                let t = Instant::now();
                send(&dev, &[b'E', (i & 0xFF) as u8]);
                let mut buf = [0u8; 64];
                match dev.read_timeout(&mut buf, 200) {
                    Ok(n) if n > 0 && buf[0] == b'E' => ms.push(t.elapsed().as_secs_f64() * 1e3),
                    _ => lost += 1,
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            ms.sort_by(f64::total_cmp);
            if ms.is_empty() {
                println!("no echoes ({lost} lost)");
                return;
            }
            println!(
                "{} round trips ({} lost): median {:.1} ms, p90 {:.1} ms, max {:.1} ms",
                ms.len(),
                lost,
                ms[ms.len() / 2],
                ms[ms.len() * 9 / 10],
                ms[ms.len() - 1]
            );
        }
        "type" => {
            let key = args
                .next()
                .and_then(|s| u8::from_str_radix(s.trim_start_matches("0x"), 16).ok())
                .unwrap_or(0x04);
            let dev = open_vendor(&api);
            println!("typing in 3 s: focus an empty Notepad");
            std::thread::sleep(Duration::from_secs(3));
            send(&dev, &[b'K', key]);
            std::thread::sleep(Duration::from_millis(60));
            send(&dev, b"U");
            println!("pressed and released {key:#04x}");
        }
        other => println!("unknown command {other:?}"),
    }
}
