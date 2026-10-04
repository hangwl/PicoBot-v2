//! The Python host's navigation-graph tests, ported.

use picobot_core::config::BotConfig;
use picobot_core::maps::MapEntry;
use picobot_core::navgraph::{graph_for, Direction, GraphOptions, Leg, MoveKind, NavGraph};
use picobot_core::reach::{base_reach, Move, Reach, ReachModel};
use rand::rngs::StdRng;
use rand::SeedableRng;

const FLOOR: [f64; 4] = [0.0, 100.0, 200.0, 100.0];
const MID: [f64; 4] = [40.0, 84.0, 120.0, 84.0]; // 16 above the floor
const TOP: [f64; 4] = [60.0, 66.0, 100.0, 66.0]; // 18 above MID
const SIDE: [f64; 4] = [130.0, 84.0, 190.0, 84.0]; // 10px gap right of MID

/// The Python suite's reach: explore 1.0 unless given.
fn reach_with(explore: f64, over: &[(Move, Reach)]) -> ReachModel {
    let mut base = base_reach(&BotConfig::default());
    let r = |dx, rise| Reach { dx, rise };
    for (m, v) in [
        (Move::Jump, r(8.0, 4.0)),
        (Move::Flash, r(25.0, 4.0)),
        (Move::DoubleFlash, r(40.0, 4.0)),
        (Move::UpFlash, r(6.0, 20.0)),
        (Move::UpSideFlash, r(25.0, 16.0)),
        (Move::RopeLift, r(3.0, 30.0)),
        (Move::Teleport, r(25.0, 12.0)),
    ]
    .into_iter()
    .chain(over.iter().copied())
    {
        base[m as usize] = v;
    }
    let mut m = ReachModel::new(base, None);
    m.explore = explore;
    m
}

fn reach() -> ReachModel {
    reach_with(1.0, &[])
}

fn graph(plats: &[[f64; 4]], reach: &ReachModel) -> NavGraph {
    NavGraph::new(plats, &[], reach, GraphOptions::default())
}

fn with_ropes(plats: &[[f64; 4]], ropes: &[[f64; 4]], opts: GraphOptions) -> NavGraph {
    NavGraph::new(plats, ropes, &reach(), opts)
}

fn kinds(legs: &[Leg]) -> Vec<&'static str> {
    legs.iter()
        .filter(|l| l.kind != MoveKind::Walk)
        .map(|l| l.kind.as_str())
        .collect()
}

fn transfers(g: &NavGraph, kind: MoveKind) -> Vec<Leg> {
    g.transfer_legs()
        .into_iter()
        .filter(|l| l.kind == kind)
        .collect()
}

#[test]
fn climbs_tier_by_tier_with_up_flash() {
    let no_rope = reach_with(1.0, &[(Move::RopeLift, Reach { dx: 0.0, rise: 0.0 })]);
    let legs = graph(&[FLOOR, MID, TOP], &no_rope)
        .route((10.0, 100.0), (80.0, 66.0), &[])
        .unwrap();
    assert_eq!(kinds(&legs), ["up_flash", "up_flash"]);
    let last = legs.last().unwrap();
    assert_eq!((last.x1, last.y1), (80.0, 66.0));
}

#[test]
fn rope_lift_grabs_the_highest_platform_in_range() {
    let m = reach_with(
        1.0,
        &[(
            Move::RopeLift,
            Reach {
                dx: 3.0,
                rise: 90.0,
            },
        )],
    );
    let g = graph(&[FLOOR, MID, TOP, [60.0, 0.0, 100.0, 0.0]], &m);
    let mut rows: Vec<f64> = transfers(&g, MoveKind::RopeLift)
        .iter()
        .filter(|l| l.y0 == 100.0)
        .map(|l| l.y1)
        .collect();
    rows.sort_by(f64::total_cmp);
    rows.dedup();
    // Under TOP it grabs TOP (34px up — no jump reaches it); beside TOP the
    // highest in range is MID, which an up flash reaches, so no rope lift.
    assert_eq!(rows, [66.0]);
    assert_eq!(
        kinds(&g.route((10.0, 100.0), (80.0, 66.0), &[]).unwrap()),
        ["rope_lift"]
    );
    assert_eq!(
        kinds(
            &g.route((10.0, 100.0), (80.0, 66.0), &[MoveKind::RopeLift])
                .unwrap()
        ),
        ["up_flash", "up_flash"]
    );
}

#[test]
fn a_platform_a_jump_reaches_gets_no_rope_lift() {
    let g = graph(&[FLOOR, MID], &reach());
    assert!(transfers(&g, MoveKind::RopeLift).is_empty());
    assert_eq!(
        kinds(&g.route((10.0, 100.0), (80.0, 84.0), &[]).unwrap()),
        ["up_flash"]
    );
    // Not preferring jumps, and priced as cheaply as it used to be, the
    // rope lift is used while ready.
    let opts = GraphOptions {
        prefer_jumps: false,
        rope_lift_cost: 0.5,
        ..GraphOptions::default()
    };
    let g = NavGraph::new(&[FLOOR, MID], &[], &reach(), opts);
    assert_eq!(
        kinds(&g.route((10.0, 100.0), (80.0, 84.0), &[]).unwrap()),
        ["rope_lift"]
    );
    assert_eq!(
        kinds(
            &g.route((10.0, 100.0), (80.0, 84.0), &[MoveKind::RopeLift])
                .unwrap()
        ),
        ["up_flash"]
    );
}

#[test]
fn a_tall_rise_needs_rope_lift() {
    let g = graph(&[FLOOR, [60.0, 70.0, 100.0, 70.0]], &reach());
    assert_eq!(
        kinds(&g.route((80.0, 100.0), (80.0, 70.0), &[]).unwrap()),
        ["rope_lift"]
    );
    assert!(g
        .route((80.0, 100.0), (80.0, 70.0), &[MoveKind::RopeLift])
        .is_none());
}

#[test]
fn up_side_flash_goes_up_and_over() {
    let g = graph(
        &[[0.0, 100.0, 100.0, 100.0], [110.0, 86.0, 160.0, 86.0]],
        &reach(),
    );
    assert_eq!(
        kinds(&g.route((50.0, 100.0), (140.0, 86.0), &[]).unwrap()),
        ["up_side_flash"]
    );
}

#[test]
fn a_class_without_double_flash_gets_no_double_flash_edges() {
    let wide = [150.0, 84.0, 190.0, 84.0];
    let opts = GraphOptions {
        allow_double_flash: false,
        ..GraphOptions::default()
    };
    let g = with_ropes(&[MID, wide], &[], opts);
    assert!(g.route((60.0, 84.0), (170.0, 84.0), &[]).is_none());
}

#[test]
fn gap_moves_land_where_their_full_carry_takes_them() {
    // 25px gap: a flash (25) can't make it from 8px inside MID; a double
    // flash can.
    let wide = [145.0, 84.0, 190.0, 84.0];
    assert_eq!(
        kinds(
            &graph(&[MID, wide], &reach())
                .route((60.0, 84.0), (170.0, 84.0), &[])
                .unwrap()
        ),
        ["double_flash"]
    );
    // 10px gap: whichever move, it covers its full carry and comes down as
    // near SIDE's middle as MID lets it take off — never at the near edge.
    let legs = graph(&[MID, SIDE], &reach())
        .route((60.0, 84.0), (170.0, 84.0), &[])
        .unwrap();
    let gap = legs.iter().find(|l| l.kind != MoveKind::Walk).unwrap();
    let carry = match gap.kind {
        MoveKind::Flash => 25.0,
        MoveKind::DoubleFlash => 40.0,
        k => panic!("{k:?}"),
    };
    assert_eq!(gap.x1 - gap.x0, carry);
    let middle = (SIDE[0] + SIDE[2]) / 2.0;
    let from_edge = MID[2] - 8.0 + carry; // the takeoff nearest MID's edge
    assert_eq!(gap.x1, middle.min(from_edge));
    assert!(gap.x1 - SIDE[0] >= 6.0);
}

#[test]
fn a_carried_move_that_would_overshoot_its_platform_is_not_linked() {
    // From a 10px stub, a flash (25) can't take off far enough back for a
    // 10px ledge 5px away: it flies past it. From MID it backs up and lands.
    let onto = |g: &NavGraph, x0: f64, x1: f64| {
        transfers(g, MoveKind::Flash)
            .iter()
            .any(|l| (x0..=x1).contains(&l.x1) && l.y1 == 84.0)
    };
    let (stub, ledge) = ([110.0, 84.0, 120.0, 84.0], [125.0, 84.0, 135.0, 84.0]);
    assert!(!onto(&graph(&[stub, ledge], &reach()), 125.0, 135.0));
    let far_ledge = [128.0, 84.0, 144.0, 84.0];
    assert!(onto(&graph(&[MID, far_ledge], &reach()), 128.0, 144.0));
}

#[test]
fn exploration_edges_cost_more_and_a_ceiling_removes_them() {
    // Gap moves stretching their reach (the near-edge model: a carried move
    // can't stretch, so it isn't explored).
    let opts = GraphOptions {
        fixed_carry: false,
        ..GraphOptions::default()
    };
    let graph = |segs: &[[f64; 4]], m: &ReachModel| NavGraph::new(segs, &[], m, opts);
    let far = [140.0, 84.0, 190.0, 84.0]; // needs 27 > flash 25
    let mut m = reach_with(1.15, &[(Move::DoubleFlash, Reach { dx: 0.0, rise: 0.0 })]);
    m.typical_carry = false;
    let legs = graph(&[MID, far], &m)
        .route((60.0, 84.0), (170.0, 84.0), &[])
        .unwrap();
    assert_eq!(kinds(&legs), ["flash"]);
    let jump = legs.iter().find(|l| l.kind == MoveKind::Flash).unwrap();
    assert!(jump.cost > 0.8); // exploration penalty
    m.observe(Move::Flash, (jump.x1 - jump.x0, 0.0), (10.0, -16.0), false);
    assert!(graph(&[MID, far], &m)
        .route((60.0, 84.0), (170.0, 84.0), &[])
        .is_none());
}

#[test]
fn descends_without_climbing_and_down_jumps_land_on_the_next_tier() {
    let g = graph(&[FLOOR, MID, TOP], &reach());
    let legs = g.route((80.0, 66.0), (10.0, 100.0), &[]).unwrap();
    assert!(!kinds(&legs)
        .iter()
        .any(|k| *k == "up_flash" || *k == "rope_lift"));
    assert_eq!(
        (legs.last().unwrap().x1, legs.last().unwrap().y1),
        (10.0, 100.0)
    );
    for l in transfers(&g, MoveKind::DownJump)
        .iter()
        .filter(|l| l.y0 == 66.0)
    {
        assert_eq!(l.y1, 84.0);
    }
}

#[test]
fn unreachable_off_platform_and_same_platform() {
    let g = graph(&[FLOOR, [60.0, 40.0, 100.0, 40.0]], &reach()); // 60 up: nothing reaches
    assert!(g.route((80.0, 100.0), (80.0, 40.0), &[]).is_none());
    assert_eq!(
        g.route_cost((80.0, 100.0), (80.0, 40.0), &[]),
        f64::INFINITY
    );
    assert!(g.route((80.0, 30.0), (10.0, 100.0), &[]).is_none()); // mid-air start
    let legs = graph(&[FLOOR, MID], &reach())
        .route((10.0, 100.0), (190.0, 100.0), &[])
        .unwrap();
    assert_eq!(
        legs.iter().map(|l| l.kind).collect::<Vec<_>>(),
        [MoveKind::Walk]
    );
}

#[test]
fn jitter_varies_between_equal_routes() {
    let no_rope = reach_with(1.0, &[(Move::RopeLift, Reach { dx: 0.0, rise: 0.0 })]);
    let g = graph(
        &[
            FLOOR,
            [20.0, 84.0, 60.0, 84.0],
            [140.0, 84.0, 180.0, 84.0],
            [40.0, 68.0, 160.0, 68.0],
        ],
        &no_rope,
    );
    let mut via = std::collections::HashSet::new();
    for seed in 0..40 {
        let mut rng = StdRng::seed_from_u64(seed);
        let legs = g
            .route_jittered((100.0, 100.0), (100.0, 68.0), 0.3, &mut rng, &[])
            .unwrap();
        let first_up = legs.iter().find(|l| l.kind == MoveKind::UpFlash).unwrap();
        via.insert(first_up.x0 < 100.0);
    }
    assert_eq!(via.len(), 2);
}

#[test]
fn teleport_edges_exist_only_for_teleport_kits() {
    let plats = [FLOOR, MID, [135.0, 84.0, 190.0, 84.0]];
    let m = reach_with(
        1.0,
        &[(
            Move::Teleport,
            Reach {
                dx: 30.0,
                rise: 12.0,
            },
        )],
    );
    let mage = NavGraph::new(
        &plats,
        &[],
        &m,
        GraphOptions {
            allow_flash: false,
            allow_teleport: true,
            ..Default::default()
        },
    );
    let seen: Vec<MoveKind> = mage.transfer_legs().iter().map(|l| l.kind).collect();
    assert!(seen.contains(&MoveKind::Teleport));
    assert!(!seen.contains(&MoveKind::Flash) && !seen.contains(&MoveKind::UpFlash));
    let hero = graph(&plats, &reach());
    assert!(transfers(&hero, MoveKind::Teleport).is_empty());
}

#[test]
fn ropes_link_platforms_as_a_last_resort() {
    let tall = [80.0, 40.0, 120.0, 40.0];
    let g = with_ropes(
        &[FLOOR, tall],
        &[[98.0, 100.0, 102.0, 40.0]],
        GraphOptions::default(),
    );
    assert_eq!(
        kinds(&g.route((50.0, 100.0), (100.0, 40.0), &[]).unwrap()),
        ["climb_up"]
    );
    assert!(!transfers(&g, MoveKind::ClimbDown).is_empty());

    let g = with_ropes(
        &[FLOOR, MID],
        &[[80.0, 100.0, 82.0, 84.0]],
        GraphOptions::default(),
    );
    assert!(transfers(&g, MoveKind::ClimbUp)[0].cost >= 5.0);
}

#[test]
fn rope_boarding_is_a_moving_jump_grab() {
    let tall = [80.0, 40.0, 120.0, 40.0];
    // Hanging 20px above the floor: grabbed from the floor.
    let g = with_ropes(
        &[FLOOR, tall],
        &[[98.0, 80.0, 102.0, 40.0]],
        GraphOptions::default(),
    );
    let ups = transfers(&g, MoveKind::ClimbUp);
    assert!(!ups.is_empty() && ups.iter().all(|l| l.y0 == 100.0));
    // 30px up: beyond a jump-grab.
    let g = with_ropes(
        &[FLOOR, tall],
        &[[98.0, 70.0, 102.0, 40.0]],
        GraphOptions::default(),
    );
    assert!(transfers(&g, MoveKind::ClimbUp).is_empty());
    assert!(g.route((50.0, 100.0), (100.0, 40.0), &[]).is_none());
    // From beside the rope, never straight up.
    let g = with_ropes(
        &[FLOOR, tall],
        &[[100.0, 80.0, 100.0, 40.0]],
        GraphOptions::default(),
    );
    let ups = transfers(&g, MoveKind::ClimbUp);
    assert!(!ups.is_empty() && ups.iter().all(|l| (l.x1 - l.x0).abs() == 6.0));
}

#[test]
fn a_rope_between_stacked_platforms_keeps_a_bottom_gap() {
    let g = with_ropes(
        &[FLOOR, [80.0, 40.0, 120.0, 40.0]],
        &[[100.0, 100.0, 100.0, 40.0]],
        GraphOptions::default(),
    );
    assert!(!transfers(&g, MoveKind::ClimbDown).is_empty());
    let ups = transfers(&g, MoveKind::ClimbUp);
    assert!(!ups.is_empty() && ups.iter().all(|l| l.y0 == 100.0));
}

#[test]
fn narrow_ledges_and_the_flash_kit() {
    let tall = [80.0, 40.0, 120.0, 40.0];
    let g = with_ropes(
        &[[98.0, 100.0, 104.0, 100.0], tall],
        &[[101.0, 90.0, 101.0, 40.0]],
        GraphOptions::default(),
    );
    let offsets: Vec<f64> = transfers(&g, MoveKind::ClimbUp)
        .iter()
        .map(|l| (l.x1 - l.x0).abs())
        .collect();
    assert_eq!(offsets, [0.0]);

    let plats = [[20.0, 100.0, 85.0, 100.0], tall]; // ends 15px short of the rope
    let rope = [[100.0, 85.0, 100.0, 40.0]];
    let flash = with_ropes(&plats, &rope, GraphOptions::default());
    let walk = with_ropes(
        &plats,
        &rope,
        GraphOptions {
            allow_flash: false,
            ..Default::default()
        },
    );
    assert!(!transfers(&flash, MoveKind::ClimbUp).is_empty());
    assert!(transfers(&walk, MoveKind::ClimbUp).is_empty());
}

#[test]
fn exit_direction_and_ropes_need_two_ends() {
    let g = graph(
        &[[20.0, 30.0, 50.0, 30.0], [70.0, 80.0, 150.0, 80.0]],
        &reach(),
    );
    assert_eq!(g.exit_direction(60.0, 60.0), Direction::Right);
    let g = with_ropes(
        &[FLOOR],
        &[[98.0, 100.0, 102.0, 40.0]],
        GraphOptions::default(),
    );
    assert!(g.transfer_legs().is_empty());
}

#[test]
fn graph_for_scales_normalised_platforms() {
    let mut entry = MapEntry::new("m");
    entry.platforms = Some(vec![[0.0, 0.5, 1.0, 0.5]]);
    let g = graph_for(&entry, (200.0, 100.0), &reach(), GraphOptions::default()).unwrap();
    assert_eq!((g.platforms[0].x1, g.platforms[0].y0), (200.0, 50.0));
    assert!(graph_for(
        &MapEntry::new("m"),
        (10.0, 10.0),
        &reach(),
        GraphOptions::default()
    )
    .is_none());
}

/// Limina 2-6's left stack: an up flash peaking at 26px from the y64 tier
/// clears y51 *and* y41 where they overlap, so it lands on y41 there.
#[test]
fn up_flash_targets_the_highest_platform_its_peak_clears() {
    let plats = [
        [31.0, 82.0, 140.0, 82.0],
        [31.0, 64.0, 66.0, 64.0],
        [32.0, 51.0, 71.0, 51.0],
        [31.0, 41.0, 55.0, 41.0],
    ];
    let m = reach_with(
        1.0,
        &[
            (
                Move::UpFlash,
                Reach {
                    dx: 6.0,
                    rise: 26.0,
                },
            ),
            (Move::RopeLift, Reach { dx: 3.0, rise: 0.0 }),
        ],
    );
    let g = graph(&plats, &m);
    let ups = transfers(&g, MoveKind::UpFlash);
    let to51: Vec<&Leg> = ups
        .iter()
        .filter(|l| l.y0 == 64.0 && l.y1 == 51.0)
        .collect();
    assert!(!to51.is_empty() && to51.iter().all(|l| l.x0 > 55.0));
    assert!(ups.iter().any(|l| l.y0 == 64.0 && l.y1 == 41.0));
    let legs = g.route((45.0, 64.0), (60.0, 51.0), &[]).unwrap();
    let up: Vec<&Leg> = legs
        .iter()
        .filter(|l| l.kind == MoveKind::UpFlash)
        .collect();
    assert_eq!(up.len(), 1);
    assert!(up[0].x0 > 55.0);
}

#[test]
fn walking_cost_scales_with_the_factor() {
    let plats = [[0.0, 100.0, 200.0, 100.0]];
    let cost = |factor: f64| {
        let opts = GraphOptions {
            walk_factor: factor,
            ..GraphOptions::default()
        };
        with_ropes(&plats, &[], opts).route_cost((10.0, 100.0), (150.0, 100.0), &[])
    };
    assert!(
        (cost(3.0) - 3.0 * cost(1.0)).abs() < 1e-6,
        "{} {}",
        cost(3.0),
        cost(1.0)
    );
}

#[test]
fn the_graph_cache_rebuilds_when_kit_or_walking_pace_change() {
    use picobot_core::navgraph::GraphCache;
    use std::sync::Arc;
    let mut e = MapEntry::new("m");
    e.platforms = Some(vec![[0.0, 0.667, 1.0, 0.667]]);
    let (wh, r) = ((200.0, 150.0), reach());
    let mut cache = GraphCache::default();
    let base = GraphOptions::default();
    let a = cache.get(Some(&e), Some(wh), &r, base).unwrap();
    let same = cache.get(Some(&e), Some(wh), &r, base).unwrap();
    assert!(Arc::ptr_eq(&a, &same));
    for changed in [
        GraphOptions {
            allow_double_flash: false,
            ..base
        },
        GraphOptions {
            walk_speed: 90.0,
            ..base
        },
        GraphOptions {
            walk_factor: 2.0,
            ..base
        },
    ] {
        let g = cache.get(Some(&e), Some(wh), &r, changed).unwrap();
        assert!(!Arc::ptr_eq(&a, &g), "{changed:?} must rebuild");
        cache.get(Some(&e), Some(wh), &r, base).unwrap();
    }
}

#[test]
fn a_rope_top_a_few_px_under_its_platform_is_lifted_onto_it() {
    use picobot_core::navgraph::lift_rope_top;
    let plats = [[80.0, 40.0, 120.0, 40.0]];
    assert_eq!(
        lift_rope_top(&plats, [100.0, 80.0, 100.0, 45.0]),
        [100.0, 80.0, 100.0, 40.0]
    );
    // Reversed ends, and a top already above the row, are left alone.
    assert_eq!(
        lift_rope_top(&plats, [100.0, 45.0, 100.0, 80.0]),
        [100.0, 40.0, 100.0, 80.0]
    );
    assert_eq!(
        lift_rope_top(&plats, [100.0, 80.0, 100.0, 37.0]),
        [100.0, 80.0, 100.0, 37.0]
    );
    // Too far under: another rope's top, not this platform's.
    assert_eq!(
        lift_rope_top(&plats, [100.0, 80.0, 100.0, 52.0]),
        [100.0, 80.0, 100.0, 52.0]
    );
}

#[test]
fn a_saved_rope_ending_under_its_platform_still_links() {
    let mut entry = MapEntry::new("m");
    entry.platforms = Some(vec![[0.0, 1.0, 1.0, 1.0], [0.4, 0.4, 0.6, 0.4]]);
    entry.ropes = Some(vec![[0.5, 0.8, 0.5, 0.45]]); // top 5px under the row
    let g = graph_for(&entry, (200.0, 100.0), &reach(), GraphOptions::default()).unwrap();
    assert!(!transfers(&g, MoveKind::ClimbUp).is_empty());
}

#[test]
fn a_down_jump_never_takes_off_on_a_rope_top() {
    let plats = [FLOOR, MID];
    let jumps = |ropes: &[[f64; 4]]| -> Vec<f64> {
        let mut xs: Vec<f64> = transfers(
            &with_ropes(&plats, ropes, GraphOptions::default()),
            MoveKind::DownJump,
        )
        .iter()
        .map(|l| l.x0)
        .collect();
        xs.sort_by(f64::total_cmp);
        xs
    };
    assert_eq!(jumps(&[]), [44.0, 80.0, 116.0]); // ends and middle of the overlap
                                                 // A rope from MID down to the floor, hanging at the middle column.
    let xs = jumps(&[[80.0, 81.0, 80.0, 95.0]]);
    assert_eq!(xs.len(), 3);
    assert!(xs.iter().all(|x| (x - 80.0).abs() > 8.0), "{xs:?}");
    // A rope that hangs off another platform doesn't move them.
    assert_eq!(jumps(&[[80.0, 40.0, 80.0, 79.0]]), [44.0, 80.0, 116.0]);
}

#[test]
fn a_carried_move_isnt_linked_across_a_platform_that_would_catch_it() {
    // Odium Road to the Castle's Gate 2: from the top-right ledge a flash
    // left would come down on the lower tier — but it passes over the
    // top-middle ledge, at the same height, and lands there first.
    let (p1, p2, p4) = (
        [106.0, 42.0, 134.0, 42.0],
        [138.0, 42.0, 173.0, 42.0],
        [75.0, 56.0, 122.0, 56.0],
    );
    let m = reach_with(
        1.0,
        &[(
            Move::Flash,
            Reach {
                dx: 37.0,
                rise: 4.0,
            },
        )],
    );
    let g = graph(&[p1, p2, p4], &m);
    assert!(transfers(&g, MoveKind::Flash)
        .iter()
        .all(|l| !(l.y0 == 42.0 && l.y1 == 56.0 && l.x0 >= 138.0)));
    // Without the ledge in the way it is linked.
    let g = graph(&[p2, p4], &m);
    assert!(transfers(&g, MoveKind::Flash)
        .iter()
        .any(|l| l.y0 == 42.0 && l.y1 == 56.0));
}
