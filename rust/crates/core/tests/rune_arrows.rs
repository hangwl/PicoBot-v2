//! The rune arrow reader on strips cut from real solve recordings.

use std::path::Path;

use picobot_core::rune_arrows::read_arrows;
use picobot_core::vision::Image;
use serde_json::Value;

fn fixtures() -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/rune")
}

/// The strip pasted back into a window of its recorded size.
fn window_with(strip: &Path, size: (usize, usize), off: (usize, usize)) -> Image {
    let png = image::open(strip).unwrap().to_rgb8();
    let crop = Image::from_rgb(png.width() as usize, png.height() as usize, png.as_raw());
    let mut img = Image::new(size.0, size.1);
    for y in 0..crop.height {
        for x in 0..crop.width {
            img.set_bgr(off.0 + x, off.1 + y, crop.bgr(x, y));
        }
    }
    img
}

#[test]
fn every_recorded_strip_reads_as_shown() {
    let spec: Value =
        serde_json::from_str(&std::fs::read_to_string(fixtures().join("strips.json")).unwrap())
            .unwrap();
    let n = |v: &Value, i: usize| v[i].as_u64().unwrap() as usize;
    let size = (n(&spec["window"], 0), n(&spec["window"], 1));
    let off = (n(&spec["offset"], 0), n(&spec["offset"], 1));
    for s in spec["strips"].as_array().unwrap() {
        let file = s["file"].as_str().unwrap();
        let want: Vec<&str> = s["arrows"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        let img = window_with(&fixtures().join(file), size, off);
        let read = read_arrows(&img).unwrap_or_else(|e| panic!("{file}: {e}"));
        assert_eq!(read.keys(), want, "{file}");
    }
}
