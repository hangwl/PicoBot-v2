//! Round-trip time of numbered commands to the Pico, measured with a
//! command the firmware NACKs without doing anything (no HID input).
//!
//!     cargo run --release -p picobot-io --example serial_latency -- COM6 [count]

use std::time::{Duration, Instant};

use picobot_io::serial::{SendError, SerialLink};

fn main() {
    let mut args = std::env::args().skip(1);
    let port = args.next().expect("port, e.g. COM6");
    let count: usize = args.next().and_then(|s| s.parse().ok()).unwrap_or(40);
    let link = SerialLink::open(&port).expect("open the port");
    assert!(link.wait_ready(Duration::from_secs(3)), "no PICO_READY");
    println!("ready (numbered: {})", link.numbered());
    let mut ms = Vec::new();
    for _ in 0..count {
        let t = Instant::now();
        let r = link.send_acked("noop", Duration::from_millis(1500));
        let dt = t.elapsed().as_secs_f64() * 1e3;
        assert!(
            matches!(r, Err(SendError::Rejected)),
            "expected a NACK, got {r:?}"
        );
        ms.push(dt);
        std::thread::sleep(Duration::from_millis(20));
    }
    ms.sort_by(f64::total_cmp);
    println!(
        "{count} round trips: median {:.1} ms, p90 {:.1} ms, max {:.1} ms",
        ms[count / 2],
        ms[count * 9 / 10],
        ms[count - 1]
    );
}
