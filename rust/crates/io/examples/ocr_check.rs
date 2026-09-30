//! Read the recorded title bands (`debug/frames/*_ocr/band.png`) with the
//! Rust reader and compare with what the Python host read.
//!
//!     cargo run --release -p picobot-io --example ocr_check -- ..\..\PicoBot-v2

use std::path::PathBuf;
use std::time::Instant;

use picobot_core::identity::match_title;
use picobot_core::maps::MapStore;
use picobot_core::title::normalize_name;
use picobot_core::vision::Image;
use picobot_io::ocr::{find_model, TitleReader};

fn main() {
    let root = PathBuf::from(std::env::args().nth(1).unwrap_or_else(|| ".".into()));
    let model = find_model(&root).expect("recogniser model not found");
    let t = Instant::now();
    let mut reader = TitleReader::load(&model).expect("load the model");
    println!("model loaded in {:.0} ms", t.elapsed().as_secs_f64() * 1e3);
    let mut dirs: Vec<PathBuf> = std::fs::read_dir(root.join("debug/frames"))
        .expect("debug/frames")
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.to_string_lossy().ends_with("_ocr") && p.join("band.png").is_file())
        .collect();
    dirs.sort();
    let mut store = MapStore::new(root.join("maps"));
    let mut reads: Vec<(String, String)> = Vec::new();
    let (mut same, mut total, mut ms, mut same_map) = (0, 0, 0.0, 0);
    for d in &dirs {
        let meta: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(d.join("meta.json")).unwrap_or_default())
                .unwrap_or_default();
        let want = meta["text"].as_str().unwrap_or("").to_owned();
        let png = image::open(d.join("band.png")).unwrap().to_rgb8();
        let band = Image::from_rgb(png.width() as usize, png.height() as usize, png.as_raw());
        let t = Instant::now();
        let got = reader.read(&band).unwrap_or_default();
        reads.push((want.clone(), got.clone()));
        ms += t.elapsed().as_secs_f64() * 1e3;
        total += 1;
        let ok = normalize_name(&got) == normalize_name(&want);
        same += ok as usize;
        let (mp, sp) = match_title(&mut store, &want);
        let (mr, sr) = match_title(&mut store, &got);
        same_map += (mp == mr) as usize;
        println!(
            "{} {:<40} | {:<40} map: {:?} {sp:.2} / {:?} {sr:.2}",
            if ok { "ok  " } else { "DIFF" },
            want,
            got,
            mp,
            mr
        );
    }
    // As if every title Python read were a stored map's title.
    let dir = std::env::temp_dir().join(format!("pb-ocr-check-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let mut titled = MapStore::new(&dir);
    let mut seen = std::collections::HashSet::new();
    for (want, _) in &reads {
        let clean = want.split_whitespace().collect::<Vec<_>>().join(" ");
        if !clean.is_empty() && seen.insert(normalize_name(&clean)) {
            let mut e = picobot_core::maps::MapEntry::new(&format!("m{}", seen.len()));
            e.map_name = Some(clean);
            titled.save(e).unwrap();
        }
    }
    let mut resolved = 0;
    for (want, got) in &reads {
        let (a, _) = match_title(&mut titled, want);
        let (b, sb) = match_title(&mut titled, got);
        if a.is_some() && a == b {
            resolved += 1;
        } else {
            println!("titled store: {want:?} -> {a:?}, rust {got:?} -> {b:?} ({sb:.2})");
        }
    }
    let _ = std::fs::remove_dir_all(&dir);
    println!("with titles stored: rust reads resolve to Python's map for {resolved}/{total}");
    println!("{same}/{total} identical after normalising; same map resolved for {same_map}/{total}; {:.1} ms per read", ms / total.max(1) as f64);
}
