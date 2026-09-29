//! Live capture + minimap analysis timing (read-only; no input).
//!
//! ```text
//! cargo run --release -p picobot-io --example vision_bench -- "<window title>"
//! ```
//! Compare with `rust/tools/vision_bench.py` (same work in the Python host).

use std::time::Instant;

use picobot_core::config::MinimapColors;
use picobot_core::minimap::{MinimapAnalyzer, PlayerTracker};
use picobot_core::vision::{find_frame, is_dark};
use picobot_io::capture::ScreenGrabber;
use picobot_io::perf::process_cpu_seconds;
use picobot_io::window::GameWindow;

fn main() {
    let title = std::env::args()
        .nth(1)
        .expect("usage: vision_bench <window title>");
    let mut win = GameWindow::find(&title).expect("window not found");
    let (l, t, r, b) = win.client_rect().expect("no client rect");
    let mut grab = ScreenGrabber::new();
    grab.layered = std::env::var_os("LAYERED").is_some();

    let t0 = Instant::now();
    let window = grab.capture(l, t, r - l, b - t).expect("capture failed");
    let t_window = t0.elapsed();
    let t0 = Instant::now();
    let frame = find_frame(&window, MinimapColors::default().border, 10);
    let t_find = t0.elapsed();
    println!(
        "client {}x{}: window capture {:.2} ms, find_frame {:.2} ms -> {:?}",
        r - l,
        b - t,
        t_window.as_secs_f64() * 1e3,
        t_find.as_secs_f64() * 1e3,
        frame
    );
    let Some((x, y, w, h)) = frame else { return };

    let mm = MinimapAnalyzer::new(MinimapColors::default(), Some((x, y, w, h)), 4);
    let mut tracker = PlayerTracker::default();
    let n = 300;
    let (mut t_cap, mut t_ana) = (0.0, 0.0);
    let mut last = None;
    let (cpu0, wall0) = (process_cpu_seconds(), Instant::now());
    for _ in 0..n {
        let t0 = Instant::now();
        let img = grab.capture(l + x, t + y, w, h).expect("capture failed");
        t_cap += t0.elapsed().as_secs_f64();
        let t0 = Instant::now();
        let _ = is_dark(&img, 12.0);
        last = mm.player_pos(&img, &mut tracker);
        let _ = mm.rune_pos(&img);
        let _ = mm.has_other_players(&img);
        t_ana += t0.elapsed().as_secs_f64();
    }
    let cpu = (process_cpu_seconds() - cpu0) / n as f64;
    let wall = wall0.elapsed().as_secs_f64() / n as f64;
    println!(
        "rust: minimap {w}x{h}: capture {:.3} ms, analysis {:.3} ms per frame; CPU {:.3} ms of {:.3} ms wall (player {:?})",
        t_cap / n as f64 * 1e3,
        t_ana / n as f64 * 1e3,
        cpu * 1e3,
        wall * 1e3,
        last
    );
}
