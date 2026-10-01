//! Checking the panel frame against the live window.

use picobot_core::config::MinimapColors;
use picobot_core::minimap::MinimapAnalyzer;
use picobot_core::vision::Image;

const WHITE: [u8; 3] = [228, 228, 228];

fn rect(img: &mut Image, x0: usize, y0: usize, x1: usize, y1: usize) {
    for y in y0..=y1 {
        for x in x0..=x1 {
            img.set_bgr(x, y, WHITE);
        }
    }
}

/// A window with a rounded-corner frame at (x, y), `w` x `h` edge to edge.
fn window(x: usize, y: usize, w: usize, h: usize) -> Image {
    let mut img = Image::new(800, 600);
    for p in img.bgra.as_chunks_mut::<4>().0 {
        p.copy_from_slice(&[40, 30, 20, 255]);
    }
    rect(&mut img, x + 3, y, x + w - 3, y);
    rect(&mut img, x + 3, y + h, x + w - 3, y + h);
    rect(&mut img, x, y + 3, x, y + h - 3);
    rect(&mut img, x + w, y + 3, x + w, y + h - 3);
    img
}

fn analyzer(region: (i32, i32, i32, i32), manual: bool) -> MinimapAnalyzer {
    let mm = MinimapAnalyzer::new(MinimapColors::default(), None, 4);
    mm.set_region(region, manual);
    mm
}

#[test]
fn a_region_with_the_right_corner_but_the_wrong_size_is_corrected() {
    // Same top-left, but the live frame is bigger: the old top-edge check
    // would never notice.
    let mm = analyzer((20, 30, 150, 60), false);
    let live = window(20, 30, 200, 100);
    assert_eq!(mm.verify_region(&live), None, "one sighting isn't enough");
    assert_eq!(
        mm.verify_region(&live),
        Some(((20, 30, 150, 60), (20, 30, 200, 100)))
    );
    assert_eq!(mm.region(), Some((20, 30, 200, 100)));
    assert_eq!(mm.verify_region(&live), None, "now it matches");
}

#[test]
fn a_flicker_between_different_frames_does_not_switch() {
    let mm = analyzer((20, 30, 200, 100), false);
    assert_eq!(mm.verify_region(&window(20, 30, 150, 60)), None);
    assert_eq!(mm.verify_region(&window(25, 40, 120, 50)), None); // a different one
    assert_eq!(mm.verify_region(&window(20, 30, 200, 100)), None); // back to the region
    assert_eq!(mm.region(), Some((20, 30, 200, 100)));
}

#[test]
fn a_one_pixel_difference_is_the_same_frame() {
    let mm = analyzer((20, 30, 200, 100), false);
    let live = window(21, 30, 200, 100);
    for _ in 0..3 {
        assert_eq!(mm.verify_region(&live), None);
    }
    assert_eq!(mm.region(), Some((20, 30, 200, 100)));
}

#[test]
fn drawn_and_pinned_regions_and_missing_frames_are_left_alone() {
    let live = window(20, 30, 200, 100);
    let manual = analyzer((20, 30, 150, 60), true);
    let pinned = MinimapAnalyzer::new(MinimapColors::default(), Some((20, 30, 150, 60)), 4);
    for mm in [&manual, &pinned] {
        for _ in 0..3 {
            assert_eq!(mm.verify_region(&live), None);
        }
        assert_eq!(mm.region(), Some((20, 30, 150, 60)));
    }
    let mm = analyzer((20, 30, 150, 60), false);
    for _ in 0..3 {
        assert_eq!(mm.verify_region(&Image::new(800, 600)), None); // no frame at all
    }
    assert_eq!(mm.region(), Some((20, 30, 150, 60)));
}
