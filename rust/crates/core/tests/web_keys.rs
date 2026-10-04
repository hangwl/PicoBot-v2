//! The dashboard's key list (`web/src/keys.ts` `PICO_KEYS`) and the
//! host's (`picobot_core::keys::PICO_KEYS`) must match what the Pico
//! firmware can press (`firmware/phase-e/k75/keymap.c` `KEY_MAP`).

use std::collections::BTreeSet;
use std::path::PathBuf;

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

/// Quoted string literals in `src` (either quote, backslash escapes),
/// each with the text right after it.
fn literals(src: &str) -> Vec<(String, &str)> {
    let mut out = Vec::new();
    let mut chars = src.char_indices().peekable();
    while let Some((_, c)) = chars.next() {
        if c == '#' {
            // A comment runs to the end of the line.
            while chars.next_if(|&(_, c)| c != '\n').is_some() {}
            continue;
        }
        if c != '\'' && c != '"' {
            continue;
        }
        let mut text = String::new();
        let mut end = src.len();
        while let Some((i, ch)) = chars.next() {
            match ch {
                '\\' => {
                    if let Some((_, esc)) = chars.next() {
                        text.push(esc);
                    }
                }
                _ if ch == c => {
                    end = i + 1;
                    break;
                }
                _ => text.push(ch),
            }
        }
        out.push((text, &src[end..]));
    }
    out
}

fn firmware_keys() -> BTreeSet<String> {
    let src = std::fs::read_to_string(repo().join("firmware/phase-e/k75/keymap.c")).unwrap();
    let start = src.find("KEY_MAP[] = {").expect("KEY_MAP in keymap.c");
    let block = &src[start..];
    let block = &block[..block.find("\n};").expect("end of KEY_MAP")];
    literals(block)
        .into_iter()
        .filter(|(_, rest)| rest.trim_start().starts_with(','))
        .map(|(k, _)| k)
        .collect()
}

fn dashboard_keys() -> BTreeSet<String> {
    let src = std::fs::read_to_string(repo().join("web/src/keys.ts")).unwrap();
    let body = &src[src.find("PICO_KEYS").expect("PICO_KEYS in keys.ts")..];
    let body = &body[body.find('[').unwrap() + 1..body.find("];").unwrap()];
    let mut keys = BTreeSet::new();
    for (text, _) in literals(body) {
        keys.insert(text);
    }
    // `..."abc"` spreads a string into its characters.
    if let Some(at) = body.find("...\"") {
        let spread = &body[at + 4..];
        let spread = &spread[..spread.find('"').unwrap()];
        keys.remove(spread);
        keys.extend(spread.chars().map(String::from));
    }
    keys
}

#[test]
fn the_dashboard_keys_match_the_firmware_key_map() {
    let (fw, web) = (firmware_keys(), dashboard_keys());
    assert!(fw.len() > 50, "parsed only {} firmware keys", fw.len());
    let only_fw: Vec<_> = fw.difference(&web).collect();
    let only_web: Vec<_> = web.difference(&fw).collect();
    assert!(
        only_fw.is_empty() && only_web.is_empty(),
        "firmware only: {only_fw:?}; dashboard only: {only_web:?}"
    );
}

#[test]
fn the_host_keys_match_the_firmware_key_map() {
    let fw = firmware_keys();
    let host: BTreeSet<String> = picobot_core::keys::PICO_KEYS
        .iter()
        .map(|k| k.to_string())
        .collect();
    let only_fw: Vec<_> = fw.difference(&host).collect();
    let only_host: Vec<_> = host.difference(&fw).collect();
    assert!(
        only_fw.is_empty() && only_host.is_empty(),
        "firmware only: {only_fw:?}; host only: {only_host:?}"
    );
}
