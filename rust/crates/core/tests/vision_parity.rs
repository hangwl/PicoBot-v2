//! Vision parity on real recorded frames (opt-in: the frames are the
//! user's own captures and aren't committed).
//!
//! ```text
//! python rust/tools/vision_trace.py <PicoBot folder> trace.json
//! $env:PICOBOT_VISION_TRACE = "trace.json"; cargo test -p picobot-core --test vision_parity
//! ```

use std::path::Path;

use picobot_core::config::MinimapColors;
use picobot_core::minimap::{MinimapAnalyzer, PlayerTracker, TransitionDetector, TransitionEvent};
use picobot_core::vision::{find_frame, is_dark, Image};
use serde_json::Value;

fn load_png(path: &Path) -> Image {
    let img = image::open(path)
        .unwrap_or_else(|e| panic!("{}: {e}", path.display()))
        .to_rgb8();
    Image::from_rgb(img.width() as usize, img.height() as usize, img.as_raw())
}

fn point(v: &Value) -> Option<(i32, i32)> {
    let a = v.as_array()?;
    Some((a[0].as_i64()? as i32, a[1].as_i64()? as i32))
}

#[test]
fn real_frames_match_python() {
    let Some(trace_path) = std::env::var_os("PICOBOT_VISION_TRACE") else {
        eprintln!("PICOBOT_VISION_TRACE not set — skipping the real-frame check");
        return;
    };
    let trace: Value = serde_json::from_str(&std::fs::read_to_string(trace_path).unwrap()).unwrap();
    let root = Path::new(trace["root"].as_str().unwrap());
    let colors = MinimapColors::default();
    let (mut frames, mut dots) = (0, 0);

    for w in trace["windows"].as_array().unwrap() {
        let img = load_png(&root.join(w["path"].as_str().unwrap()));
        let want = w["frame"].as_array().map(|a| {
            let v: Vec<i32> = a.iter().map(|x| x.as_i64().unwrap() as i32).collect();
            (v[0], v[1], v[2], v[3])
        });
        assert_eq!(find_frame(&img, colors.border, 10), want, "{}", w["path"]);
    }

    for seq in trace["sequences"].as_array().unwrap() {
        let mut det = TransitionDetector::default();
        let mm = MinimapAnalyzer::new(colors, None, 4);
        let mut tracker = PlayerTracker::default();
        for s in seq["steps"].as_array().unwrap() {
            let ctx = s["path"].as_str().unwrap();
            let img = load_png(&root.join(ctx));
            assert_eq!(
                is_dark(&img, 12.0),
                s["dark"].as_bool().unwrap(),
                "{ctx}: dark"
            );
            let event = det.note(&img, s["t"].as_f64().unwrap()).map(|e| match e {
                TransitionEvent::Loading => "loading",
                TransitionEvent::Arrived => "arrived",
            });
            assert_eq!(event, s["event"].as_str(), "{ctx}: event");
            assert_eq!(
                det.state.as_str(),
                s["state"].as_str().unwrap(),
                "{ctx}: state"
            );
            let player = mm.player_pos(&img, &mut tracker);
            assert_eq!(player, point(&s["player"]), "{ctx}: player");
            assert_eq!(mm.rune_pos(&img), point(&s["rune"]), "{ctx}: rune");
            assert_eq!(
                mm.has_other_players(&img),
                s["others"].as_bool().unwrap(),
                "{ctx}: others"
            );
            frames += 1;
            dots += player.is_some() as usize;
        }
    }

    for s in trace["stills"].as_array().unwrap() {
        let ctx = s["path"].as_str().unwrap();
        let img = load_png(&root.join(ctx));
        let mm = MinimapAnalyzer::new(colors, None, 4);
        assert_eq!(
            is_dark(&img, 12.0),
            s["dark"].as_bool().unwrap(),
            "{ctx}: dark"
        );
        assert_eq!(
            mm.player_pos(&img, &mut PlayerTracker::default()),
            point(&s["player"]),
            "{ctx}: player"
        );
        assert_eq!(mm.rune_pos(&img), point(&s["rune"]), "{ctx}: rune");
        assert_eq!(
            mm.has_other_players(&img),
            s["others"].as_bool().unwrap(),
            "{ctx}: others"
        );
    }
    eprintln!("{frames} sequence frames ({dots} dots), windows and stills match Python");
}
