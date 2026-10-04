//! What the rune reader sees on a saved window capture.
//!
//!     cargo run --release -p picobot-io --example rune_check -- <window.png>... [--at x,y]...
//!
//! Prints every candidate blob (pixels, box, centre, arrow-shaped?, the
//! direction it reads) and the one-frame verdict. `--at x,y` adds the colour
//! make-up of the 33x33 patch there: strongly coloured pixels by hue.

use picobot_core::rune_arrows::{diagnose, read_arrows};
use picobot_core::vision::Image;

fn hue_sv(bgr: [u8; 3]) -> (f64, f64, f64) {
    let [b, g, r] = bgr.map(|v| v as f64 / 255.0);
    let (mx, mn) = (r.max(g).max(b), r.min(g).min(b));
    if mx <= 0.0 || mx == mn {
        return (0.0, 0.0, mx);
    }
    let d = mx - mn;
    let h = if mx == r {
        60.0 * ((g - b) / d).rem_euclid(6.0)
    } else if mx == g {
        60.0 * ((b - r) / d + 2.0)
    } else {
        60.0 * ((r - g) / d + 4.0)
    };
    (h, d / mx, mx)
}

fn patch(img: &Image, cx: usize, cy: usize) {
    // Per 30 degrees: (strongly coloured, near-pure).
    let mut buckets = [(0usize, 0usize); 12];
    let mut other = 0;
    for y in cy.saturating_sub(16)..(cy + 17).min(img.height) {
        for x in cx.saturating_sub(16)..(cx + 17).min(img.width) {
            let (h, s, v) = hue_sv(img.bgr(x, y));
            if s >= 0.55 && v >= 0.6 {
                let k = (h / 30.0) as usize % 12;
                buckets[k].0 += 1;
                if s >= 0.85 && v >= 0.8 {
                    buckets[k].1 += 1;
                }
            } else {
                other += 1;
            }
        }
    }
    println!("  patch at ({cx},{cy}): {other} px not strongly coloured; strongly coloured (all / near-pure) by hue:");
    for (k, (a, p)) in buckets.iter().enumerate() {
        if *a > 0 {
            println!("    {:>3}-{:<3}: {a:>4} / {p}", k * 30, k * 30 + 29);
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (mut ats, mut paths) = (Vec::new(), Vec::new());
    let mut it = args.iter();
    while let Some(a) = it.next() {
        if a == "--at" {
            if let Some((x, y)) = it.next().and_then(|v| v.split_once(',')) {
                ats.push((x.parse::<usize>().unwrap(), y.parse::<usize>().unwrap()));
            }
        } else {
            paths.push(a.clone());
        }
    }
    for path in paths {
        let rgb = image::open(&path).expect("open the png").to_rgb8();
        let (w, h) = (rgb.width() as usize, rgb.height() as usize);
        let mut img = Image::new(w, h);
        for (x, y, p) in rgb.enumerate_pixels() {
            img.set_bgr(x as usize, y as usize, [p[2], p[1], p[0]]);
        }
        println!("== {path} ({w}x{h})");
        for line in diagnose(&img) {
            println!("{line}");
        }
        match read_arrows(&img) {
            Ok(r) => println!("read: {:?}", r.keys()),
            Err(e) => println!("not read: {e}"),
        }
        for &(x, y) in &ats {
            patch(&img, x, y);
        }
    }
}
