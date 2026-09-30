//! Title matching against Python's difflib and `title_score`.

use picobot_core::fuzzy::{looks_like_sibling, ratio, title_score};
use serde_json::Value;

#[test]
fn fuzzy_matching_matches_python() {
    let path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/trace_fuzzy.json");
    let cases: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let mut bad = Vec::new();
    for c in &cases {
        let (ocr, cand) = (c["ocr"].as_str().unwrap(), c["cand"].as_str().unwrap());
        let got = (
            ratio(ocr, cand),
            looks_like_sibling(ocr, cand),
            title_score(ocr, cand),
        );
        let want = (
            c["ratio"].as_f64().unwrap(),
            c["sibling"].as_bool().unwrap(),
            c["score"].as_f64().unwrap(),
        );
        if (got.0 - want.0).abs() > 1e-12 || got.1 != want.1 || (got.2 - want.2).abs() > 1e-12 {
            bad.push(format!("{ocr:?} vs {cand:?}: got {got:?}, want {want:?}"));
        }
    }
    assert!(
        bad.is_empty(),
        "{} of {} differ:\n{}",
        bad.len(),
        cases.len(),
        bad[..bad.len().min(10)].join("\n")
    );
}
