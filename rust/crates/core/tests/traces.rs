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

#[test]
fn navigation_graph_matches_python_on_random_maps() {
    use picobot_core::navgraph::{Direction, GraphOptions, MoveKind, NavGraph};
    use picobot_core::reach::Reach;

    let seg = |v: &Value| -> [f64; 4] {
        let a = v.as_array().unwrap();
        [0, 1, 2, 3].map(|i| a[i].as_f64().unwrap())
    };
    let pair = |v: &Value| (v[0].as_f64().unwrap(), v[1].as_f64().unwrap());
    let reach_of = |v: &Value| Reach {
        dx: v[0].as_f64().unwrap_or(f64::INFINITY),
        rise: v[1].as_f64().unwrap_or(f64::INFINITY),
    };
    let kind_of = |s: &str| match s {
        "rope_lift" => MoveKind::RopeLift,
        "teleport" => MoveKind::Teleport,
        other => panic!("unexpected exclude {other}"),
    };

    for (gi, g) in load("trace_nav.json")
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        let mut m = ReachModel::new(base_reach(&BotConfig::default()), None);
        for (k, v) in g["base"].as_object().unwrap() {
            m.base[Move::parse(k).unwrap() as usize] = reach_of(v);
        }
        for (k, v) in g["est"].as_object().unwrap() {
            m.est[Move::parse(k).unwrap() as usize] = reach_of(v);
        }
        m.ceiling = g["ceiling"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (Move::parse(k).unwrap(), reach_of(v)))
            .collect();
        m.explore = g["explore"].as_f64().unwrap();
        let plats: Vec<[f64; 4]> = g["platforms"].as_array().unwrap().iter().map(seg).collect();
        let ropes: Vec<[f64; 4]> = g["ropes"].as_array().unwrap().iter().map(seg).collect();
        let opts = GraphOptions {
            rope_penalty: g["penalty"].as_f64().unwrap(),
            allow_flash: g["allow_flash"].as_bool().unwrap(),
            allow_teleport: g["allow_teleport"].as_bool().unwrap(),
            rope_clear_px: 0.0,  // the Python host had no rope clearance
            prefer_jumps: false, // nor jumps over rope lift
            ..Default::default()
        };
        let graph = NavGraph::new(&plats, &ropes, &m, opts);

        // Every transfer edge, in Python's sorted order.
        let mut edges: Vec<(String, [f64; 5])> = graph
            .transfer_legs()
            .iter()
            .map(|l| (l.kind.as_str().to_owned(), [l.x0, l.y0, l.x1, l.y1, l.cost]))
            .collect();
        edges.sort_by(|a, b| {
            a.0.cmp(&b.0).then_with(|| {
                a.1.iter()
                    .zip(&b.1)
                    .map(|(x, y)| x.total_cmp(y))
                    .find(|o| o.is_ne())
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
        });
        let want = g["edges"].as_array().unwrap();
        assert_eq!(edges.len(), want.len(), "graph {gi}: edge count");
        for (e, w) in edges.iter().zip(want) {
            assert_eq!(e.0, w[0].as_str().unwrap(), "graph {gi}: {e:?} vs {w}");
            for (k, v) in e.1.iter().enumerate() {
                assert!(
                    close(*v, w[k + 1].as_f64().unwrap()),
                    "graph {gi}: {e:?} vs {w}"
                );
            }
        }

        for r in g["routes"].as_array().unwrap() {
            let exclude: Vec<MoveKind> = r["exclude"]
                .as_array()
                .unwrap()
                .iter()
                .map(|k| kind_of(k.as_str().unwrap()))
                .collect();
            let cost = graph.route_cost(pair(&r["from"]), pair(&r["to"]), &exclude);
            match r["cost"].as_f64() {
                None => assert!(
                    cost.is_infinite(),
                    "graph {gi}: route {r} found cost {cost}"
                ),
                Some(w) => assert!(close(cost, w), "graph {gi}: route {r} cost {cost}"),
            }
        }

        for q in g["queries"].as_array().unwrap() {
            let (x, y) = pair(&q["at"]);
            let idx = |v: &Value| v.as_u64().map(|i| i as usize);
            assert_eq!(graph.locate(x, y), idx(&q["locate"]), "graph {gi}: {q}");
            assert_eq!(graph.above(x, y, None), idx(&q["above"]), "graph {gi}: {q}");
            assert_eq!(graph.below(x, y, None), idx(&q["below"]), "graph {gi}: {q}");
            let ha = graph.highest_above(x, y, 30.0, None);
            match q["highest30"].as_array() {
                None => assert!(ha.is_none(), "graph {gi}: {q}"),
                Some(w) => {
                    let (j, rise) = ha.unwrap_or_else(|| panic!("graph {gi}: {q}"));
                    assert!(
                        j as u64 == w[0].as_u64().unwrap() && close(rise, w[1].as_f64().unwrap()),
                        "graph {gi}: {q}"
                    );
                }
            }
            if let Some(exit) = q["exit"].as_str() {
                let want = if exit == "right" {
                    Direction::Right
                } else {
                    Direction::Left
                };
                assert_eq!(graph.exit_direction(x, y), want, "graph {gi}: {q}");
            }
        }
    }
}
