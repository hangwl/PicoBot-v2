//! Key names the Pico firmware can press (`KEY_MAP` in
//! `firmware/phase-e/k75/keymap.c`; `tests/web_keys.rs` keeps the three lists equal).

use std::collections::BTreeSet;

use crate::config::BotConfig;

#[rustfmt::skip]
pub const PICO_KEYS: &[&str] = &[
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
    "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "enter", "esc", "backspace", "tab", "space",
    "-", "=", "[", "]", "\\", ";", "'", "`", ",", ".", "/",
    "caps lock", "shift", "ctrl", "alt", "cmd", "windows",
    "right shift", "right ctrl", "right alt",
    "print screen", "scroll lock", "pause", "insert", "home", "page up",
    "delete", "end", "page down", "right", "left", "down", "up",
];

/// Every key a run with `cfg` may press: arrows, the move keys, the
/// rune key and the skill book (trimmed and lowercased, as the firmware
/// reads them; blanks skipped).
pub fn keys_used(cfg: &BotConfig) -> BTreeSet<String> {
    let mut keys: Vec<&str> = vec!["left", "right", "up", "down", &cfg.rune_key, &cfg.jump_key];
    keys.extend(
        [
            &cfg.up_jump_skill_key,
            &cfg.flash_jump_key,
            &cfg.teleport_key,
        ]
        .into_iter()
        .flatten()
        .map(String::as_str),
    );
    keys.extend(cfg.skills.iter().map(|s| s.key.as_str()));
    keys.into_iter()
        .map(|k| k.trim().to_lowercase())
        .filter(|k| !k.is_empty())
        .collect()
}

/// The keys of `cfg` that `known` lacks.
pub fn unpressable(cfg: &BotConfig, known: &BTreeSet<String>) -> Vec<String> {
    keys_used(cfg)
        .into_iter()
        .filter(|k| !known.contains(k))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::skills::Skill;

    #[test]
    fn a_config_uses_its_move_rune_and_skill_keys() {
        let cfg = BotConfig {
            rune_key: "Y ".into(),
            teleport_key: Some("".into()),
            skills: vec![Skill::new("hit", "a"), Skill::new("odd", "f13")],
            ..BotConfig::default()
        };
        let used = keys_used(&cfg);
        for k in ["left", "right", "up", "down", "y", "alt", "a", "f13"] {
            assert!(used.contains(k), "{k} missing from {used:?}");
        }
        assert!(!used.contains(""));
        let known: BTreeSet<String> = PICO_KEYS.iter().map(|k| k.to_string()).collect();
        assert_eq!(unpressable(&cfg, &known), ["f13"]);
    }
}
