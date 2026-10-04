//! Reboot the Pico into maintenance mode (CIRCUITPY drive and REPL back,
//! stock USB identity) so its files can be edited. The next reset returns
//! to the normal boot.
//!
//!     cargo run --release -p picobot-io --example pico_maintenance -- [COM6]

use std::time::Duration;

use picobot_io::serial::{discover_data_port, SerialLink};

fn main() {
    let port = std::env::args()
        .nth(1)
        .or_else(|| discover_data_port(None, Duration::from_millis(1500)))
        .expect("no Pico data port found; pass one, e.g. COM6");
    let link = SerialLink::open(&port).expect("open the port");
    assert!(link.wait_ready(Duration::from_secs(3)), "no PICO_READY");
    match link.send_acked("maintenance", Duration::from_millis(1500)) {
        Ok(()) => println!("{port}: rebooting into maintenance mode"),
        Err(e) => println!("{port}: not accepted ({e:?}) - old firmware?"),
    }
}
