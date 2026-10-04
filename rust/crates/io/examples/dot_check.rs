//! Where the player detector puts the dot on a saved minimap crop.
//!
//!     cargo run --release -p picobot-io --example dot_check -- <frame.png>...

use picobot_core::config::MinimapColors;
use picobot_core::minimap::{MinimapAnalyzer, PlayerTracker};
use picobot_core::vision::Image;

fn main() {
    for path in std::env::args().skip(1) {
        let rgb = image::open(&path).expect("open the png").to_rgb8();
        let (w, h) = (rgb.width() as usize, rgb.height() as usize);
        let mut img = Image::new(w, h);
        for (x, y, p) in rgb.enumerate_pixels() {
            img.set_bgr(x as usize, y as usize, [p[2], p[1], p[0]]);
        }
        let mm = MinimapAnalyzer::new(MinimapColors::default(), None, 4);
        let mut tracker = PlayerTracker::default();
        let pos = mm.player_pos(&img, &mut tracker);
        println!("{path} ({w}x{h}): feet {pos:?}");
        // The yellow core of the disc by the same colour rule, uncropped.
        let c = MinimapColors::default().player;
        let (mut x0, mut x1, mut y0, mut y1, mut n) = (w, 0, h, 0, 0);
        for y in 0..h {
            for x in 0..w {
                let b = img.bgr(x, y);
                let d = (b[0] as i32 - c[0] as i32).abs()
                    + (b[1] as i32 - c[1] as i32).abs()
                    + (b[2] as i32 - c[2] as i32).abs();
                if d < 30 {
                    n += 1;
                    x0 = x0.min(x);
                    x1 = x1.max(x);
                    y0 = y0.min(y);
                    y1 = y1.max(y);
                }
            }
        }
        println!("  colour match: {n} px, box x {x0}..{x1}, y {y0}..{y1}");
    }
}
