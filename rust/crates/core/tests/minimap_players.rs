//! Counting other players on a real minimap capture.

use std::path::Path;

use picobot_core::config::MinimapColors;
use picobot_core::minimap::{MinimapAnalyzer, PlayerTracker, MARKER_MIN_PX};
use picobot_core::vision::{find_frame, Image};

fn load(name: &str) -> Image {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/minimap")
        .join(name);
    let img = image::open(&path).unwrap().to_rgb8();
    Image::from_rgb(img.width() as usize, img.height() as usize, img.as_raw())
}

/// The minimap panel cut out of a small capture, as the bot captures it:
/// placed in the corner of a window-sized canvas, where `find_frame` looks.
fn panel(shot: &Image, colors: &MinimapColors) -> Image {
    let mut window = Image::new(shot.width * 2 + 8, shot.height * 2 + 8);
    for y in 0..shot.height {
        for x in 0..shot.width {
            window.set_bgr(x, y, shot.bgr(x, y));
        }
    }
    let (x, y, w, h) = find_frame(&window, colors.border, 10).expect("minimap frame");
    window.crop(x as usize, y as usize, w as usize, h as usize)
}

#[test]
fn two_other_players_and_the_player_are_told_apart() {
    let colors = MinimapColors::default();
    let mm = MinimapAnalyzer::new(colors, None, 4);
    let img = panel(&load("two_others.png"), &colors);
    assert_eq!(mm.count_other_players(&img, MARKER_MIN_PX), 2);
    assert!(mm.has_other_players(&img));
    // The yellow marker is the player, not an other.
    assert!(mm.player_pos(&img, &mut PlayerTracker::default()).is_some());
}

#[test]
fn a_larger_minimum_drops_small_markers() {
    let colors = MinimapColors::default();
    let mm = MinimapAnalyzer::new(colors, None, 4);
    let img = panel(&load("two_others.png"), &colors);
    assert_eq!(mm.count_other_players(&img, 10_000), 0);
}
