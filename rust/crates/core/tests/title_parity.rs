//! The title-band segmentation against the Python `title_scan`, on the
//! synthetic bands `tools/gen_fixtures.py` writes.

use std::path::Path;

use picobot_core::title::{title_scan, ScanOptions};
use picobot_core::vision::Image;
use serde_json::Value;

#[test]
fn title_scan_matches_python() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/title");
    let cases: Vec<Value> =
        serde_json::from_str(&std::fs::read_to_string(dir.join("trace_title.json")).unwrap())
            .unwrap();
    assert_eq!(cases.len(), 60);
    for c in cases {
        let png = image::open(dir.join(c["png"].as_str().unwrap()))
            .unwrap()
            .to_rgb8();
        let band = Image::from_rgb(png.width() as usize, png.height() as usize, png.as_raw());
        let (lines, div) = title_scan(&band, &ScanOptions::default());
        let got_lines: Vec<Vec<usize>> = lines.iter().map(|l| vec![l.0, l.1, l.2, l.3]).collect();
        let want_lines: Vec<Vec<usize>> = serde_json::from_value(c["lines"].clone()).unwrap();
        assert_eq!(got_lines, want_lines, "{}", c["png"]);
        let want_div: Option<Vec<usize>> = serde_json::from_value(c["div"].clone()).unwrap();
        assert_eq!(div.map(|d| vec![d.0, d.1, d.2]), want_div, "{}", c["png"]);
    }
}
