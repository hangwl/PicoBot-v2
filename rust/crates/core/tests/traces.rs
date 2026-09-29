//! Behaviour parity: seeded random operations were run through the Python
//! logic (`rust/tools/gen_fixtures.py`) with every result recorded; each
//! test replays them here and must agree at every step.

use std::path::Path;

use picobot_core::config::BotConfig;
use picobot_core::platform_fit::{tidy_segments, PlatformFit};
use picobot_core::reach::{base_reach, Move, ReachModel};
use picobot_core::skills::{skills_from_json, SkillBook};
use serde_json::Value;

fn load(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join(name);
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-6
}

fn opt(v: &Value) -> Option<f64> {
    v.as_f64()
}

#[test]
fn reach_learning_matches_python_step_by_step() {
    let trace = load("trace_reach.json");
    let mut m = ReachModel::new(base_reach(&BotConfig::default()), None);
    let ops = trace["ops"].as_array().unwrap();
    let states = trace["states"].as_array().unwrap();
    for (step, (op, want)) in ops.iter().zip(states).enumerate() {
        let mv = Move::parse(op[1].as_str().unwrap()).unwrap();
        match op[0].as_str().unwrap() {
            "calibrate" => m.calibrate(mv, opt(&op[2]), opt(&op[3]), None),
            _ => {
                let pair = |v: &Value| (v[0].as_f64().unwrap(), v[1].as_f64().unwrap());
                m.observe(mv, pair(&op[2]), pair(&op[3]), op[4].as_bool().unwrap());
            }
        }
        for row in want.as_array().unwrap() {
            let mv = Move::parse(row[0].as_str().unwrap()).unwrap();
            let e = m.get(mv);
            let ctx = format!("step {step} ({op}), {}", mv.as_str());
            assert!(close(e.dx, row[1].as_f64().unwrap()), "{ctx}: dx {}", e.dx);
            assert!(
                close(e.rise, row[2].as_f64().unwrap()),
                "{ctx}: rise {}",
                e.rise
            );
            let c = m.ceiling_of(mv);
            match row.get(3) {
                None => assert!(c.is_none(), "{ctx}: unexpected ceiling {c:?}"),
                Some(cdx) => {
                    let c = c.unwrap_or_else(|| panic!("{ctx}: missing ceiling"));
                    let side = |v: &Value, got: f64| match v.as_f64() {
                        None => got.is_infinite(),
                        Some(w) => close(w, got),
                    };
                    assert!(
                        side(cdx, c.dx) && side(&row[4], c.rise),
                        "{ctx}: ceiling {c:?}"
                    );
                }
            }
        }
    }
}

#[test]
fn skill_book_matches_python_step_by_step() {
    let trace = load("trace_skills.json");
    let mut book = SkillBook::new(skills_from_json(&trace["skills"]).unwrap());
    for (i, step) in trace["steps"].as_array().unwrap().iter().enumerate() {
        let name = step[1].as_str().unwrap();
        let now = step[2].as_f64().unwrap();
        if step[0] == "use" {
            book.mark_used(name, now);
            continue;
        }
        let ctx = format!("step {i} ({step})");
        assert_eq!(
            book.charges(name, now),
            step[3].as_u64().unwrap() as u32,
            "{ctx}"
        );
        assert!(
            close(book.remaining(name, now), step[4].as_f64().unwrap()),
            "{ctx}"
        );
        let names = |v: Vec<picobot_core::skills::Skill>| {
            let mut n: Vec<String> = v.into_iter().map(|s| s.name).collect();
            n.sort();
            n
        };
        let want = |v: &Value| -> Vec<String> {
            v.as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_str().unwrap().to_owned())
                .collect()
        };
        assert_eq!(names(book.ready_attacks(now)), want(&step[5]), "{ctx}");
        assert_eq!(names(book.due_buffs(now)), want(&step[6]), "{ctx}");
    }
}

#[test]
fn tidy_matches_python() {
    let seg = |v: &Value| -> [f64; 4] {
        let a = v.as_array().unwrap();
        [0, 1, 2, 3].map(|i| a[i].as_f64().unwrap())
    };
    for (i, case) in load("trace_tidy.json")
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        let input: Vec<[f64; 4]> = case["in"].as_array().unwrap().iter().map(seg).collect();
        let want: Vec<[f64; 4]> = case["out"].as_array().unwrap().iter().map(seg).collect();
        let got = tidy_segments(&input);
        assert_eq!(got.len(), want.len(), "case {i}: {input:?}");
        for (g, w) in got.iter().zip(&want) {
            assert!(
                g.iter().zip(w).all(|(a, b)| close(*a, *b)),
                "case {i}: {got:?} vs {want:?}"
            );
        }
    }
}

#[test]
fn platform_fit_matches_python() {
    let trace = load("trace_fit.json");
    let segs: Vec<[f64; 4]> = trace["segs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| {
            let a = s.as_array().unwrap();
            [0, 1, 2, 3].map(|i| a[i].as_f64().unwrap())
        })
        .collect();
    let wh = Some((200.0, 100.0));
    let mut fit = PlatformFit::default();
    for item in trace["feed"].as_array().unwrap() {
        let t = item[0].as_f64().unwrap();
        let pos = (item[1][0].as_f64().unwrap(), item[1][1].as_f64().unwrap());
        fit.observe(Some("m"), &segs, wh, Some(pos), t);
    }
    let got = serde_json::to_value(fit.summary(Some("m"), &segs, wh)).unwrap();
    assert_eq!(got, trace["summary"]);
}
