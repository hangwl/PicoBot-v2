//! The Rust host must read and write the Python host's files unchanged.
//!
//! Fixtures under `tests/fixtures/` are written by the Python code
//! (`rust/tools/gen_fixtures.py`): each is loaded, saved back, and must come
//! out byte-identical. Set `PICOBOT_DATA` to a PicoBot folder to run the
//! same check over real `maps/`, `nav_reach*.json` and `config.json`.

use std::path::{Path, PathBuf};

use picobot_core::config::{AppConfig, BotConfig, ClassTravel, PatrolPolicy};
use picobot_core::json::dumps;
use picobot_core::maps::MapStore;
use picobot_core::reach::{base_reach, ReachModel};
use picobot_core::skills::skills_to_json;
use serde_json::Value;

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
}

fn read(path: &Path) -> String {
    std::fs::read_to_string(path)
        .unwrap_or_else(|e| panic!("{}: {e}", path.display()))
        .replace("\r\n", "\n")
}

/// Every map file in `dir` re-serialises to the same bytes.
fn maps_round_trip(dir: &Path) -> usize {
    let mut store = MapStore::new(dir);
    let entries = store.load_all().to_vec();
    assert!(
        store.skipped.is_empty(),
        "unreadable map files: {:?}",
        store.skipped
    );
    for e in &entries {
        let path = e.path.as_ref().unwrap();
        assert_eq!(
            dumps(&e.to_json(), 2),
            read(path).trim_end(),
            "{}",
            path.display()
        );
    }
    entries.len()
}

fn reach_round_trip(path: &Path) {
    let model = ReachModel::load(base_reach(&BotConfig::default()), path);
    assert_eq!(
        dumps(&model.to_json(), 2),
        read(path).trim_end(),
        "{}",
        path.display()
    );
}

#[test]
fn map_files_round_trip_byte_for_byte() {
    assert_eq!(maps_round_trip(&fixtures().join("maps")), 2);
}

#[test]
fn reach_file_round_trips_byte_for_byte() {
    reach_round_trip(&fixtures().join("nav_reach_erel.json"));
}

#[test]
fn config_round_trips_and_parses_like_python() {
    let path = fixtures().join("config.json");
    let (app, err) = AppConfig::load(&path);
    assert!(err.is_none());
    assert_eq!(dumps(&app.to_json(), 4), read(&path).trim_end());

    let bot = BotConfig::from_json(&app.bot).unwrap();
    let want: Value =
        serde_json::from_str(&read(&fixtures().join("config_bot_expected.json"))).unwrap();
    assert_eq!(bot.class_active, want["class_active"]);
    assert_eq!(bot.class_travel, ClassTravel::Flash);
    assert_eq!(want["class_travel"], "flash");
    assert_eq!(bot.air_attacks, want["air_attacks"]);
    assert_eq!(bot.jump_key, want["jump_key"]);
    assert_eq!(
        bot.flash_jump_key.as_deref(),
        want["flash_jump_key"].as_str()
    );
    assert_eq!(bot.teleport_key.as_deref(), want["teleport_key"].as_str());
    assert_eq!(bot.nav_threshold_px, want["nav_threshold_px"]);
    assert_eq!(bot.patrol_policy, PatrolPolicy::Greedy);
    assert_eq!(want["patrol_policy"], "greedy");
    assert_eq!(bot.patrol_weight_temp, want["patrol_weight_temp"]);
    assert_eq!(
        bot.minimap_colors.player.to_vec(),
        serde_json::from_value::<Vec<u8>>(want["player_color"].clone()).unwrap()
    );
    assert_eq!(bot.reach_path(), want["reach_path"]);
    assert_eq!(skills_to_json(&bot.skills), want["skills"]);
}

/// Real data, when `PICOBOT_DATA` points at a PicoBot folder. Config
/// values are never printed (the file holds a Telegram token).
#[test]
fn real_data_round_trips() {
    let Some(root) = std::env::var_os("PICOBOT_DATA").map(PathBuf::from) else {
        eprintln!("PICOBOT_DATA not set — skipping the real-data check");
        return;
    };
    let n = maps_round_trip(&root.join("maps"));
    eprintln!("{n} real map files round-trip");
    for entry in std::fs::read_dir(&root).unwrap().flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.starts_with("nav_reach") && name.ends_with(".json") {
            reach_round_trip(&entry.path());
            eprintln!("{name} round-trips");
        }
    }
    let path = root.join("config.json");
    if path.exists() {
        let (app, err) = AppConfig::load(&path);
        assert!(err.is_none(), "config.json failed to load");
        let ours: Value = serde_json::from_str(&dumps(&app.to_json(), 4)).unwrap();
        let theirs: Value = serde_json::from_str(&read(&path)).unwrap();
        let differing: Vec<_> = theirs
            .as_object()
            .unwrap()
            .keys()
            .filter(|k| ours.get(*k) != theirs.get(*k))
            .collect();
        assert!(
            differing.is_empty(),
            "config.json keys differ after a round trip: {differing:?}"
        );
        let text_matches = dumps(&app.to_json(), 4) == read(&path).trim_end();
        assert!(text_matches, "config.json text differs (values match)");
        BotConfig::from_json(&app.bot).expect("the bot block parses");
        eprintln!("config.json round-trips and its bot block parses");
    }
}
