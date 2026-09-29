//! List serial ports and handshake with the Pico — no key presses.
//!
//! ```text
//! cargo run -p picobot-io --example pico_ping -- [COM6]
//! ```
//! Without a port name, probes for the Pico's DATA port.

use std::time::Duration;

use picobot_io::serial::{discover_data_port, list_ports, SerialLink};

fn main() {
    for (name, desc) in list_ports() {
        println!("port {name}  {desc}");
    }
    let port = match std::env::args().nth(1) {
        Some(p) => p,
        None => match discover_data_port(None, Duration::from_secs(1)) {
            Some(p) => p,
            None => {
                println!("no Pico DATA port found");
                return;
            }
        },
    };
    let link = match SerialLink::open(&port) {
        Ok(l) => l,
        Err(e) => {
            println!("{port}: can't open ({e}) — is the Python host holding it?");
            return;
        }
    };
    link.on_line(|l| println!("  <- {l}"));
    if link.wait_ready(Duration::from_secs(3)) {
        let proto = if link.numbered() {
            "v2: numbered commands + watchdog"
        } else {
            "older firmware: plain commands"
        };
        println!("{port}: PICO_READY ({proto})");
    } else {
        println!("{port}: no PICO_READY within 3s");
    }
}
