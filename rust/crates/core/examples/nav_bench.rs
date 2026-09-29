//! Graph build + all-pairs anchor routing over a data folder's maps.
//!
//! ```text
//! cargo run --release -p picobot-core --example nav_bench -- <PicoBot folder> [reach file]
//! ```
//! Compare with `rust/tools/nav_bench.py` (same work in the Python host).

use std::path::PathBuf;
use std::time::Instant;

use picobot_core::config::BotConfig;
use picobot_core::maps::MapStore;
use picobot_core::navgraph::{graph_for, GraphOptions};
use picobot_core::reach::{base_reach, ReachModel};
use picobot_core::rotation::resolve_coord;

fn main() {
    let mut args = std::env::args().skip(1);
    let root = PathBuf::from(
        args.next()
            .expect("usage: nav_bench <PicoBot folder> [reach file]"),
    );
    let reach_file = args.next().unwrap_or_else(|| "nav_reach_erel.json".into());
    let reach = ReachModel::load(base_reach(&BotConfig::default()), root.join(reach_file));
    let mut store = MapStore::new(root.join("maps"));
    let maps: Vec<_> = store
        .load_all()
        .iter()
        .filter(|e| e.platforms.is_some())
        .cloned()
        .collect();

    let rounds = 50;
    let (mut builds, mut routes) = (0u32, 0u32);
    let (mut t_build, mut t_route) = (0.0f64, 0.0f64);
    let mut checksum = 0.0;
    for _ in 0..rounds {
        for e in &maps {
            let (w, h) = e
                .minimap_region
                .map_or((200.0, 150.0), |r| (r[2] as f64, r[3] as f64));
            let t = Instant::now();
            let g = graph_for(e, (w, h), &reach, GraphOptions::default()).unwrap();
            t_build += t.elapsed().as_secs_f64();
            builds += 1;
            let pts: Vec<(f64, f64)> = e
                .rotation
                .anchors
                .iter()
                .map(|a| {
                    (
                        resolve_coord(a.x, w as i64) as f64,
                        resolve_coord(a.y, h as i64) as f64,
                    )
                })
                .collect();
            let t = Instant::now();
            for a in &pts {
                for b in &pts {
                    let c = g.route_cost(*a, *b, &[]);
                    if c.is_finite() {
                        checksum += c;
                    }
                    routes += 1;
                }
            }
            t_route += t.elapsed().as_secs_f64();
        }
    }
    println!(
        "rust: {} maps, graph build {:.1} us, route {:.1} us (checksum {:.3})",
        maps.len(),
        t_build / builds as f64 * 1e6,
        t_route / routes as f64 * 1e6,
        checksum / rounds as f64
    );
}
