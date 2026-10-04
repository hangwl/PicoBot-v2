//! Where the player's feet read now, against a map's drawn platform rows.
//!
//!     cargo run --release -p picobot-io --example feet_now -- <maps/file.json> [samples]
//!
//! Captures the live minimap, reads the dot with the real detector and
//! prints each sample's feet with the nearest drawn row (residual = feet y
//! minus the row: positive means the feet sit below the line). Stand still
//! on one platform while it runs.

use std::path::Path;
use std::time::Duration;

use picobot_core::config::{AppConfig, MinimapColors};
use picobot_core::minimap::{MinimapAnalyzer, PlayerTracker};
use picobot_io::capture::ScreenGrabber;
use picobot_io::window::GameWindow;

fn main() {
    let mut args = std::env::args().skip(1);
    let map_path = args.next().expect("a maps/*.json file");
    let samples: usize = args.next().and_then(|s| s.parse().ok()).unwrap_or(10);
    let map: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&map_path).expect("read the map")).unwrap();
    let plats: Vec<[f64; 4]> = map["platforms"]
        .as_array()
        .expect("platforms")
        .iter()
        .map(|p| {
            let v: Vec<f64> = p
                .as_array()
                .unwrap()
                .iter()
                .map(|n| n.as_f64().unwrap())
                .collect();
            [v[0], v[1], v[2], v[3]]
        })
        .collect();
    let title = AppConfig::load(Path::new("../config.json"))
        .0
        .default_target_window;
    let win = GameWindow::find(&title);
    let mut win = win.unwrap_or_else(|| panic!("no window titled {title:?}"));
    let mut grab = ScreenGrabber::new();
    let mm = MinimapAnalyzer::new(MinimapColors::default(), None, 4);
    let mut tracker = PlayerTracker::default();
    for _ in 0..samples {
        let (l, t, r, b) = win.client_rect().expect("client rect");
        let window = grab.capture(l, t, r - l, b - t).expect("capture");
        let Some(reg) = mm.locate(&window) else {
            println!("minimap not found");
            continue;
        };
        let img = grab
            .capture(l + reg.0, t + reg.1, reg.2, reg.3)
            .expect("capture the minimap");
        let (w, h) = (reg.2 as f64, reg.3 as f64);
        match mm.player_pos(&img, &mut tracker) {
            Some((x, y)) => {
                let near = plats
                    .iter()
                    .filter(|p| x as f64 >= p[0] * w - 3.0 && x as f64 <= p[2] * w + 3.0)
                    .map(|p| p[1] * h)
                    .min_by(|a, b| (a - y as f64).abs().total_cmp(&(b - y as f64).abs()));
                match near {
                    Some(row) => println!(
                        "feet ({x},{y}); nearest drawn row {row:.1}; residual {:+.1}",
                        y as f64 - row
                    ),
                    None => println!("feet ({x},{y}); no drawn platform spans x"),
                }
            }
            None => println!("dot not found"),
        }
        std::thread::sleep(Duration::from_millis(300));
    }
}
