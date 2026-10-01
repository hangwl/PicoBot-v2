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

/// A lossy in-game screenshot of a crowded map: the red monster markers
/// are not the other-player colour, so none of them count.
#[test]
fn monster_markers_are_not_counted_as_players() {
    let colors = MinimapColors::default();
    let mm = MinimapAnalyzer::new(colors, None, 4);
    let img = load("monsters.png").crop(8, 69, 170, 62);
    assert_eq!(mm.count_other_players(&img, MARKER_MIN_PX), 0);
}

#[test]
fn a_rope_lift_to_the_top_keeps_the_dot_under_the_frame_rim() {
    // A dot-lost capture: the dot's bottom shows at rows 2-4 at x 90,
    // mostly inside the 4px rim the marker search crops.
    let colors = MinimapColors::default();
    let rim = load("dot_under_top_rim.png");
    let mm = MinimapAnalyzer::new(colors, Some((0, 0, rim.width as i32, rim.height as i32)), 4);
    assert_eq!(mm.player_pos(&rim, &mut PlayerTracker::default()), None);
    // Seen just below a moment earlier (on the rope lift's way up).
    let mut before = rim.clone();
    for y in 10..=15 {
        for x in 87..93 {
            before.set_bgr(x, y, colors.player);
        }
    }
    let mut t = PlayerTracker::default();
    assert_eq!(mm.player_pos(&before, &mut t), Some((90, 15)));
    for _ in 0..3 {
        assert_eq!(mm.player_pos(&rim, &mut t), Some((90, 4)));
    }
}
