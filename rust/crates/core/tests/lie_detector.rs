//! The lie detector's title on crops of real game windows.

use std::path::Path;

use picobot_core::lie_detector::find_lie_detector;
use picobot_core::vision::Image;

fn load(name: &str) -> Image {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/lie")
        .join(name);
    let png = image::open(path).unwrap().to_rgb8();
    Image::from_rgb(png.width() as usize, png.height() as usize, png.as_raw())
}

/// `crop` pasted into a 1366x768 window at `off`.
fn window_with(crop: &Image, off: (usize, usize)) -> Image {
    let mut img = Image::new(1366, 768);
    for y in 0..crop.height {
        for x in 0..crop.width {
            img.set_bgr(off.0 + x, off.1 + y, crop.bgr(x, y));
        }
    }
    img
}

#[test]
fn the_title_is_found_where_the_window_shows_it() {
    // Cut at (895, 350); the title starts at (912, 364).
    for name in ["title_a.png", "title_b.png"] {
        let crop = load(name);
        assert_eq!(find_lie_detector(&crop), Some((17, 14)), "{name}");
        assert_eq!(
            find_lie_detector(&window_with(&crop, (895, 350))),
            Some((912, 364))
        );
        // Anywhere else on screen, too.
        assert_eq!(
            find_lie_detector(&window_with(&crop, (300, 100))),
            Some((317, 114))
        );
    }
}

#[test]
fn yellow_banners_and_chat_are_not_it() {
    for name in ["busy_a.png", "busy_b.png"] {
        assert_eq!(find_lie_detector(&load(name)), None, "{name}");
    }
    assert_eq!(find_lie_detector(&Image::new(1366, 768)), None);
}
