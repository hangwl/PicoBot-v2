//! How fast the player dot can be sampled (read-only: captures only).
//!
//!     cargo run --release -p picobot-io --example dot_rate -- "Rien" [seconds]

use std::time::{Duration, Instant};

use picobot_core::config::MinimapColors;
use picobot_core::minimap::{MinimapAnalyzer, PlayerTracker};
use picobot_io::capture::ScreenGrabber;
use picobot_io::window::GameWindow;

fn main() {
    let mut args = std::env::args().skip(1);
    let title = args.next().unwrap_or_else(|| "Rien".into());
    let secs: f64 = args.next().and_then(|s| s.parse().ok()).unwrap_or(2.0);
    let mut win = GameWindow::find(&title).expect("game window");
    let mut grab = ScreenGrabber::new();
    let an = MinimapAnalyzer::new(MinimapColors::default(), None, 4);
    let (l, t, r, b) = win.client_rect().unwrap();
    let full = grab.capture(l, t, r - l, b - t).unwrap();
    let region = an.locate(&full).expect("minimap frame");
    println!("region {region:?}");
    let mut tracker = PlayerTracker::default();
    let start = Instant::now();
    let (mut n, mut hits, mut ys) = (0, 0, Vec::new());
    let mut gaps = Vec::new();
    let mut last = Instant::now();
    while start.elapsed().as_secs_f64() < secs {
        let t0 = Instant::now();
        let (l, t, _, _) = win.client_rect().unwrap();
        if let Some(img) = grab.capture(l + region.0, t + region.1, region.2, region.3) {
            n += 1;
            if let Some(p) = an.player_pos(&img, &mut tracker) {
                hits += 1;
                ys.push(p.1);
            }
        }
        gaps.push(last.elapsed().as_secs_f64() * 1e3);
        last = Instant::now();
        let rest = (1.0 / 60.0 - t0.elapsed().as_secs_f64()).max(0.002);
        std::thread::sleep(Duration::from_secs_f64(rest));
    }
    gaps.sort_by(f64::total_cmp);
    let rate = n as f64 / secs;
    println!(
        "{n} captures ({rate:.0}/s), dot in {hits}; gap median {:.1} ms, p90 {:.1} ms; y range {:?}..{:?}",
        gaps[gaps.len() / 2],
        gaps[gaps.len() * 9 / 10],
        ys.iter().min(),
        ys.iter().max()
    );
}
